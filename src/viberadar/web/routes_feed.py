from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from viberadar.feedback import limiter
from viberadar.pipeline.feed import execute_refresh, feed_view, reserve_refresh, run_view
from viberadar.store.models import RefreshRunRow
from .deps import client_ip, get_session, resolve_locale, same_origin
from .templating import templates

router = APIRouter()


def _start(request, tasks):
    if not same_origin(request):
        return JSONResponse({"code": "forbidden"}, status_code=403)
    if not limiter.allow("feed:" + client_ip(request), 6):
        return JSONResponse({"code": "rate_limited"}, status_code=429)
    factory = request.app.state.session_factory
    result = reserve_refresh(factory)
    if result.pop("created", False):
        tasks.add_task(execute_refresh, factory, request.app.state.settings, result["run_id"])
    return result


@router.post("/api/v1/refresh")
def refresh_api(request: Request, tasks: BackgroundTasks):
    result = _start(request, tasks)
    return result if isinstance(result, JSONResponse) else JSONResponse(result, status_code=202 if result["status"] == "running" else 200)


@router.post("/refresh")
def refresh_form(request: Request, tasks: BackgroundTasks):
    result = _start(request, tasks)
    if isinstance(result, JSONResponse):
        return result
    return RedirectResponse("/?refresh=" + result["status"], status_code=303)


@router.get("/api/v1/refresh/{run_id}")
def refresh_status(run_id: str, session: Session = Depends(get_session)):
    run = session.get(RefreshRunRow, run_id)
    return run_view(run) if run else JSONResponse({"code": "not_found"}, status_code=404)


@router.get("/api/v1/feed")
def get_feed(request: Request, cursor: str | None = None, section: str | None = None, session: Session = Depends(get_session)):
    locale = resolve_locale(request)
    settings = request.app.state.settings
    try:
        view = feed_view(session, settings, locale, cursor, section)
    except ValueError as exc:
        return JSONResponse({"code": "invalid_cursor", "message": str(exc)}, status_code=400)
    html = templates.env.get_template("_feed_section.html").render(
        feed=view, settings=settings, locale=locale, request=request)
    return JSONResponse(jsonable_encoder({**view, "html": html}), headers={"Cache-Control": "no-store"})
