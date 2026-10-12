"""多语言（中文 / English）工具：界面词典、标签与卡片文案本地化。

规则（与产品决策一致）：
- 中文模式除品牌/产品名、平台名与常见技术词（AI、API 密钥、GitHub）外不出现英文句子；
- 英文模式不出现中文（产品名与品牌名除外）；
- 缺少对应语言文案或文案语言不匹配时，用基于已核查事实的确定性兜底文案，不逐字翻译、
  不伪造不存在的能力与承诺。
"""

from __future__ import annotations

import re

SUPPORTED_LOCALES = ("zh", "en")
DEFAULT_LOCALE = "zh"

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

MESSAGES: dict[str, dict[str, str]] = {
    "zh": {
        "meta.title": "振感雷达 · 每日 AI builder 新品精选",
        "meta.description": "每天早上 9 点，给你一份新近发现的 AI 编程产品精选。",
        "brand.name": "振感雷达",
        "brand.sub": "Vibe Radar",
        "nav.today": "今日",
        "nav.saved": "收藏",
        "nav.rss": "订阅",
        "nav.settings": "设置",
        "lang.switch": "语言",
        "footer.line1": "中文 AI builder 每日新品策展 · 无账号、匿名反馈 · 收藏仅保存在本浏览器",
        "footer.line2": "“玩过了”为读者自报，不代表第三方站内操作已完成。",
        "home.hero.title": "振感雷达（Vibe Radar）",
        "home.hero.desc": "每天早上 9 点，给你一份新近发现的 AI 编程产品精选：它是什么、为什么值得看、怎样最快体验、要付出什么成本。",
        "home.hero.first": "首个公开期号尚未发布。发布后这里会显示最新一期。",
        "home.today": "今日精选",
        "home.latest": "最近一期",
        "home.count": "{count} 条",
        "home.short": "短刊 {count} 条",
        "home.empty_badge": "暂无推荐",
        "home.not_today": "今日尚未发布，以下为最近一期（{date}），请以日期为准。",
        "home.permalink": "本期永久链接：",
        "home.highlights": "重点推荐",
        "home.empty.p1": "本期没有通过质量门槛的条目，未用旧产品冒充今日新品。",
        "home.empty.p2": "可以看看下面的最近期号，或等待下一期。",
        "edition.empty.p1": "本期没有通过质量门槛的条目，未用旧产品冒充今日新品。",
        "edition.empty.p2": "可以查看下面的往期回顾。",
        "edition.empty.p3": "暂时没有可回顾的往期，下一次通过核验的推荐发布后会出现在这里。",
        "edition.snapshot": "本页为永久快照；内容修订以最新版本为准。",
        "intro.normal": "今日精选 {count} 条。",
        "intro.short": "今日通过核验的推荐较少，精选 {count} 条。",
        "intro.empty": "今日暂无通过核验的推荐，已保留历史期号入口。",
        "section.tool": "工具与工作流",
        "section.creative": "创意与实验",
        "recent.latest": "最近期号",
        "recent.past": "往期回顾",
        "recent.count": "精选 {count} 条",
        "recent.short": "短刊 {count} 条",
        "recent.empty": "暂无推荐",
        "recent.degraded": "部分信源缺失",
        "card.highlight": "重点推荐",
        "card.user_story": "用户故事：",
        "card.why": "为什么值得看：",
        "card.try": "查看项目 / 试玩",
        "card.tried": "玩过了",
        "card.save": "收藏",
        "card.saved": "已收藏",
        "card.feedback": "评价与报错",
        "card.checked": "核查：{date}",
        "evidence.human_tested": "已实测",
        "evidence.doc_checked": "文档已核查",
        "evidence.probe_tested": "探针已核查",
        "evidence.failed": "入口未核查通过",
        "evidence.unverified": "证据待补充",
        "mobile.unknown": "手机体验未确认",
        "mobile.desktop_required": "需桌面设备",
        "source.origin": "来源",
        "source.project": "项目/官网",
        "mode.direct": "直接试玩",
        "mode.demo": "看演示",
        "mode.setup": "安装体验",
        "product_status.active": "有效",
        "product_status.merged": "已合并",
        "product_status.blocked": "已阻止",
        "product_status.retracted": "已撤回",
        "product.entry": "产品/仓库入口",
        "product.first_seen": "首次发现：{date}",
        "product.history": "历史期号",
        "product.history_note": "历史期号保存当期快照；产品当前简介变化不会改写当时的卡片文本。",
        "product.history_none": "尚未被公开期号收录。",
        "product.history_position": "第 {position} 条",
        "product.retracted": "该产品已撤回：{note} 不再承诺可正常试玩。",
        "product.retracted_default": "入口或信息不可用。",
        "saved.title": "收藏",
        "saved.desc": "收藏仅保存在本浏览器的 localStorage，不会上传完整收藏夹，也不跨设备同步。清除缓存或更换设备后无法找回。",
        "saved.empty_prefix": "暂无收藏。回到",
        "saved.empty_link": "今日精选",
        "saved.empty_suffix": "添加想体验的产品。",
        "saved.from_edition": "收藏自 {date} 期",
        "saved.item": "收藏条目",
        "saved.feedback": "反馈",
        "saved.remove": "取消收藏",
        "feedback.title": "告诉主理人你的体验",
        "feedback.desc": "无需注册。飞书里的链接打开本页后由你确认操作，链接预览机器人不会产生反馈。",
        "feedback.tried": "玩过了",
        "feedback.interest": "有趣",
        "feedback.boring": "无聊",
        "feedback.try_failed": "没玩起来",
        "feedback.clear": "撤回评价",
        "feedback.fail_label": "没玩起来的原因（可选）：",
        "feedback.fail_none": "不填写",
        "feedback.fail_submit": "提交失败原因",
        "feedback.reason.entry_dead": "入口失效",
        "feedback.reason.login_required": "需登录",
        "feedback.reason.paid": "需要付费",
        "feedback.reason.missing_api_key": "缺 API 密钥",
        "feedback.reason.platform_unsupported": "平台不支持",
        "feedback.reason.steps_mismatch": "步骤与说明不符",
        "feedback.reason.other": "其他",
        "feedback.report_title": "报告内容错误",
        "feedback.issue_type": "问题类型",
        "feedback.issue.pricing_wrong": "费用不符",
        "feedback.issue.link_dead": "入口失效",
        "feedback.issue.platform_wrong": "平台不符",
        "feedback.issue.steps_wrong": "步骤错误",
        "feedback.issue.other": "其他",
        "feedback.note_label": "一句话说明（可选）",
        "feedback.note_placeholder": "例如：官网实测为收费",
        "feedback.submit_issue": "提交报错",
        "feedback.issue_ok": "已收到报错，内容问题当日进入复核。",
        "feedback.issue_failed": "提交失败，请稍后重试。",
        "feedback.save_failed": "保存失败，请重试",
        "feedback.selection_kept": "（你的选择未丢失，可再次点击提交）",
        "feedback.state.tried": "已记录：玩过了",
        "feedback.state.interest": "已记录：有趣",
        "feedback.state.boring": "已记录：无聊",
        "feedback.state.try_failed": "已记录：没玩起来",
        "feedback.state.reason": "原因已记录",
        "feedback.state.saved": "已保存",
        "not_found.title": "未找到",
        "not_found.page": "你访问的页面不存在。",
        "not_found.default": "你访问的内容不存在。",
        "not_found.date": "日期不存在",
        "not_found.edition": "该日期没有已公开的期号",
        "not_found.product": "产品不存在",
        "not_found.item": "条目不存在",
        "not_found.link": "链接不存在",
        "not_found.back": "返回最近一期",
        "category.generic": "AI 产品",
        "access.free": "免费",
        "access.freemium": "免费+付费",
        "access.paid": "收费",
        "access.unknown": "收费未确认",
        "access.login": "需登录",
        "access.api_key": "需要 API 密钥",
        "access.waitlist": "需候补",
        "access.self_host": "需本地安装",
        "fallback.one_liner": "{label} 方向的新发现，点开看怎么玩、有什么门槛",
        "fallback.why": "{label} 方向的近期新品；以下内容按已核查证据整理，未实测不额外承诺。",
        "fallback.story.direct": "手头正好有类似任务：打开入口，按提示走一遍核心流程，几分钟内判断它是否顺手，再决定要不要长期用。",
        "fallback.story.demo": "想先看效果再投入时间：打开页面看演示或截图，对照自己的场景，判断值不值得上手。",
        "fallback.story.setup": "愿意动手配置：按项目文档完成安装，跑通第一个示例，再决定是否纳入日常工作流。",
        "fallback.step.open": "打开入口页面",
        "fallback.step.open_url": "打开入口页面：{url}",
        "fallback.step.readme": "阅读 README 的安装与运行说明",
        "fallback.step.run": "按文档完成本地安装后启动",
        "fallback.step.limits": "准备时间未验证，安装前确认平台与依赖",
        "fallback.step.demo_watch": "查看公开演示或截图中呈现的核心效果",
        "fallback.step.demo_compare": "对照页面说明判断是否符合自己的使用场景",
        "fallback.step.demo_gate": "若需安装或付费，先确认门槛再决定",
        "fallback.step.direct_flow": "按页面提示开始核心操作",
        "fallback.step.direct_check": "观察页面反馈是否符合描述",
    },
    "en": {
        "meta.title": "Vibe Radar · Daily picks for AI builders",
        "meta.description": "Every morning at 9, a curated list of newly discovered AI builder tools.",
        "brand.name": "Vibe Radar",
        "brand.sub": "振感雷达",
        "nav.today": "Today",
        "nav.saved": "Saved",
        "nav.rss": "RSS",
        "nav.settings": "Settings",
        "lang.switch": "Language",
        "footer.line1": "Daily curation for AI builders · No account, anonymous feedback · Saved items stay in this browser",
        "footer.line2": "“Tried it” is reader-reported and does not mean actions on third-party sites were completed.",
        "home.hero.title": "Vibe Radar",
        "home.hero.desc": "Every morning at 9, a curated list of newly discovered AI builder tools: what it is, why it matters, how to try it fast, and what it costs.",
        "home.hero.first": "The first public edition is not out yet. The latest edition will appear here once published.",
        "home.today": "Today's picks",
        "home.latest": "Latest edition",
        "home.count": "{count} items",
        "home.short": "Short edition · {count}",
        "home.empty_badge": "No picks",
        "home.not_today": "Nothing published today yet. Showing the latest edition ({date}).",
        "home.permalink": "Permanent link:",
        "home.highlights": "Top picks",
        "home.empty.p1": "No item passed the quality bar today; older products are not passed off as new.",
        "home.empty.p2": "Browse recent editions below or check back tomorrow.",
        "edition.empty.p1": "No item passed the quality bar today; older products are not passed off as new.",
        "edition.empty.p2": "Browse earlier editions below.",
        "edition.empty.p3": "No earlier editions yet. The next verified pick will appear here.",
        "edition.snapshot": "This page is a permanent snapshot; revisions follow the latest version.",
        "intro.normal": "Today's edition: {count} picks.",
        "intro.short": "Fewer picks passed verification today: {count}.",
        "intro.empty": "No pick passed verification today. Past editions remain available.",
        "section.tool": "Tools & workflow",
        "section.creative": "Creative & experimental",
        "recent.latest": "Recent editions",
        "recent.past": "Earlier editions",
        "recent.count": "{count} picks",
        "recent.short": "Short · {count}",
        "recent.empty": "No picks",
        "recent.degraded": "Partial source coverage",
        "card.highlight": "Top pick",
        "card.user_story": "User story: ",
        "card.why": "Why it matters: ",
        "card.try": "Open project / try it",
        "card.tried": "Tried it",
        "card.save": "Save",
        "card.saved": "Saved",
        "card.feedback": "Rate & report",
        "card.checked": "Checked: {date}",
        "evidence.human_tested": "Human-tested",
        "evidence.doc_checked": "Docs checked",
        "evidence.probe_tested": "Probe-checked",
        "evidence.failed": "Entry not verified",
        "evidence.unverified": "Evidence pending",
        "mobile.unknown": "Mobile experience unconfirmed",
        "mobile.desktop_required": "Desktop required",
        "source.origin": "Source",
        "source.project": "Project / site",
        "mode.direct": "Try it live",
        "mode.demo": "Watch demo",
        "mode.setup": "Install & try",
        "product_status.active": "Active",
        "product_status.merged": "Merged",
        "product_status.blocked": "Blocked",
        "product_status.retracted": "Retracted",
        "product.entry": "Project / repository",
        "product.first_seen": "First seen: {date}",
        "product.history": "Appeared in",
        "product.history_note": "Editions keep their original snapshot; later profile updates do not rewrite past cards.",
        "product.history_none": "Not featured in a published edition yet.",
        "product.history_position": "No. {position}",
        "product.retracted": "This product was retracted: {note} We no longer claim it can be tried normally.",
        "product.retracted_default": "Entry or information unavailable.",
        "saved.title": "Saved",
        "saved.desc": "Saved items live in this browser's localStorage only. The full list is never uploaded and does not sync across devices. Clearing storage loses them.",
        "saved.empty_prefix": "Nothing saved yet. Go to",
        "saved.empty_link": "Today's picks",
        "saved.empty_suffix": "to add products worth trying.",
        "saved.from_edition": "Saved from {date}",
        "saved.item": "Saved item",
        "saved.feedback": "Feedback",
        "saved.remove": "Remove",
        "feedback.title": "Tell the curator about your experience",
        "feedback.desc": "No sign-up needed. Links opened from Feishu ask you to confirm here; link-preview bots produce no feedback.",
        "feedback.tried": "Tried it",
        "feedback.interest": "Interesting",
        "feedback.boring": "Boring",
        "feedback.try_failed": "Couldn't try",
        "feedback.clear": "Clear rating",
        "feedback.fail_label": "Why couldn't you try it? (optional)",
        "feedback.fail_none": "Skip",
        "feedback.fail_submit": "Submit reason",
        "feedback.reason.entry_dead": "Entry broken",
        "feedback.reason.login_required": "Login required",
        "feedback.reason.paid": "Payment required",
        "feedback.reason.missing_api_key": "Missing API key",
        "feedback.reason.platform_unsupported": "Platform unsupported",
        "feedback.reason.steps_mismatch": "Steps don't match",
        "feedback.reason.other": "Other",
        "feedback.report_title": "Report a content issue",
        "feedback.issue_type": "Issue type",
        "feedback.issue.pricing_wrong": "Pricing mismatch",
        "feedback.issue.link_dead": "Entry broken",
        "feedback.issue.platform_wrong": "Platform mismatch",
        "feedback.issue.steps_wrong": "Steps wrong",
        "feedback.issue.other": "Other",
        "feedback.note_label": "One-line note (optional)",
        "feedback.note_placeholder": "e.g. the site charges after sign-up",
        "feedback.submit_issue": "Submit report",
        "feedback.issue_ok": "Report received. Content issues enter same-day review.",
        "feedback.issue_failed": "Submission failed, please retry later.",
        "feedback.save_failed": "Save failed, please retry",
        "feedback.selection_kept": " (your choice is kept, you can submit again)",
        "feedback.state.tried": "Recorded: tried",
        "feedback.state.interest": "Recorded: interesting",
        "feedback.state.boring": "Recorded: boring",
        "feedback.state.try_failed": "Recorded: couldn't try",
        "feedback.state.reason": "Reason recorded",
        "feedback.state.saved": "Saved",
        "not_found.title": "Not found",
        "not_found.page": "The page you requested does not exist.",
        "not_found.default": "The content you requested does not exist.",
        "not_found.date": "Invalid date",
        "not_found.edition": "No published edition for this date",
        "not_found.product": "Product not found",
        "not_found.item": "Item not found",
        "not_found.link": "Link not found",
        "not_found.back": "Back to the latest edition",
        "category.generic": "AI builder tools",
        "access.free": "Free",
        "access.freemium": "Free + paid",
        "access.paid": "Paid",
        "access.unknown": "Pricing unconfirmed",
        "access.login": "Login required",
        "access.api_key": "API key required",
        "access.waitlist": "Waitlist",
        "access.self_host": "Self-hosted",
        "fallback.one_liner": "A new {label} find; check what it does and what it costs",
        "fallback.why": "A recent {label} release. Copy below is assembled from checked evidence only; nothing beyond that is promised.",
        "fallback.story.direct": "You have a similar task at hand: open the entry, run the core flow as prompted, and decide within minutes whether it fits your workflow.",
        "fallback.story.demo": "You want to see the result before investing time: open the page, watch the demo or screenshots, and compare it with your own use case.",
        "fallback.story.setup": "You are willing to set it up: follow the project docs, get the first example running, then decide whether to keep it in your daily workflow.",
        "fallback.step.open": "Open the entry page",
        "fallback.step.open_url": "Open the entry page: {url}",
        "fallback.step.readme": "Follow the README to install and run",
        "fallback.step.run": "Start it locally and try the first example",
        "fallback.step.limits": "Setup time is unverified; check platforms and requirements first",
        "fallback.step.demo_watch": "Watch the public demo or screenshots",
        "fallback.step.demo_compare": "Compare it with your own use case",
        "fallback.step.demo_gate": "Confirm pricing and requirements before installing",
        "fallback.step.direct_flow": "Follow the on-screen steps for the core flow",
        "fallback.step.direct_check": "Check whether the result matches the description",
    },
}

_MODE_VALUES = ("direct", "demo", "setup")
_EVIDENCE_VALUES = ("human_tested", "doc_checked", "probe_tested", "failed", "unverified")

SOURCE_LABEL_MAP = {"来源": "source.origin", "项目/官网": "source.project"}

JS_KEYS = [
    "card.save",
    "card.saved",
    "card.feedback",
    "saved.from_edition",
    "saved.item",
    "saved.feedback",
    "saved.remove",
    "feedback.state.tried",
    "feedback.state.interest",
    "feedback.state.boring",
    "feedback.state.try_failed",
    "feedback.state.reason",
    "feedback.state.saved",
    "feedback.save_failed",
    "feedback.selection_kept",
    "feedback.issue_ok",
    "feedback.issue_failed",
]


def normalize_locale(value: str | None) -> str:
    return value if value in SUPPORTED_LOCALES else DEFAULT_LOCALE


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def is_copy_pure(text: str, locale: str) -> bool:
    """该语言下文案是否可用：中文要求含中文字符，英文要求不含中文字符。"""
    text = (text or "").strip()
    if not text:
        return False
    return has_cjk(text) if normalize_locale(locale) == "zh" else not has_cjk(text)


def translate(locale: str, key: str, **kwargs) -> str:
    locale = normalize_locale(locale)
    text = MESSAGES.get(locale, MESSAGES[DEFAULT_LOCALE]).get(key)
    if text is None:
        text = MESSAGES[DEFAULT_LOCALE].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def mode_label(mode: str, locale: str) -> str:
    return translate(locale, f"mode.{mode}") if mode in _MODE_VALUES else (mode or "")


def evidence_label(status: str, locale: str) -> str:
    return (
        translate(locale, f"evidence.{status}")
        if status in _EVIDENCE_VALUES
        else (status or "")
    )


def section_label(key: str, locale: str) -> str:
    return translate(locale, f"section.{key}") if key in ("tool", "creative") else (key or "")


def product_status_label(status: str, locale: str) -> str:
    key = f"product_status.{status}"
    locale = normalize_locale(locale)
    if key in MESSAGES[locale]:
        return MESSAGES[locale][key]
    return status or ""


def localize_source_links(links: list[dict] | None, locale: str) -> list[dict]:
    locale = normalize_locale(locale)
    localized: list[dict] = []
    for link in links or []:
        if not isinstance(link, dict):
            continue
        label = str(link.get("label") or "")
        url = str(link.get("url") or "")
        key = SOURCE_LABEL_MAP.get(label)
        if key:
            label = translate(locale, key)
        elif locale == "en" and has_cjk(label):
            label = translate(locale, "source.origin")
        localized.append({"label": label, "url": url})
    return localized


def access_display(access: dict | None, legacy_text: str | None, locale: str) -> str:
    """门槛文案：优先用结构化 access 按语言渲染；老数据字符串做术语规范化/兜底。"""
    locale = normalize_locale(locale)
    if isinstance(access, dict) and access:
        parts = [translate(locale, f"access.{access.get('pricing') or 'unknown'}")]
        if access.get("login_required") is True:
            parts.append(translate(locale, "access.login"))
        if access.get("api_key_required") is True:
            parts.append(translate(locale, "access.api_key"))
        if access.get("waitlist") is True:
            parts.append(translate(locale, "access.waitlist"))
        if access.get("self_host_required") is True:
            parts.append(translate(locale, "access.self_host"))
        platforms = [str(p) for p in (access.get("platforms") or []) if p]
        if platforms:
            parts.append(" / ".join(platforms))
        return " · ".join(parts)
    if legacy_text:
        if locale == "zh":
            return legacy_text.replace("需要API key", "需要 API 密钥")
        return translate(locale, "access.unknown")
    return translate(locale, "access.unknown")


def mobile_note_label(key: str | None, legacy_text: str | None, locale: str) -> str | None:
    key = key or ""
    if not key and legacy_text:
        mapping = {
            "手机体验未确认": "mobile.unknown",
            "需桌面设备": "mobile.desktop_required",
        }
        key = mapping.get(legacy_text, "")
    if key in ("mobile.unknown", "mobile.desktop_required"):
        return translate(locale, key)
    return None


def _pure_value(value, locale: str) -> bool:  # noqa: ANN001
    if isinstance(value, str):
        return is_copy_pure(value, locale)
    if isinstance(value, list):
        return bool(value) and all(isinstance(v, str) and is_copy_pure(v, locale) for v in value)
    return False


def default_steps(mode: str, locale: str, url: str | None) -> list[str]:
    locale = normalize_locale(locale)
    mode = mode if mode in _MODE_VALUES else "setup"
    opened = (
        translate(locale, "fallback.step.open_url", url=url)
        if url
        else translate(locale, "fallback.step.open")
    )
    if mode == "setup":
        return [
            opened,
            translate(locale, "fallback.step.readme"),
            translate(locale, "fallback.step.run"),
            translate(locale, "fallback.step.limits"),
        ]
    if mode == "demo":
        return [
            opened,
            translate(locale, "fallback.step.demo_watch"),
            translate(locale, "fallback.step.demo_compare"),
            translate(locale, "fallback.step.demo_gate"),
        ]
    return [
        opened,
        translate(locale, "fallback.step.direct_flow"),
        translate(locale, "fallback.step.direct_check"),
    ]


def fallback_one_liner(locale: str, category_label: str) -> str:
    return translate(locale, "fallback.one_liner", label=category_label)


def fallback_why(locale: str, category_label: str) -> str:
    return translate(locale, "fallback.why", label=category_label)


def fallback_user_story(locale: str, mode: str, category_label: str) -> str:
    mode = mode if mode in _MODE_VALUES else "setup"
    return translate(locale, f"fallback.story.{mode}", label=category_label)


def localize_card_content(
    content: dict,
    locale: str,
    category_label: str | None = None,
) -> dict:
    """按语言挑选卡片文案；缺失或不纯净时用确定性兜底，绝不混排两种语言。"""
    locale = normalize_locale(locale)
    label = category_label or content.get("category_label") or content.get("category") or ""
    if not label or (locale == "en" and has_cjk(str(label))):
        label = translate(locale, "category.generic")
    en_block = (content.get("i18n") or {}).get("en") or {}
    name = content.get("name") or ""
    mode = content.get("mode") or "setup"
    links = content.get("source_links") or []
    url = ""
    for link in links:
        if isinstance(link, dict) and link.get("url"):
            url = str(link["url"])
            break

    def stored(field: str):  # noqa: ANN202
        if locale == "en":
            candidate = en_block.get(field)
            if _pure_value(candidate, "en"):
                return candidate
            legacy = content.get(field)
            if _pure_value(legacy, "en"):
                return legacy
            return None
        candidate = content.get(field)
        if _pure_value(candidate, "zh"):
            return candidate
        return None

    one_liner = stored("one_liner") or fallback_one_liner(locale, label)
    why = stored("why_interesting") or fallback_why(locale, label)
    story = stored("user_story") or fallback_user_story(locale, mode, label)
    steps = stored("steps") or default_steps(mode, locale, url)

    return {
        "name": name,
        "category_label": label,
        "one_liner": one_liner,
        "why_interesting": why,
        "user_story": story,
        "steps": steps,
        "access_text": access_display(
            content.get("access_json"), content.get("access_text"), locale
        ),
        "mobile_note": mobile_note_label(
            content.get("mobile_note_key"), content.get("mobile_note"), locale
        ),
        "source_links": localize_source_links(links, locale),
    }
