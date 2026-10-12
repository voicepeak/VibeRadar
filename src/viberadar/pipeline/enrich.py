"""富化与体验指南（PRD M4/4.4/4.5）。

- 抓取公开产品页/README，保留来源 URL、摘录、抓取时间、内容 hash 和证据 ID。
- 404、登录墙或超时不能伪造成功；有可访问公开演示改 demo，仅仓库改 setup。
- 每条关键 claim 必须引用已存在的摘录；程序检查引用完整性。
- 付费、注册、候补、API key 不因生成文案而自动被“确认”。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from viberadar.contracts import (
    AccessInfo,
    Claim,
    Enrichment,
    Evidence,
    MobileSupport,
    Pricing,
    TryMode,
    TryStep,
    VerificationStatus,
    new_id,
    utcnow,
)
from viberadar.llm import COMMON_SYSTEM, LLMGateway
from viberadar.settings import Settings, get_settings
from viberadar.store import repo_products
from viberadar.store.models import ProductRow, RawItemRow

from .normalize import github_owner_repo

EXCERPT_LIMIT = 200

LOGIN_MARKERS = ["sign in", "log in", "login", "sign up", "登录", "注册"]
API_KEY_MARKERS = ["api key", "api_key", "apikey", "access token"]
WAITLIST_MARKERS = ["waitlist", "wait list", "候补", "排队", "join the wait"]
SELF_HOST_MARKERS = ["self-host", "selfhost", "docker run", "docker compose", "npm install", "pip install", "git clone", "local install"]
PAID_MARKERS = ["$", "€", "¥", "pricing", "per month", "per seat", "/mo", "billed"]
FREE_MARKERS = ["free", "open source", "open-source", "免费", "开源", "mit license", "apache-2.0"]
PLATFORM_MARKERS = {
    "Windows": ["windows", ".exe", "win32"],
    "macOS": ["macos", "mac os", "dmg", "osx"],
    "Linux": ["linux", "ubuntu", "debian", "appimage"],
    "iOS": ["ios", "app store", "iphone"],
    "Android": ["android", "play store", "apk"],
    "Web": ["browser", "web app", "网页"],
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.text_parts: list[str] = []
        self._in_title = False
        self._in_script = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        attrs_dict = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag in ("script", "style"):
            self._in_script = True
        elif tag == "meta":
            key = attrs_dict.get("property") or attrs_dict.get("name") or ""
            content = attrs_dict.get("content") or ""
            if key and content and key.lower() in (
                "description",
                "og:description",
                "og:title",
                "og:site_name",
            ):
                self.meta[key.lower()] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style"):
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        else:
            self.text_parts.append(text)

    @property
    def title(self) -> str | None:
        value = " ".join(self.title_parts).strip()
        return value or None

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.text_parts)).strip()


@dataclass
class PageContent:
    url: str
    final_url: str
    status: int
    reachable: bool
    title: str | None = None
    description: str | None = None
    text: str = ""
    error: str | None = None
    login_wall: bool = False

    def haystack(self) -> str:
        return f"{self.title or ''} {self.description or ''} {self.text}".lower()


@dataclass
class EnrichOutcome:
    product_id: str
    enrichment: Enrichment | None
    evidence_ids: list[str] = field(default_factory=list)
    page: PageContent | None = None
    error: str | None = None


def fetch_page(url: str, transport=None, resolve_dns: bool = False, timeout: float = 20.0) -> PageContent:
    from viberadar.ingest.fetch import safe_get

    try:
        response = safe_get(
            url, transport=transport, resolve_dns=resolve_dns, timeout=timeout, max_bytes=800_000
        )
    except Exception as exc:  # noqa: BLE001
        return PageContent(
            url=url, final_url=url, status=0, reachable=False, error=f"{type(exc).__name__}: {exc}"
        )
    if response.status_code >= 400:
        return PageContent(
            url=url,
            final_url=response.url,
            status=response.status_code,
            reachable=False,
            error=f"HTTP {response.status_code}",
        )
    parser = _TextExtractor()
    try:
        parser.feed(response.text or "")
    except Exception:  # noqa: BLE001 —— HTML 残缺不视为失败
        pass
    text = parser.text
    haystack = f"{parser.title or ''} {text}".lower()
    login_wall = any(m in haystack for m in ("sign in to continue", "log in to continue", "请登录"))
    return PageContent(
        url=url,
        final_url=response.url,
        status=response.status_code,
        reachable=True,
        title=parser.title,
        description=parser.meta.get("description") or parser.meta.get("og:description"),
        text=text[:20_000],
        login_wall=login_wall,
    )


def infer_access(page: PageContent | None, raw: RawItemRow) -> AccessInfo:
    now = utcnow()
    haystack = (page.haystack() if page and page.reachable else "") or ""
    description = (raw.description or "").lower()
    combined = f"{haystack} {description}"

    has_free = any(m in combined for m in FREE_MARKERS)
    has_paid = any(m in combined for m in PAID_MARKERS)
    if has_free and has_paid:
        pricing = Pricing.FREEMIUM
    elif has_paid:
        pricing = Pricing.PAID
    elif has_free:
        pricing = Pricing.FREE
    else:
        pricing = Pricing.UNKNOWN

    login_required: bool | None = None
    if page is not None and page.reachable:
        login_required = any(m in haystack for m in LOGIN_MARKERS)
    elif description:
        login_required = None

    api_key_required: bool | None = None
    if page is not None and page.reachable:
        api_key_required = any(m in haystack for m in API_KEY_MARKERS)
    elif description:
        api_key_required = None

    waitlist: bool | None = None
    if page is not None and page.reachable:
        waitlist = any(m in haystack for m in WAITLIST_MARKERS)
    elif description:
        waitlist = None

    self_host: bool | None = None
    if page is not None and page.reachable:
        self_host = any(m in haystack for m in SELF_HOST_MARKERS)
    elif description:
        self_host = None

    platforms = []
    for platform, markers in PLATFORM_MARKERS.items():
        if any(m in combined for m in markers):
            platforms.append(platform)

    # Web 入口存在不自动等于手机支持（PRD 4.4）；无实测记录时保持 unknown
    mobile = MobileSupport.UNKNOWN

    return AccessInfo(
        pricing=pricing,
        mobile_support=mobile,
        login_required=login_required,
        api_key_required=api_key_required,
        waitlist=waitlist,
        self_host_required=self_host,
        platforms=platforms,
        setup_minutes=None,
        checked_at=now,
    )


def decide_mode(raw: RawItemRow, page: PageContent | None) -> TryMode:
    """未经人工/探针验证不标 direct（PRD 4.5）。"""
    repo = github_owner_repo(raw.target_url or raw.source_url or "")
    is_repo_entry = repo is not None and (raw.target_url or "").startswith("https://github.com")
    if is_repo_entry:
        return TryMode.SETUP
    if page is not None and page.reachable and not page.login_wall:
        return TryMode.DEMO
    if is_repo_entry or repo is not None:
        return TryMode.SETUP
    return TryMode.SETUP if "github.com" in (raw.target_url or raw.source_url or "") else TryMode.DEMO


def _excerpt(text: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat[:EXCERPT_LIMIT]


def create_evidence(
    session: Session,
    raw: RawItemRow,
    url: str,
    text: str,
    kind: str,
) -> Evidence | None:
    excerpt = _excerpt(text)
    if not excerpt:
        return None
    evidence = Evidence(
        id=new_id("evd"),
        raw_item_id=raw.id,
        source_url=url,
        excerpt=excerpt,
        content_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        retrieved_at=utcnow(),
        evidence_kind=kind,
    )
    repo_products.add_evidence(session, evidence)
    return evidence


def template_enrichment(
    product: ProductRow,
    raw: RawItemRow,
    access: AccessInfo,
    mode: TryMode,
    evidence_ids: list[str],
) -> Enrichment:
    """确定性模板：只使用已抓取事实；无依据的字段保持 unknown/未验证。"""
    base_text = (raw.description or "").strip() or (product.summary or product.name)
    one_liner = base_text[:40]
    why = base_text[:160] if base_text else f"{product.name} 的新发布，等待补充证据"
    entry = raw.target_url or raw.source_url or ""

    if mode == TryMode.SETUP:
        steps = [
            TryStep(text=f"打开仓库或官网：{entry}"),
            TryStep(text="阅读 README 的安装与运行说明"),
            TryStep(text="按文档完成本地安装后启动"),
            TryStep(text="准备时间未验证，安装前确认平台与依赖"),
        ]
    elif mode == TryMode.DEMO:
        steps = [
            TryStep(text=f"打开项目页面：{entry}"),
            TryStep(text="查看公开演示或截图中呈现的核心效果"),
            TryStep(text="对照页面说明判断是否符合自己的使用场景"),
            TryStep(text="若需安装或付费，先确认门槛再决定"),
        ]
    else:  # direct（需人工确认；模板不主动使用）
        steps = [
            TryStep(text=f"打开入口：{entry}"),
            TryStep(text="按页面提示开始核心操作"),
            TryStep(text="观察页面反馈是否符合描述"),
        ]

    claims: list[Claim] = []
    if access.pricing != Pricing.UNKNOWN and evidence_ids:
        claims.append(
            Claim(text=f"费用：{access.display_text()}", evidence_ids=list(evidence_ids[:1]))
        )
    if access.login_required is not None and evidence_ids:
        claims.append(
            Claim(
                text=f"登录要求：{'需登录' if access.login_required else '无需登录'}",
                evidence_ids=list(evidence_ids[:1]),
            )
        )

    verification = (
        VerificationStatus.DOC_CHECKED
        if evidence_ids
        else VerificationStatus.UNVERIFIED
    )
    return Enrichment(
        one_liner=one_liner or product.name[:40],
        why_interesting=why,
        mode=mode,
        steps=steps,
        access=access,
        claims=claims,
        verification_status=verification,
        verified_by=None,
        verified_at=None,
        time_promise=None,
        prompt_version=None,
        model_version=None,
    )


def enrich_product(
    session: Session,
    product: ProductRow,
    raw: RawItemRow,
    settings: Settings | None = None,
    transport=None,
    llm: LLMGateway | None = None,
    use_strong_model: bool = False,
    mock_guide_response: dict | None = None,
) -> EnrichOutcome:
    settings = settings or get_settings()
    target = raw.target_url or raw.source_url
    page = fetch_page(target, transport=transport) if target else None

    evidence_ids: list[str] = []
    # 源描述摘录
    if raw.description:
        ev = create_evidence(session, raw, raw.source_url, raw.description, "excerpt")
        if ev:
            evidence_ids.append(ev.id)
    if page is not None and page.reachable:
        if page.description:
            ev = create_evidence(session, raw, page.final_url, page.description, "landing")
            if ev:
                evidence_ids.append(ev.id)
        if page.text:
            ev = create_evidence(session, raw, page.final_url, page.text[:400], "landing")
            if ev:
                evidence_ids.append(ev.id)

    if page is not None and not page.reachable and not raw.description:
        return EnrichOutcome(
            product_id=product.id,
            enrichment=None,
            error=f"入口不可达：{page.error}（不伪造成功，退出公开候选）",
            page=page,
        )

    access = infer_access(page, raw)
    mode = decide_mode(raw, page)
    enrichment = template_enrichment(product, raw, access, mode, evidence_ids)

    if page is not None and not page.reachable:
        enrichment = enrichment.model_copy(
            update={"verification_status": VerificationStatus.FAILED}
        )

    if use_strong_model and llm is not None:
        response = llm.chat_json(
            prompt_name="guide",
            system=COMMON_SYSTEM,
            payload={
                "product": {"id": product.id, "name": product.name},
                "event": {"id": raw.id, "title": raw.title},
                "access_evidence": access.model_dump(mode="json"),
                "evidence": [
                    {"id": ev.id, "excerpt": ev.excerpt}
                    for ev in repo_products.list_evidence(session, evidence_ids)
                ],
            },
            model="deepseek-pro",
            stage="enrich",
            max_output_tokens=1500,
            mock_response=mock_guide_response,
        )
        if response.ok and response.data:
            try:
                candidate = Enrichment(**response.data)
                errors = candidate.claim_evidence_errors(set(evidence_ids))
                if not errors:
                    # 不得把未验证状态升级
                    enrichment = candidate.model_copy(
                        update={
                            "verification_status": enrichment.verification_status,
                            "time_promise": None,
                            "prompt_version": "guide-v1",
                            "model_version": "deepseek-pro",
                        }
                    )
                else:
                    return EnrichOutcome(
                        product_id=product.id,
                        enrichment=enrichment,
                        evidence_ids=evidence_ids,
                        page=page,
                        error=f"指南引用完整性错误，使用模板: {errors[:2]}",
                    )
            except Exception as exc:  # noqa: BLE001
                return EnrichOutcome(
                    product_id=product.id,
                    enrichment=enrichment,
                    evidence_ids=evidence_ids,
                    page=page,
                    error=f"指南 schema 失败，使用模板: {exc}",
                )

    repo_products.update_product_summary(
        session, product.id, enrichment.one_liner, product.category, utcnow()
    )
    return EnrichOutcome(
        product_id=product.id,
        enrichment=enrichment,
        evidence_ids=evidence_ids,
        page=page,
        error=None,
    )
