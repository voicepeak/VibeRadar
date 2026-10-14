"""富化质量：句界压缩与证据驱动的试玩步骤（本 PR 的不变量）。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from viberadar.contracts import AccessInfo, TryMode
from viberadar.pipeline.enrich import (
    PageContent,
    clip_summary,
    extract_install_command,
    template_enrichment,
    fetch_page,
    infer_access,
)

CHECKED_AT = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
ENTRY = "https://github.com/acme/demo"


def _access() -> AccessInfo:
    return AccessInfo(checked_at=CHECKED_AT)


def _page(chunks: list[str] | None = None, description: str | None = None) -> PageContent:
    return PageContent(
        url=ENTRY,
        final_url=ENTRY,
        status=200,
        reachable=True,
        description=description,
        chunks=list(chunks or []),
    )


def _product():
    return SimpleNamespace(id="prd_1", name="acme/demo", summary=None, category=None)


def _raw(description: str = "面向编码 agent 的上下文快照与回滚工具"):
    return SimpleNamespace(
        description=description,
        target_url=ENTRY,
        source_url=ENTRY,
    )


def test_repository_access_uses_readme_not_host_navigation():
    html = '<nav>Sign in Pricing $9 iOS App Store</nav><article class="markdown-body entry-content"><h1>Demo</h1><p>Open source MIT license</p><p>pip install demo</p><p>Various scenarios</p></article>'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    page = fetch_page(ENTRY, transport=transport)
    assert page.text == 'Demo Open source MIT license pip install demo Various scenarios'
    access = infer_access(page, _raw())
    assert access.pricing.value == 'free'
    assert access.login_required is False and 'iOS' not in access.platforms
    assert access.self_host_required is True


def test_repository_without_readme_does_not_infer_navigation_requirements():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='<nav>Sign in Pricing $9 iOS App Store</nav>'))
    page = fetch_page(ENTRY, transport=transport)
    assert not page.text and not page.chunks
    access = infer_access(page, _raw())
    assert access.pricing.value == 'unknown' and access.login_required is False
    assert not access.platforms


# ---------- clip_summary ----------


def test_clip_summary_short_text_unchanged():
    text = "把编码会话的上下文变成可回滚记录"
    assert clip_summary(text, 40) == text


def test_clip_summary_within_limit_no_ellipsis():
    text = "a" * 40
    assert clip_summary(text, 40) == text


def test_clip_summary_cjk_sentence_boundary():
    text = "把编码会话的上下文变成可以随时回滚的本地记录。后面还有很多内容不该出现这只是补充的描述文字"
    assert len(text) > 40
    result = clip_summary(text, 40)
    assert result == "把编码会话的上下文变成可以随时回滚的本地记录。…"
    assert len(result) <= 40


def test_clip_summary_english_word_boundary():
    text = "Deploy intelligent virtual assistants to streamline routine administrative tasks"
    result = clip_summary(text, 40)
    assert result == "Deploy intelligent virtual assistants…"
    assert len(result) <= 40


def test_clip_summary_never_exceeds_limit():
    samples = [
        "很长的中文描述" * 10,
        "no spaces at all in this very long english sentence" * 3,
        "混合 mixed 内容 with symbols #$% 和中文" * 5,
        "短",
        "",
    ]
    for sample in samples:
        for limit in (20, 40, 160):
            assert len(clip_summary(sample, limit)) <= limit


def test_clip_summary_empty_returns_empty():
    assert clip_summary("", 40) == ""
    assert clip_summary("   ", 40) == ""


# ---------- extract_install_command ----------


def test_extract_install_command_git_clone():
    command = extract_install_command(
        ["Installation", "`git clone https://github.com/acme/demo.git`", "cd demo"]
    )
    assert command == "git clone https://github.com/acme/demo.git"


def test_extract_install_command_npm_and_docker():
    assert extract_install_command(["npm install -g demo-cli"]) == "npm install -g demo-cli"
    assert (
        extract_install_command(["docker run -p 8000:8000 acme/demo:latest"])
        == "docker run -p 8000:8000 acme/demo:latest"
    )


def test_extract_install_command_none_for_prose():
    assert extract_install_command(["Download the binary and run it on your machine"]) is None
    assert extract_install_command([]) is None


def test_extract_install_command_skips_oversized_chunks():
    chunk = ("prose " * 45) + "npm install demo"
    assert len(chunk) > 240
    assert extract_install_command([chunk]) is None


# ---------- template_enrichment ----------


def test_template_setup_uses_real_command():
    page = _page(chunks=["git clone https://github.com/acme/demo.git", "npm install"])
    enrichment = template_enrichment(
        _product(), _raw(), _access(), TryMode.SETUP, [], page=page
    )
    assert any("git clone https://github.com/acme/demo.git" in step.text for step in enrichment.steps)
    assert len(enrichment.steps) <= 4


def test_template_setup_without_command_is_honest():
    enrichment = template_enrichment(
        _product(), _raw(), _access(), TryMode.SETUP, [], page=_page(chunks=["read the docs"])
    )
    assert any("未检测到明确命令" in step.text for step in enrichment.steps)


def test_template_demo_includes_self_description():
    page = _page(description="在线体验：上传一段录音即可得到转写文本")
    enrichment = template_enrichment(
        _product(), _raw(), _access(), TryMode.DEMO, [], page=page
    )
    assert any(step.text.startswith("页面自述：") for step in enrichment.steps)


def test_template_one_liner_passes_contract_for_long_english():
    raw = _raw(
        description=(
            "Deploy intelligent virtual assistants to streamline routine administrative tasks "
            "and coordinate every follow-up automatically"
        )
    )
    enrichment = template_enrichment(_product(), raw, _access(), TryMode.SETUP, [], page=_page())
    en_one = enrichment.en.one_liner
    assert len(en_one) <= 80
    assert en_one.endswith("…")
    assert not en_one[:-1].endswith(" st")


def test_template_one_liner_zh_sentence_boundary():
    raw = _raw(
        description=(
            "把编码会话的上下文变成可以随时回滚的本地记录，并且保留来源、差异与回放时间线，帮助团队协作与审计"
        )
    )
    enrichment = template_enrichment(_product(), raw, _access(), TryMode.DEMO, [], page=_page())
    assert len(enrichment.one_liner) <= 40
    assert enrichment.one_liner.endswith("…")
    assert enrichment.one_liner.startswith("把编码会话的上下文变成可以随时回滚的本地记录")
