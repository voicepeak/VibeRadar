"""依赖注入：数据库会话与匿名 actor。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from fastapi import Request, Response
from sqlalchemy.orm import Session

from viberadar.feedback import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    derive_actor_hash,
    new_anonymous_id,
)


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass
class Actor:
    actor_hash: str | None
    identity_available: bool
    anonymous_id: str | None


def resolve_actor(request: Request, settings, *, create: bool = False) -> Actor:
    aid = request.cookies.get(COOKIE_NAME)
    if not aid and create:
        aid = new_anonymous_id()
    if not aid:
        return Actor(actor_hash=None, identity_available=False, anonymous_id=None)
    return Actor(
        actor_hash=derive_actor_hash(settings.secret_key, aid),
        identity_available=True,
        anonymous_id=aid,
    )


def ensure_cookie(request: Request, response: Response, settings, actor: Actor) -> None:
    if actor.anonymous_id and request.cookies.get(COOKIE_NAME) != actor.anonymous_id:
        response.set_cookie(
            COOKIE_NAME,
            actor.anonymous_id,
            max_age=COOKIE_MAX_AGE,
            httponly=False,
            samesite="lax",
            secure=False,
        )


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def same_origin(request: Request) -> bool:
    """CSRF/同源校验：Origin 或 Referer 必须与请求 Host 同源。"""
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if origin:
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        return parsed.netloc == host
    referer = request.headers.get("referer")
    if referer:
        from urllib.parse import urlparse

        parsed = urlparse(referer)
        return parsed.netloc == host
    return False
