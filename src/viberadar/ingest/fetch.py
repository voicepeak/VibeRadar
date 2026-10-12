"""公开 URL 安全抓取边界（PRD 10.5）。

公开输入与采集 URL 只允许 HTTP(S)；拒绝 localhost、私网、链路本地、
云 metadata 与非 Web 协议；验证每次重定向目标；限制超时、大小与重定向次数。
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

ALLOWED_SCHEMES = {"http", "https"}
DENY_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "kubernetes.default",
}
DENY_HOST_SUFFIXES = (".local", ".internal", ".localhost")


class UnsafeURLError(ValueError):
    pass


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_url_safety(url: str, resolve_dns: bool = False) -> str:
    """校验 URL；不合法抛 UnsafeURLError。返回规范化后的 URL。"""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"仅允许 HTTP(S)：{url}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeURLError(f"缺少主机名：{url}")
    if host in DENY_HOSTNAMES or host.endswith(DENY_HOST_SUFFIXES):
        raise UnsafeURLError(f"禁止访问的主机：{host}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and _is_private_ip(str(literal)):
        raise UnsafeURLError(f"禁止访问的 IP：{host}")
    if resolve_dns and literal is None:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise UnsafeURLError(f"DNS 解析失败：{host}") from exc
        for info in infos:
            ip = info[4][0]
            if _is_private_ip(ip):
                raise UnsafeURLError(f"DNS 解析到私网地址：{host} → {ip}")
    return url


@dataclass
class SafeResponse:
    status_code: int
    url: str
    headers: dict
    text: str | None
    content: bytes | None


class FetchTooLarge(RuntimeError):
    pass


def safe_get(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 20.0,
    max_bytes: int = 1_000_000,
    max_redirects: int = 5,
    resolve_dns: bool = False,
    headers: dict | None = None,
) -> SafeResponse:
    """带边界的手动重定向 GET。正文读取到 max_bytes 上限。"""
    current = validate_url_safety(url, resolve_dns=resolve_dns)
    hops = 0
    with httpx.Client(
        timeout=timeout, transport=transport, follow_redirects=False, headers=headers or {}
    ) as client:
        while True:
            response = client.get(current)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    return SafeResponse(
                        response.status_code, current, dict(response.headers), None, None
                    )
                hops += 1
                if hops > max_redirects:
                    raise UnsafeURLError("重定向次数超限")
                from urllib.parse import urljoin

                current = validate_url_safety(
                    urljoin(current, location), resolve_dns=resolve_dns
                )
                continue
            content = response.content
            if len(content) > max_bytes:
                raise FetchTooLarge(f"响应超过 {max_bytes} 字节上限")
            return SafeResponse(
                status_code=response.status_code,
                url=current,
                headers=dict(response.headers),
                text=response.text,
                content=content,
            )
