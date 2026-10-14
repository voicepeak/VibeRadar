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
from sqlalchemy import select

from viberadar.contracts import (
    AccessInfo,
    CardCopyEn,
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
from viberadar.i18n import (
    default_steps,
    fallback_one_liner,
    fallback_user_story,
    fallback_why,
    has_cjk,
    is_copy_pure,
)
from viberadar.llm import COMMON_SYSTEM, LLMGateway
from viberadar.settings import Settings, get_settings
from viberadar.store import repo_products
from viberadar.store.models import EvidenceRow, ProductRow, RawItemRow

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
        self._in_readme = False
        self.readme_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        attrs_dict = dict(attrs)
        if tag == "article" and "markdown-body" in attrs_dict.get("class", "").split():
            self._in_readme = True
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
        if tag == "article":
            self._in_readme = False
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
        if self._in_readme:
            self.readme_chunks.append(text)
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

    @property
    def chunks(self) -> list[str]:
        return list(self.text_parts)


@dataclass
class PageContent:
    url: str
    final_url: str
    status: int
    reachable: bool
    title: str | None = None
    description: str | None = None
    text: str = ""
    chunks: list[str] = field(default_factory=list)
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
    chunks = parser.chunks
    # Repository navigation describes GitHub's services, not the discovered tool.
    if github_owner_repo(response.url) is not None:
        chunks = parser.readme_chunks
        text = re.sub(r"\s+", " ", " ".join(chunks)).strip()
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
        chunks=chunks,
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
        if any((bool(re.search(r"(?<![a-z0-9])" + re.escape(m) + r"(?![a-z0-9])", combined))
                if m[0].isalnum() else m in combined) for m in markers):
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


_HARD_BOUNDARY = "。！？；"
_SOFT_BOUNDARY = "，、,;"


def clip_summary(text: str, limit: int = 40) -> str:
    """按句读边界压缩到 limit 字以内，避免半句/半词截断。

    优先在硬边界（句号/问号/叹号/分号）收尾，其次软边界（逗号/顿号），
    英文退到空白边界防止截断单词；截断一律以省略号收尾且不会超出 limit。
    """
    flat = re.sub(r"\s+", " ", (text or "")).strip()
    if not flat:
        return ""
    if len(flat) <= limit:
        return flat

    window = flat[: limit - 1]
    min_use = max(1, limit // 2)
    hard = -1
    soft = -1
    for i in range(len(window) - 1, -1, -1):
        ch = window[i]
        if ch in _SOFT_BOUNDARY and soft == -1:
            soft = i + 1
        if ch in _HARD_BOUNDARY:
            hard = i + 1
            break
        if ch in ".!?":
            # 英文句点需后跟空格才算句界，避免把 3.5 / v1.2 误判为句尾
            if i + 1 < len(flat) and flat[i + 1] == " ":
                hard = i + 1
                break
    boundary = hard if hard > 0 else soft
    if boundary >= min_use:
        return window[:boundary].rstrip(" ,;:，、；：") + "…"
    space = window.rfind(" ")
    if space >= min_use:
        return window[:space].rstrip(" ,;:") + "…"
    return window.rstrip() + "…"


_COMMAND_PATTERNS = [
    re.compile(r"git clone\s+https?://\S+"),
    re.compile(r"curl\s+-\S*\s*\S+\s*\|\s*(?:sudo\s+)?(?:sh|bash)"),
    re.compile(r"docker\s+compose\s+up\b[^|]{0,48}"),
    re.compile(r"docker\s+run\b[^|]{0,72}"),
    re.compile(r"(?:npm|pnpm|yarn|bun)\s+(?:install|i|create|add|run|dev|start)\b[^|]{0,48}"),
    re.compile(r"(?:pip3?|uv)\s+(?:install|sync|run|add|pip\s+install)\b[^|]{0,48}"),
    re.compile(r"cargo\s+(?:install|build|run)\b[^|]{0,48}"),
    re.compile(r"go\s+(?:install|build|run)\s+\S+"),
    re.compile(r"npx\s+[^\s|]{2,60}"),
    re.compile(r"brew\s+install\s+[^\s|]{2,48}"),
    re.compile(r"make\s+(?:install|run|dev|serve)\b"),
]
_COMMAND_MAX = 80


def extract_install_command(chunks: list[str]) -> str | None:
    """从页面文本块中提取一条可直接执行的安装/运行命令（按出现顺序取首个）。

    仅接受简洁的单行命令形态；长散文句中被提及的命令词不视为命令，
    避免把“你可以用 npm install…”整句误当步骤。
    """
    for chunk in chunks:
        flat = re.sub(r"\s+", " ", chunk).strip()
        if not flat or len(flat) > 240:
            continue
        for pattern in _COMMAND_PATTERNS:
            match = pattern.search(flat)
            if not match:
                continue
            command = re.sub(r"\s+", " ", match.group(0)).strip()
            command = command.rstrip("`'\"”’。，,.;:)")
            if len(command) > _COMMAND_MAX:
                command = command[:_COMMAND_MAX].rstrip(" ,;:-")
            if len(command) >= 4:
                return command
    return None


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
    digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    existing = session.scalar(select(EvidenceRow).where(
        EvidenceRow.source_url == url, EvidenceRow.content_hash == digest))
    if existing:
        return Evidence(id=existing.id, raw_item_id=existing.raw_item_id,
            source_url=existing.source_url, excerpt=existing.excerpt, content_hash=existing.content_hash,
            retrieved_at=existing.retrieved_at, evidence_kind=existing.evidence_kind)
    evidence = Evidence(
        id=new_id("evd"),
        raw_item_id=raw.id,
        source_url=url,
        excerpt=excerpt,
        content_hash=digest,
        retrieved_at=utcnow(),
        evidence_kind=kind,
    )
    repo_products.add_evidence(session, evidence)
    return evidence


def _category_label(product: ProductRow, settings: Settings | None) -> str:
    settings = settings or get_settings()
    category = product.category or ""
    return settings.taxonomy.labels("zh").get(category, category) or "AI 产品"


def _template_copy(
    product: ProductRow,
    raw: RawItemRow,
    mode: TryMode,
    entry: str,
    label: str,
    page: PageContent | None = None,
) -> tuple[str, str, str, list[str], CardCopyEn]:
    """生成中英两套模板文案；除产品名/URL 外不混排语言。"""
    base = re.sub(r"\s+", " ", (raw.description or "").strip()) or (
        product.summary or product.name
    )
    base_is_zh = has_cjk(base)

    if base_is_zh:
        zh_one = clip_summary(base, 40) or fallback_one_liner("zh", label)
        zh_why = clip_summary(base, 160) or fallback_why("zh", label)
        zh_story = fallback_user_story("zh", mode.value, label)
    else:
        zh_one = fallback_one_liner("zh", label)
        zh_why = fallback_why("zh", label)
        zh_story = fallback_user_story("zh", mode.value, label)

    if base and not base_is_zh:
        en_one = clip_summary(base, 80)
        en_why = clip_summary(base, 160)
        en_story = fallback_user_story("en", mode.value, label)
    else:
        en_one = fallback_one_liner("en", label)
        en_why = fallback_why("en", label)
        en_story = fallback_user_story("en", mode.value, label)

    command = extract_install_command(page.chunks if page is not None else [])
    self_description = (
        clip_summary(page.description, 50)
        if page is not None and page.description
        else None
    )
    zh_steps = default_steps(
        mode.value,
        "zh",
        entry or None,
        command=command,
        self_description=(
            self_description
            if self_description and has_cjk(self_description)
            else None
        ),
    )
    en_steps = default_steps(
        mode.value,
        "en",
        entry or None,
        command=command,
        self_description=(
            self_description
            if self_description and not has_cjk(self_description)
            else None
        ),
    )
    en = CardCopyEn(
        one_liner=en_one or product.name[:80],
        why_interesting=en_why,
        user_story=en_story,
        steps=en_steps[:4],
    )
    return zh_one, zh_why, zh_story, zh_steps, en


def template_enrichment(
    product: ProductRow,
    raw: RawItemRow,
    access: AccessInfo,
    mode: TryMode,
    evidence_ids: list[str],
    settings: Settings | None = None,
    page: PageContent | None = None,
) -> Enrichment:
    """确定性模板：只使用已抓取事实；中英双语，无依据的字段保持 unknown/未验证。"""
    label = _category_label(product, settings)
    entry = raw.target_url or raw.source_url or ""
    zh_one, zh_why, zh_story, zh_steps, en = _template_copy(
        product, raw, mode, entry, label, page
    )

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
        one_liner=zh_one[:40] or product.name[:40],
        why_interesting=zh_why,
        user_story=zh_story,
        en=en,
        mode=mode,
        steps=[TryStep(text=text) for text in zh_steps[:4]],
        access=access,
        claims=claims,
        verification_status=verification,
        verified_by=None,
        verified_at=None,
        time_promise=None,
        prompt_version=None,
        model_version=None,
    )


def ensure_bilingual(candidate: Enrichment, fallback: Enrichment) -> Enrichment:
    """补齐模型输出中缺失或不纯净的字段，保证两种语言模式都不混排。"""
    updates: dict = {}
    if not is_copy_pure(candidate.one_liner, "zh"):
        updates["one_liner"] = fallback.one_liner
    if not is_copy_pure(candidate.why_interesting, "zh"):
        updates["why_interesting"] = fallback.why_interesting
    if not is_copy_pure(candidate.user_story, "zh"):
        updates["user_story"] = fallback.user_story
    if not (
        candidate.steps and all(is_copy_pure(step.text, "zh") for step in candidate.steps)
    ):
        updates["steps"] = fallback.steps

    en = candidate.en
    if en is None:
        updates["en"] = fallback.en
    else:
        en_updates: dict = {}
        if not is_copy_pure(en.one_liner, "en"):
            en_updates["one_liner"] = fallback.en.one_liner if fallback.en else en.one_liner
        if not is_copy_pure(en.why_interesting, "en"):
            en_updates["why_interesting"] = (
                fallback.en.why_interesting if fallback.en else en.why_interesting
            )
        if not is_copy_pure(en.user_story, "en"):
            en_updates["user_story"] = (
                fallback.en.user_story if fallback.en else en.user_story
            )
        if not (en.steps and all(is_copy_pure(step, "en") for step in en.steps)):
            en_updates["steps"] = fallback.en.steps if fallback.en else en.steps
        if en_updates:
            updates["en"] = en.model_copy(update=en_updates)
    return candidate.model_copy(update=updates) if updates else candidate


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
    enrichment = template_enrichment(product, raw, access, mode, evidence_ids, settings, page=page)

    if page is not None and not page.reachable:
        enrichment = enrichment.model_copy(
            update={"verification_status": VerificationStatus.FAILED}
        )

    if use_strong_model and llm is not None:
        resolved_model = llm.model_for("strong")
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
            model="strong",
            stage="enrich",
            max_output_tokens=2400,
            mock_response=mock_guide_response,
        )
        if response.ok and response.data:
            try:
                candidate = Enrichment(**response.data)
                errors = candidate.claim_evidence_errors(set(evidence_ids))
                if not errors:
                    # 不得把未验证状态升级；缺语言/语言不纯的字段用模板补齐
                    candidate = ensure_bilingual(candidate, enrichment)
                    enrichment = candidate.model_copy(
                        update={
                            "verification_status": enrichment.verification_status,
                            "time_promise": None,
                            "prompt_version": "guide-v2",
                            "model_version": resolved_model,
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
