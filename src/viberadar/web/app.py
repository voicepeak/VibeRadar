"""FastAPI 应用与公共路由装配（PRD 9.1/10.1）。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from viberadar import __version__
from viberadar.settings import PROJECT_ROOT, Settings, get_settings
from viberadar.store import get_engine, get_session_factory, init_db

from .routes_api import router as api_router
from .routes_public import router as public_router
from .templating import templates

logger = logging.getLogger("viberadar.web")


def web_dir() -> Path:
    return PROJECT_ROOT / "web"


def create_app(db_path: str | Path | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = get_engine(db_path)
    init_db(engine)
    session_factory = get_session_factory(db_path)

    app = FastAPI(
        title="Vibe Radar",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.templates = templates
    app.state.web_dir = web_dir()

    static_dir = web_dir() / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # noqa: ANN001
        request_id = f"req_{int(time.time() * 1000):x}"
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            logger.exception("未处理异常 request_id=%s", request_id)
            return JSONResponse(
                {
                    "code": "internal_error",
                    "message": "服务内部错误",
                    "request_id": request_id,
                    "retryable": True,
                },
                status_code=500,
            )
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """未知路径返回站点 404 页；JSON 前缀路径维持统一错误格式（PRD 9.1）。"""
        path = request.url.path
        wants_json = path.startswith(("/api/", "/health/", "/go/", "/rss.xml"))
        if not wants_json and exc.status_code == 404:
            from .templating import render

            return render(
                request,
                "not_found.html",
                {"settings": settings, "message": "你访问的页面不存在。", "status": 404},
                status_code=404,
            )
        return JSONResponse(
            {
                "code": "not_found" if exc.status_code == 404 else "http_error",
                "message": "资源不存在" if exc.status_code == 404 else "请求失败",
                "request_id": getattr(request.state, "request_id", ""),
                "retryable": False,
            },
            status_code=exc.status_code,
        )

    app.include_router(public_router)
    app.include_router(api_router)
    return app


app = None  # 由 `viberadar serve` 或 uvicorn 工厂创建


def get_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app
