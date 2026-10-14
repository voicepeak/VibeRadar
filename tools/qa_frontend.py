"""前端验收脚本（Playwright）：桌面 + 360px 移动端截图与结构断言（PRD 4.6/M8）。

用法：
    python tools/seed_acceptance.py        # 可选：准备昨天常规刊/前天空刊
    python tools/qa_frontend.py            # 默认 http://127.0.0.1:8000

覆盖：
- 首页分级（重点推荐 → 分组 → 最近期号）与短刊/空刊标识
- 日期页：重点推荐优先、方案/门槛/证据可见、往期回顾只含更早日期
- 空刊页：暂无推荐、不冒充今日新品、历史入口
- 产品页：中文分类/状态、历史期号
- 反馈：卡片评价入口指向反馈页；反馈页保存提示、改选、没玩起来原因展开与提交；收藏 localStorage
- 404（站点版式 + API 统一错误格式）
- 360px：无横向溢出、按钮 ≥44px、正文 ≥16px、无 JS 错误

需要：pip install playwright（使用本机缓存的 Chromium，或 `playwright install chromium`）。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

GOTO_KW = {"wait_until": "domcontentloaded", "timeout": 20000}
results: list[dict] = []
TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
DAY_BEFORE = TODAY - timedelta(days=2)


def find_chrome() -> str | None:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    candidates = sorted(
        glob.glob(str(Path(local) / "ms-playwright" / "chromium-*" / "chrome-win64" / "chrome.exe"))
    )
    return candidates[-1] if candidates else None


def check(name: str, ok: bool, detail: object = "") -> None:
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"→ {detail}")


def goto(page, url: str, attempts: int = 3):  # noqa: ANN001
    last = None
    for _ in range(attempts):
        try:
            return page.goto(url, **GOTO_KW)
        except Exception as exc:  # noqa: BLE001
            last = exc
            page.wait_for_timeout(600)
    raise last


def metrics(page):  # noqa: ANN001
    return page.evaluate(
        """() => {
            const de = document.documentElement;
            const btns = [...document.querySelectorAll('.btn')]
                .filter(b => b.getBoundingClientRect().height > 0)
                .map(b => b.getBoundingClientRect().height);
            return {
                scrollWidth: de.scrollWidth,
                innerWidth: window.innerWidth,
                bodyFont: parseFloat(getComputedStyle(document.body).fontSize),
                minBtn: btns.length ? Math.min(...btns) : null,
            };
        }"""
    )


def assert_common(page, label: str) -> None:  # noqa: ANN001
    m = metrics(page)
    check(f"{label}: 无横向溢出", m["scrollWidth"] <= m["innerWidth"] + 1,
          f"scrollWidth={m['scrollWidth']} innerWidth={m['innerWidth']}")
    if m["minBtn"] is not None:
        check(f"{label}: 按钮高度 ≥44px", m["minBtn"] >= 43.5, f"minBtn={m['minBtn']}")
    check(f"{label}: 正文字号 ≥16px", m["bodyFont"] >= 16, m["bodyFont"])


def fetch_edition(page, day: date, lang: str | None = None):  # noqa: ANN001
    try:
        url = f"/api/v1/editions/{day.isoformat()}"
        if lang:
            url += f"?lang={lang}"
        return page.evaluate(
            """(u) => fetch(u, {cache: 'no-store'}).then(r => r.ok ? r.json() : null)""",
            url,
        )
    except Exception:  # noqa: BLE001
        return None


def run(base: str) -> int:
    out = Path("logs") / "qa"
    results.clear()
    out.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    launch_kw = {"executable_path": chrome} if chrome else {}

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kw)
        desktop = browser.new_context(viewport={"width": 1280, "height": 900}, locale="zh-CN")
        page = desktop.new_page()
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

        # ---- 首页 ----
        goto(page, f"{base}/")
        page.wait_for_timeout(1200)
        body = page.inner_text("body")
        check("首页：四个 Feed 栏目", all(x in body for x in ("重要必看", "近期火爆", "最新发现", "全部动态")))
        check(
            "首页：有内容或有说明",
            ("下一件好工具" in body) or ("暂时没有符合条件" in body),
        )
        feed_data = page.evaluate("() => fetch('/api/v1/feed').then(r => r.json())")
        cards = page.locator(".item-card")
        ids = cards.evaluate_all("els => els.map(e => e.dataset.itemId)")
        check("Feed：卡片去重", len(ids) == len(set(ids)))
        check("Feed：刷新可无 JS 提交", page.locator('form[action="/refresh"] button[type="submit"]').count() == 1)
        check("Feed：指南默认折叠", page.locator('.card-details[open]').count() == 0)
        if cards.count():
            page.locator('.card-details summary').first.click()
            check("Feed：指南可展开", page.locator('.card-details[open]').count() == 1)
            page.locator('.card-details summary').first.click()
        page.locator('.archive-disclosure summary').click()
        if fetch_edition(page, YESTERDAY):
            check("首页：最近期号入口", "最近期号" in body)
            check("首页：展示昨日期号", page.locator(f'.recent-list a[href="/d/{YESTERDAY.isoformat()}"]').count() == 1)
        else:
            print("SKIP 首页：最近期号（未准备昨日数据，先运行 tools/seed_acceptance.py）")
        assert_common(page, "首页桌面")
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(out / "01-home-desktop.png"), full_page=True)

        # ---- 语言切换（中/英纯语言检查）----
        goto(page, f"{base}/?lang=en")
        page.wait_for_timeout(900)
        en_body = page.inner_text("body")
        check(
            "英文模式：界面切换",
            "Must read" in en_body and "New discoveries" in en_body,
        )
        check(
            "英文模式：无中文界面文案",
            "用户故事" not in en_body and "为什么值得看" not in en_body and "重点推荐" not in en_body,
        )
        page.screenshot(path=str(out / "01b-home-en.png"), full_page=True)

        en_edition = fetch_edition(page, YESTERDAY, lang="en")
        if en_edition and en_edition.get("items"):
            goto(page, f"{base}/d/{YESTERDAY.isoformat()}?lang=en")
            page.wait_for_timeout(700)
            day_en = page.inner_text("body")
            check("英文模式：日期页无中文", "用户故事" not in day_en and "往期回顾" not in day_en)
            check("英文模式：用户故事区块", "User story:" in day_en)
            stories = [item.get("user_story", "") for item in en_edition["items"]]
            check(
                "英文模式：英文推荐理由",
                any(
                    story.strip() and all(not ("\u4e00" <= ch <= "\u9fff") for ch in story)
                    for story in stories
                ),
                stories,
            )
            page.click('a[hreflang="zh-CN"]')
            page.wait_for_timeout(700)
            back_zh = page.inner_text("body")
            check("语言切换：返回中文", "用户故事：" in back_zh or "重点推荐" in back_zh)
            check("语言切换：cookie 记忆", page.evaluate("document.cookie.includes('vr_lang=zh')"))

        today_edition = fetch_edition(page, TODAY)
        yesterday_edition = fetch_edition(page, YESTERDAY)
        day_before_edition = fetch_edition(page, DAY_BEFORE)

        # ---- 今日期号（短刊或常规）----
        if today_edition:
            goto(page, f"{base}/d/{TODAY.isoformat()}")
            page.wait_for_timeout(800)
            body = page.inner_text("body")
            items = today_edition.get("items") or []
            if today_edition.get("content_status") == "short":
                check("今日短刊：标识", "短刊" in body)
            if items:
                check("今日：重点推荐优先", body.find("重点推荐") < body.find("工具与工作流") if "工具与工作流" in body else True)
                check(
                    "卡片：评价入口指向反馈页",
                    page.locator('a[href^="/feedback/"]').count() > 0,
                )
                border = page.evaluate(
                    """() => {
                        const h = document.querySelector('.item-card.highlight');
                        const n = document.querySelector('.item-card:not(.highlight)');
                        if (!h || !n) return null;
                        return {h: parseFloat(getComputedStyle(h).borderLeftWidth),
                                n: parseFloat(getComputedStyle(n).borderLeftWidth)};
                    }"""
                )
                if border:
                    check("分级：重点卡强调边框", border["h"] > border["n"], border)
            page.screenshot(path=str(out / "02-today-desktop.png"), full_page=True)

        # ---- 昨日常规刊（如已准备）----
        if yesterday_edition and yesterday_edition.get("content_status") == "normal":
            goto(page, f"{base}/d/{YESTERDAY.isoformat()}")
            page.wait_for_timeout(800)
            body = page.inner_text("body")
            check("常规刊：重点推荐 + 分组", "重点推荐" in body and ("工具与工作流" in body or "创意与实验" in body))
            check("常规刊：方案标签中文", "直接试玩" in body or "看演示" in body or "安装体验" in body)
            check("常规刊：门槛可见", "免费" in body and ("需登录" in body or "需本地安装" in body))
            check("常规刊：往期不含更新期号", page.locator(f'.recent-list a[href="/d/{TODAY.isoformat()}"]').count() == 0)
            page.screenshot(path=str(out / "03-yesterday-desktop.png"), full_page=True)
        else:
            print("SKIP 常规刊检查（未准备昨日常规刊，先运行 tools/seed_acceptance.py）")

        # ---- 前天空刊（如已准备）----
        if day_before_edition and day_before_edition.get("content_status") == "empty":
            goto(page, f"{base}/d/{DAY_BEFORE.isoformat()}")
            page.wait_for_timeout(600)
            body = page.inner_text("body")
            check("空刊页：暂无推荐文案", "今日暂无通过核验的推荐" in body or "暂无通过核验的推荐" in body)
            check("空刊页：不冒充今日新品", "未用旧产品冒充今日新品" in body)
            check(
                "空刊页：历史入口说明",
                ("往期回顾" in body) or ("暂时没有可回顾的往期" in body),
            )
            page.screenshot(path=str(out / "04-empty-desktop.png"), full_page=True)
        else:
            print("SKIP 空刊检查（未准备前天空刊，先运行 tools/seed_acceptance.py）")

        # ---- 产品页 ----
        source_edition = yesterday_edition or today_edition
        if source_edition and source_edition.get("items"):
            slug = source_edition["items"][0]["slug"]
            goto(page, f"{base}/p/{slug}")
            page.wait_for_timeout(500)
            body = page.inner_text("body")
            check("产品页：历史期号区块", "历史期号" in body)
            check("产品页：状态中文", "有效" in body or "已合并" in body or "已撤回" in body)
            page.screenshot(path=str(out / "05-product-desktop.png"), full_page=True)

        # ---- 反馈交互 ----
        item_id = None
        if source_edition and source_edition.get("items"):
            item_id = source_edition["items"][0]["id"]
        if item_id:
            goto(page, f"{base}/feedback/{item_id}")
            page.wait_for_timeout(500)
            check(
                "反馈页：原因区块默认隐藏",
                not page.locator(f'[data-reasons-for="{item_id}"]').first.is_visible(),
            )
            page.click('button[data-action="tried"]')
            page.wait_for_timeout(700)
            status = page.inner_text(f'[data-status-for="{item_id}"]')
            check("反馈页：保存成功提示", "已记录" in status, status)
            page.click('button[data-action="try-failed"]')
            page.wait_for_timeout(700)
            check(
                "反馈页：没玩起来时展开原因选择",
                page.locator(f'[data-reasons-for="{item_id}"]').first.is_visible(),
            )
            page.select_option(f'[data-reason-select="{item_id}"]', "entry_dead")
            page.click(f'button[data-action="submit-fail"][data-item-id="{item_id}"]')
            page.wait_for_timeout(700)
            status = page.inner_text(f'[data-status-for="{item_id}"]')
            check("反馈页：保存失败原因", "没玩起来" in status, status)
            page.screenshot(path=str(out / "06-feedback-desktop.png"), full_page=True)

        # ---- 收藏 ----
        if source_edition and source_edition.get("items"):
            goto(page, f"{base}/d/{source_edition['date']}")
            page.wait_for_timeout(500)
            page.locator("button[data-action='save']").first.click()
            page.wait_for_timeout(400)
            saved = page.evaluate("JSON.parse(localStorage.getItem('vr_saved') || '[]')")
            check("收藏：写入 localStorage", len(saved) == 1 and "product_id" in saved[0], saved)
            goto(page, f"{base}/saved")
            page.wait_for_timeout(600)
            check("收藏页：渲染收藏条目", source_edition["items"][0]["name"] in page.inner_text("body"))
            page.screenshot(path=str(out / "07-saved-desktop.png"), full_page=True)

        # ---- 404 ----
        goto(page, f"{base}/no-such-page")
        check("404 页面：站点版式", "未找到" in page.inner_text("body"))
        api404 = page.evaluate("""() => fetch('/api/v1/nope').then(r => r.json())""")
        check(
            "API 404：统一错误格式",
            api404.get("code") == "not_found" and "request_id" in api404 and "retryable" in api404,
            api404,
        )
        real_errors = [e for e in errors if "Failed to load resource" not in e]
        check("控制台无 JS 错误", not real_errors, real_errors[:3])
        desktop.close()

        # ---- 移动端 360px ----
        mobile = browser.new_context(viewport={"width": 360, "height": 760}, locale="zh-CN")
        mp = mobile.new_page()
        m_errors: list[str] = []
        mp.on("console", lambda msg: m_errors.append(msg.text) if msg.type == "error" else None)
        mobile_pages = [("home", "/"), ("saved", "/saved")]
        if today_edition:
            mobile_pages.append(("today", f"/d/{TODAY.isoformat()}"))
        if yesterday_edition:
            mobile_pages.append(("yesterday", f"/d/{YESTERDAY.isoformat()}"))
        if day_before_edition:
            mobile_pages.append(("empty", f"/d/{DAY_BEFORE.isoformat()}"))
        if item_id:
            mobile_pages.append(("feedback", f"/feedback/{item_id}"))
        for slug, path in mobile_pages:
            goto(mp, base + path)
            mp.wait_for_timeout(700)
            assert_common(mp, f"移动 {path}")
            mp.screenshot(path=str(out / f"10-{slug}-mobile.png"), full_page=True)
        real_m = [e for e in m_errors if "Failed to load resource" not in e]
        check("移动端控制台无 JS 错误", not real_m, real_m[:3])
        mobile.close()
        nojs = browser.new_context(viewport={"width": 360, "height": 760}, java_script_enabled=False)
        np = nojs.new_page()
        goto(np, base + "/")
        check("无 JS：首页栏目可读", "重要必看" in np.inner_text("body"))
        check("无 JS：刷新表单可用", np.locator('form[action="/refresh"]').count() == 1)
        np.screenshot(path=str(out / "11-home-nojs.png"), full_page=True)
        nojs.close()
        browser.close()

    failed = [r for r in results if not r["ok"]]
    print(f"\n结果：{len(results) - len(failed)}/{len(results)} 通过；截图在 {out}/")
    if failed:
        print("失败项：", json.dumps(failed, ensure_ascii=False, indent=1))
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    return run(args.base)


if __name__ == "__main__":
    sys.exit(main())
