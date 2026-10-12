"""为验收准备多日示例数据（相对当天日期）：

- 昨天：常规刊 6 条（多分类、含创意作品，验证重点推荐与分组；中英双语 + 用户故事）
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
    {
        "name": "Delta CI Guard",
        "category": "agent-hygiene",
        "label": "Agent 工作流保障",
        "label_en": "Agent Workflow Tooling",
        "mode": "demo",
        "zh": {
            "one_liner": "盯着编码 agent 别偷偷弱化测试，提交前自动比对用例改动。",
            "why": "agent 改代码时顺手删掉或跳过失败的测试，是团队最常见的隐形事故。",
            "story": "你是维护 CI 的工程师：仓库接上它之后，agent 每次改测试文件都会先给你一份改动对照，弱化测试的提交在合并前就被拦下。",
            "steps": ["打开项目页面", "连接一个测试仓库", "让 agent 提交一次测试改动，观察比对结果"],
        },
        "en": {
            "one_liner": "Catches coding agents quietly weakening your tests before each commit.",
            "why": "Agents often delete or skip failing tests; this makes that visible before merge.",
            "story": "You maintain CI for a repo with agent-authored PRs: wire it up, and every test-file change gets a diff review before it can merge.",
            "steps": ["Open the project page", "Connect a test repository", "Let an agent change a test and watch the diff"],
        },
        "access": {"zh": "免费 · 需登录", "pricing": "free", "login": True},
    },
    {
        "name": "Pocket Agent",
        "category": "coding-agent",
        "label": "编码 Agent",
        "label_en": "Coding Agents",
        "mode": "direct",
        "zh": {
            "one_liner": "把多 agent 编排塞进手机通知流，出门也能推进小任务。",
            "why": "大部分 agent 编排工具把你钉在电脑前，它把确认节点推到手机通知里。",
            "story": "你在排队等人的十分钟里：手机收到 agent 的任务节点通知，点几下确认下一步，回到电脑时分支已经推进完了。",
            "steps": ["打开入口并登录", "连接一个代码仓库", "在手机上批准一个 agent 节点"],
        },
        "en": {
            "one_liner": "Puts multi-agent orchestration into your phone's notification stream.",
            "why": "Most agent tools keep you at a desk; this pushes approval steps to mobile notifications.",
            "story": "You have ten minutes in a queue: a notification arrives for an agent checkpoint, you approve the next step on your phone, and the branch has moved on by the time you're back.",
            "steps": ["Open the entry and sign in", "Connect a code repository", "Approve an agent checkpoint on your phone"],
        },
        "access": {"zh": "免费+付费 · 需登录", "pricing": "freemium", "login": True, "platforms": ["iOS", "Android"]},
    },
    {
        "name": "MCP Smith",
        "category": "mcp-skill",
        "label": "MCP 与技能",
        "label_en": "MCP & Skills",
        "mode": "setup",
        "zh": {
            "one_liner": "三条命令把内部接口包成 MCP server，自带调用日志。",
            "why": "接 MCP 的重复样板代码很烦，它把包装、鉴权和日志一次做掉。",
            "story": "你要给团队内部 API 接上 Claude：三条命令生成 server，第二天同事就能在不写胶水代码的情况下调用内部数据。",
            "steps": ["克隆仓库并安装依赖", "指向内部接口生成 server", "在客户端里调用并查看日志"],
        },
        "en": {
            "one_liner": "Wraps internal APIs into an MCP server in three commands, with call logs.",
            "why": "MCP boilerplate is tedious; this handles wrapping, auth and logging in one pass.",
            "story": "You need to expose an internal API to your assistant: generate the server in three commands, and teammates can call internal data the next day without glue code.",
            "steps": ["Clone the repo and install dependencies", "Point it at the internal API to generate a server", "Call it from a client and inspect the logs"],
        },
        "access": {"zh": "开源免费 · 需本地安装", "pricing": "free", "self_host": True, "platforms": ["macOS", "Linux"]},
    },
    {
        "name": "Landing Witch",
        "category": "app-builder",
        "label": "应用构建",
        "label_en": "App Builders",
        "mode": "direct",
        "zh": {
            "one_liner": "一句话生成可发布的落地页，改文案不用等设计。",
            "why": "小团队落地页迭代快，它让改文案和换结构变成一句话的事。",
            "story": "产品周五要发新功能但设计排期满了：你输入一句话生成落地页，自己改三版文案，下午就能发出去。",
            "steps": ["打开入口", "输入产品一句话生成页面", "在线改文案并导出发布"],
        },
        "en": {
            "one_liner": "Generates a shippable landing page from one sentence."
            ,
            "why": "Small teams iterate landing copy fast; this turns a rewrite into one sentence.",
            "story": "Launch is Friday but design is booked: generate the page from one sentence, edit three rounds of copy yourself, and ship it the same afternoon.",
            "steps": ["Open the entry", "Describe the product in one sentence", "Edit the copy online and publish"],
        },
        "access": {"zh": "免费试用", "pricing": "free", "login": True},
    },
    {
        "name": "Glitch Garden",
        "category": "creative-toy",
        "label": "创意作品",
        "label_en": "Creative & Fun",
        "mode": "direct",
        "zh": {
            "one_liner": "把代码报错养成果园里的像素植物，越修越茂盛。",
            "why": "调试的挫败感被换成了养成反馈，适合给团队新人破冰。",
            "story": "你调试一个棘手的报错半小时了：每修掉一个错误，屏幕角落的像素树就长高一节，久违地觉得修 bug 有点上头。",
            "steps": ["打开入口开始体验", "粘贴一段报错的代码", "修掉错误看植物变化"],
        },
        "en": {
            "one_liner": "Grows pixel plants in a garden every time your code errors get fixed.",
            "why": "Turns debugging frustration into a small cultivation loop; great for team icebreakers.",
            "story": "You've been stuck on an error for half an hour: every fix grows the pixel tree in the corner, and debugging suddenly feels a bit addictive.",
            "steps": ["Open the entry", "Paste code that throws", "Fix the error and watch the plant grow"],
        },
        "access": {"zh": "免费 · 无需登录", "pricing": "free", "login": False},
    },
    {
        "name": "Focus Lens",
        "category": "editor-extension",
        "label": "编辑器工具",
        "label_en": "Editor & IDE Tools",
        "mode": "demo",
        "zh": {
            "one_liner": "VS Code 侧栏实时显示 agent 上下文占用，配额一眼可见。",
            "why": "上下文快满时 agent 会悄悄降智，提前看见占用就能及时收尾。",
            "story": "你在用 agent 改一个长文件时：侧栏显示上下文已用 92%，你果断收尾当前任务，避免了它后半段开始乱改。",
            "steps": ["在 VS Code 安装插件", "打开一个 agent 会话", "观察侧栏的上下文占用变化"],
        },
        "en": {
            "one_liner": "Shows agent context usage in the VS Code sidebar so limits are visible early.",
            "why": "Agents degrade quietly when context fills up; seeing usage helps you wrap up in time.",
            "story": "Mid-task on a long file, the sidebar hits 92% context: you wrap up the current change instead of letting the agent start improvising.",
            "steps": ["Install the extension in VS Code", "Open an agent session", "Watch context usage in the sidebar"],
        },
        "access": {"zh": "免费 · 需登录", "pricing": "free", "login": True, "platforms": ["Windows", "macOS"]},
    },
]


def _publish_at(day: date) -> datetime:
    tz = get_settings().pipeline.tz
    local = datetime.combine(day, time(9, 0), tzinfo=tz)
    return local.astimezone(timezone.utc)


def _access_json(access: dict, checked_at: str) -> dict:
    return {
        "pricing": access.get("pricing", "unknown"),
        "mobile_support": "unknown",
        "login_required": access.get("login"),
        "api_key_required": access.get("api_key"),
        "waitlist": access.get("waitlist"),
        "self_host_required": access.get("self_host"),
        "platforms": access.get("platforms") or [],
        "setup_minutes": None,
        "checked_at": checked_at,
    }


def seed_normal(session, target: date) -> None:
    settings = get_settings()
    if repo_editions.get_edition_by_date(session, target, settings.pipeline.timezone):
        print(f"{target} 已存在，跳过")
        return
    checked_at = datetime.combine(target, time(7, 0), tzinfo=timezone.utc).isoformat()
    for idx, spec in enumerate(SPECS):
        name = spec["name"]
        slug = name.lower().replace(" ", "-")
        product = repo_products.create_product(
            session, name, f"https://example.com/{slug}", None,
            f"url:https://example.com/{slug}",
            datetime.combine(target, time(6, 0), tzinfo=timezone.utc),
        )
        product.category = spec["category"]
        content = {
            "name": name,
            "category": spec["category"],
            "category_label": spec["label"],
            "one_liner": spec["zh"]["one_liner"],
            "why_interesting": spec["zh"]["why"],
            "user_story": spec["zh"]["story"],
            "mode": spec["mode"],
            "steps": spec["zh"]["steps"],
            "access_text": spec["access"]["zh"],
            "access_json": _access_json(spec["access"], checked_at),
            "time_promise": None,
            "mobile_note": "手机体验未确认",
            "mobile_note_key": "mobile.unknown",
            "evidence_status": "doc_checked",
            "checked_at": checked_at,
            "source_links": [{"label": "来源", "url": f"https://example.com/{slug}/src"}],
            "i18n": {
                "en": {
                    "one_liner": spec["en"]["one_liner"],
                    "why_interesting": spec["en"]["why"],
                    "user_story": spec["en"]["story"],
                    "steps": spec["en"]["steps"],
                }
            },
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
