"""编排：冻结选择、短刊/空刊、人工修订层、更正版本（PRD 5.2/5.3/M6）。"""

from __future__ import annotations

from datetime import date, datetime, timezone

from viberadar.compose import build_edition_view, create_correction, freeze_edition, publish_edition
from viberadar.store import repo_editions, repo_pipeline, repo_products

NOW = datetime(2026, 10, 12, 0, 30, tzinfo=timezone.utc)
EDITION_DATE = date(2026, 10, 12)


def _product(session, name: str):
    return repo_products.create_product(
        session, name, None, None, f"url:https://example.com/{name}", NOW
    )


def _content(name: str, status: str = "doc_checked", category: str = "agent-hygiene"):
    return {
        "name": name,
        "category": category,
        "category_label": name,
        "one_liner": f"{name} 的一句话",
        "why_interesting": "理由",
        "mode": "demo",
        "steps": ["打开入口"],
        "access_text": "免费 · 需登录",
        "evidence_status": status,
        "checked_at": NOW.isoformat(),
        "source_links": [{"label": "来源", "url": "https://example.com/src"}],
    }


def seed(session, name: str, auto: bool = True, score: float = 0.8, status: str | None = None,
         evidence: str = "doc_checked", locked: bool = False, category: str = "agent-hygiene"):
    product = _product(session, name)
    row = repo_pipeline.upsert_candidate(
        session,
        EDITION_DATE,
        "run_seed",
        product.id,
        None,
        auto,
        score,
        {},
        _content(name, evidence, category),
    )
    if status or locked:
        repo_pipeline.apply_curator_change(session, row, status=status, locked=locked or None)
    return product, row


def test_freeze_normal_edition_uses_target(session, temp_settings):
    categories = ["agent-hygiene", "coding-agent", "mcp-skill"]
    for i in range(20):
        seed(session, f"P{i}", score=0.9 - i * 0.01, category=categories[i % 3])
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW)
    items = repo_editions.list_items(session, edition.id, revision=1)
    assert len(items) == 15  # public_target（宽进策略）
    assert edition.content_status == "normal"
    assert edition.intro.startswith("今日精选 15 条")
    assert items[0].highlight is True and items[1].highlight is True and items[2].highlight is False


def test_freeze_short_and_empty(session, temp_settings):
    for i in range(2):
        seed(session, f"S{i}")
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW)
    assert edition.content_status == "short"  # 少于 public_min（宽进策略：3）
    assert len(repo_editions.list_items(session, edition.id, revision=1)) == 2

    # 空刊：新日期无候选
    edition2 = freeze_edition(session, date(2026, 10, 13), settings=temp_settings, now=NOW)
    assert edition2.content_status == "empty"
    assert "暂无通过核验的推荐" in (edition2.intro or "")


def test_rejected_candidate_excluded(session, temp_settings):
    p1, c1 = seed(session, "Keep")
    p2, c2 = seed(session, "Drop")
    repo_pipeline.apply_curator_change(session, c2, status="rejected")
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW)
    items = repo_editions.list_items(session, edition.id, revision=1)
    assert [i.product_id for i in items] == [p1.id]


def test_hard_gate_blocks_curator_approved_failed_entry(session, temp_settings):
    p1, c1 = seed(session, "Good")
    p2, c2 = seed(session, "BadGate", auto=False, evidence="failed", status="approved")
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW)
    items = repo_editions.list_items(session, edition.id, revision=1)
    assert [i.product_id for i in items] == [p1.id]
    assert c2.curator_status == "approved"  # 硬门槛人工也不能绕过


def test_curator_edits_applied_to_snapshot(session, temp_settings):
    _, candidate = seed(session, "Edited")
    repo_pipeline.apply_curator_change(
        session, candidate, status="approved", edits={"one_liner": "人工改写的一句话"}
    )
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW)
    items = repo_editions.list_items(session, edition.id, revision=1)
    assert items[0].content_json["one_liner"] == "人工改写的一句话"


def test_public_view_uses_outbound_links_not_raw_redirect(session, temp_settings):
    seed(session, "Linked")
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW)
    view = build_edition_view(session, edition)
    assert view.items[0].links.get("project")
    link = repo_editions.get_outbound_link(session, view.items[0].links["project"])
    assert link is not None and link.target_url.startswith("https://")


def test_correction_creates_new_revision_and_keeps_old(session, temp_settings):
    seed(session, "FixMe")
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW)
    publish_edition(session, edition, now=NOW)
    items = repo_editions.list_items(session, edition.id, revision=1)
    create_correction(
        session,
        edition,
        changes={items[0].id: {"access_text": "收费"}},
        reason="读者报告费用错误",
        operator="curator",
    )
    assert edition.revision == 2
    old = repo_editions.list_items(session, edition.id, revision=1)
    new = repo_editions.list_items(session, edition.id, revision=2)
    assert old[0].content_json["access_text"] != new[0].content_json["access_text"]
    view = build_edition_view(session, edition)
    assert view.items[0].access_text == "收费"
    assert view.revision == 2


def test_static_export_matches_public_page_structure(session, temp_settings):
    """静态导出与 Web 页面同构：首页含卡片与重点推荐，重点条目不重复进分组。"""
    categories = ["agent-hygiene", "coding-agent", "creative-toy"]
    for i in range(8):
        seed(session, f"Pub{i}", score=0.9 - i * 0.01, category=categories[i % 3])
    edition = freeze_edition(session, EDITION_DATE, settings=temp_settings, now=NOW)
    publish_edition(session, edition, now=NOW)

    from viberadar.compose.render import export_site

    exported = export_site(session, temp_settings)
    assert len(exported) == 3  # 日期页 + 首页 + RSS
    index_html = (temp_settings.public_dir / "index.html").read_text(encoding="utf-8")
    edition_html = (
        temp_settings.public_dir / "d" / f"{EDITION_DATE.isoformat()}.html"
    ).read_text(encoding="utf-8")

    for html in (index_html, edition_html):
        assert "重点推荐" in html
        assert "评价与报错" in html
        assert 'data-action="interest"' not in html
        assert html.count('data-action="tried"') == 8  # 每条只渲染一次，重点条目不重复
        assert "/admin/llm" not in html  # 设置入口不出现在公开静态导出
