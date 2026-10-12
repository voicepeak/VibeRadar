"""URL 归一化与身份提取（PRD M2）。

规则：
- 去除已知跟踪参数，保留身份相关路径与 query；路径大小写遵从站点语义。
- HTTPS、www 与重定向仅在已验证同一目标时归并（不在本模块自动合并）。
- GitHub 以 repo 稳定 ID 或 owner/repo 识别；子路径映射到同一 repo。
- 共享平台（github.com、*.vercel.app、*.notion.site 等）不能仅凭域名合并。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PREFIXES = ("utm_",)
TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref_src",
    "ref_url",
    "spm",
}
SHARED_PLATFORM_DOMAINS = {
    "github.com",
    "gitlab.com",
    "vercel.app",
    "notion.site",
    "netlify.app",
    "pages.dev",
    "substack.com",
    "medium.com",
}
GITHUB_REPO_RE = re.compile(r"^/([^/]+)/([^/]+?)(?:\.git)?(?:/|$)")


@dataclass
class IdentityHints:
    canonical_key: str | None = None
    identity_kind: str = "unknown"          # github_repo / url
    repo_full_name: str | None = None
    repo_id: str | None = None              # 来自源 metadata 的稳定 ID
    name_guess: str = ""
    host: str | None = None
    root_domain: str | None = None
    shared_platform: bool = False
    subpath: str | None = None
    warnings: list[str] = field(default_factory=list)


def strip_tracking(url: str) -> str:
    parsed = urlparse(url)
    pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not (k.lower().startswith(TRACKING_PREFIXES) or k.lower() in TRACKING_PARAMS)
    ]
    pairs.sort()
    query = urlencode(pairs)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            query,
            "",  # 身份不包含 fragment
        )
    )


def root_domain(host: str) -> str:
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


def github_owner_repo(url: str) -> tuple[str, str, str | None] | None:
    """返回 (owner, repo, subpath) 或 None。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "www.github.com"):
        return None
    match = GITHUB_REPO_RE.match(parsed.path)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if owner.lower() in ("orgs", "topics", "collections", "sponsors", "settings"):
        return None
    subpath = parsed.path[match.end() - 1 :] if match.end() <= len(parsed.path) else None
    return owner, repo, (subpath or None)


def extract_identity(
    url: str | None,
    title: str,
    metadata: dict | None = None,
    source_key: str | None = None,
) -> IdentityHints:
    metadata = metadata or {}
    hints = IdentityHints(name_guess=title.strip())
    repo_id = metadata.get("repo_id")
    if repo_id is not None:
        hints.repo_id = str(repo_id)

    if not url:
        hints.warnings.append("缺少入口 URL，只能产生待复核关系")
        return hints

    normalized = strip_tracking(url)
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower()
    hints.host = host
    hints.root_domain = root_domain(host)
    hints.shared_platform = hints.root_domain in SHARED_PLATFORM_DOMAINS

    gh = github_owner_repo(normalized)
    if gh is not None:
        owner, repo, subpath = gh
        hints.identity_kind = "github_repo"
        hints.repo_full_name = f"{owner}/{repo}"
        hints.canonical_key = f"github:{owner.lower()}/{repo.lower()}"
        hints.subpath = subpath
        if subpath:
            hints.warnings.append("GitHub 子路径映射到同一 repo（子路径规则）")
        return hints

    hints.identity_kind = "url"
    hints.canonical_key = f"url:{normalized}"
    if hints.shared_platform:
        # 共享平台：只保留 host+path 作为待复核信号，不单独作为强主键来源
        hints.warnings.append(f"共享平台域名 {host}：禁止仅凭域名合并")
    return hints
