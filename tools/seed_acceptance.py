"""为验收准备多日示例数据（相对当天日期）：

- 昨天：常规刊 6 条（多分类、含创意作品，验证重点推荐与分组）
- 前天：空刊（验证"暂无推荐、不冒充今日新品"与历史入口）
已存在的日期会跳过，不会覆盖真实数据。

用法：
    python tools/seed_acceptance.py
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from viberadar.compose import freeze_edition, publish_edition
from viberadar.settings import get_settings, reload_settings
from viberadar.store import get_session_factory, repo_editions, repo_pipeline, repo_products

SPECS = [
    ("Delta CI Guard", "agent-hygiene", "Agent 工作流保障", "盯着编码 agent 别偷偷弱化测试，提交前自动比对用例改动。", "免费 · 需登录", "demo"),
    ("Pocket Agent", "coding-agent", "编码 Agent", "把多 agent 编排塞进手机通知流，出门也能推进小任务。", "免费 + 付费 · 需登录", "direct"),
    ("MCP Smith", "mcp-skill", "MCP 与技能", "三条命令把内部接口包成 MCP server，自带调用日志。", "开源免费 · 需本地安装", "setup"),
    ("Landing Witch", "app-builder", "应用构建", "一句话生成可发布的落地页，改文案不用等设计。", "免费试用", "direct"),
    ("Glitch Garden", "creative-toy", "创意作品", "把代码报错养成果园里的像素植物，越修越茂盛。", "免费 · 无需登录", "direct"),
    ("Focus Lens", "editor-extension", "编辑器工具", "VS Code 侧栏实时显示 agent 上下文占用，配额一眼可见。", "免费 · 需登录", "demo"),
]


def _publish_at(day: date) -> datetime:
    tz = get_settings().pipeline.tz
    local = datetime.combine(day, time(9, 0), tzinfo=tz)
    return local.astimezone(timezone.utc)


def seed_normal(session, target: date) -> None:
    settings = get_settings()
    if repo_editions.get_edition_by_date(session, target, settings.pipeline.timezone):
        print(f"{target} 已存在，跳过")
        return
    for idx, (name, category, label, one_liner, access, mode) in enumerate(SPECS):
        slug = name.lower().replace(" ", "-")
        product = repo_products.create_product(
            session, name, f"https://example.com/{slug}", None,
            f"url:https://example.com/{slug}",
            datetime.combine(target, time(6, 0), tzinfo=timezone.utc),
        )
        product.category = category
        content = {
            "name": name,
            "category": category,
            "category_label": label,
            "one_liner": one_liner,
            "why_interesting": one_liner,
            "mode": mode,
            "steps": ["打开入口", "完成第一步操作", "观察核心效果"],
            "access_text": access,
            "time_promise": None,
            "mobile_note": "手机体验未确认",
            "evidence_status": "doc_checked",
            "checked_at": datetime.combine(target, time(7, 0), tzinfo=timezone.utc).isoformat(),
            "source_links": [{"label": "来源", "url": f"https://example.com/{slug}/src"}],
        }
        repo_pipeline.upsert_candidate(
            session, target, "run_seed", product.id, None, True, 0.9 - idx * 0.02, {}, content
        )
    edition = freeze_edition(session, target, settings=settings, now=_publish_at(target) - timedelta(hours=1))
    publish_edition(session, edition, now=_publish_at(target))
    print(f"{target} 已发布：{edition.content_status} · {edition.intro}")


def seed_empty(session, target: date) -> None:
    settings = get_settings()
    if repo_editions.get_edition_by_date(session, target, settings.pipeline.timezone):
        print(f"{target} 已存在，跳过")
        return
    edition = freeze_edition(session, target, settings=settings, now=_publish_at(target) - timedelta(hours=1))
    publish_edition(session, edition, now=_publish_at(target))
    print(f"{target} 已发布：{edition.content_status} · {edition.intro}")


def main() -> None:
    reload_settings()
    today = date.today()
    with get_session_factory()() as session:
        seed_normal(session, today - timedelta(days=1))
        seed_empty(session, today - timedelta(days=2))
        session.commit()
    print("完成")


if __name__ == "__main__":
    main()
