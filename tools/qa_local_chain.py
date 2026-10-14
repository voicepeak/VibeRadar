"""Browser acceptance: refresh -> new card -> verified-pack import -> real interaction.

Use --runtime-only while the user configures a model. Full mode refuses a mock/absent
model and requires the controlled discovery item to be absent before refresh.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import re

import httpx
from playwright.sync_api import sync_playwright, expect

from qa_frontend import find_chrome
from run_local_chain import ROOT
from viberadar.settings import load_settings
from viberadar.store import get_session_factory
from viberadar.store.models import TrialSessionRow, EnrichmentRow, ClassificationRow, RefreshRunRow, CostLedgerRow
from sqlalchemy import select
from viberadar.trial.runtime import Docker, clean_trials


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--runtime-only", action="store_true")
    args = parser.parse_args()
    record = {"scope": "real local browser/API/DB/Docker/HTML conversion",
              "discovery_input": "controlled MarkItDown repository", "runtime_only": args.runtime_only,
              "checks": []}
    output = ROOT / "evidence"
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "playground-image.json").read_text(encoding="utf-8"))
    db_path = Path(json.loads((ROOT / "current-db.json").read_text(encoding="utf-8"))["path"])
    docker = Docker()
    if not args.runtime_only:
        status = httpx.get(args.base+"/api/llm/status", trust_env=False).json()
        if not status["configured"]:
            raise SystemExit("Configure a real model on this acceptance service before testing refresh")
        record["model_active"] = status["active"]
    # Force an actual import, not a warm image reuse. Never force removal or touch another image.
    try:
        docker.call(["image", "rm", manifest["image"]])
    except RuntimeError as exc:
        if "No such image" not in str(exc):
            raise
    session_id = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=find_chrome(), headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
            errors = []
            context.on("page", lambda page: page.on("pageerror", lambda error: errors.append(str(error))))
            page = context.new_page()
            page.goto(args.base)
            if not args.runtime_only:
                expect(page.locator('.item-card[data-product-id]')).to_have_count(0)
                record["checks"].append("feed empty before refresh")
                started = time.perf_counter()
                page.locator('#refresh-form button').click()
                card = page.locator('.item-card').filter(has=page.locator('a[href^="/try/"]')).first
                expect(card).to_be_visible(timeout=180000)
                record["refresh_to_card_seconds"] = round(time.perf_counter()-started, 3)
                record["checks"].append("real refresh produced a new card with a local trial entry")
                verification = get_session_factory(db_path)()
                try:
                    classification = verification.scalar(select(ClassificationRow))
                    enrichment = verification.scalar(select(EnrichmentRow))
                    assert classification.relevant is True and classification.source == 'llm'
                    assert enrichment and enrichment.model_version and enrichment.prompt_version == 'guide-v2'
                    refresh = verification.scalar(select(RefreshRunRow))
                    ledger = list(verification.scalars(select(CostLedgerRow)))
                    record["refresh_run"] = {"id": refresh.id, "status": refresh.status,
                        "new_raw": refresh.new_raw, "new_items": refresh.new_items, "llm_calls": refresh.llm_calls}
                    record["model_usage"] = {"model": enrichment.model_version,
                        "input_tokens": sum(row.input_tokens for row in ledger),
                        "output_tokens": sum(row.output_tokens for row in ledger)}
                    record["checks"].append("real model classification and guide passed task schemas")
                finally:
                    verification.close()
                page.screenshot(path=str(output / "01-new-card.png"), full_page=True)
                card.locator('.actions a[href^="/try/"]').click()
            else:
                page.goto(args.base+"/try/markitdown")
            expect(page.locator('#trial-launch button')).to_be_enabled()
            started = time.perf_counter()
            page.locator('#trial-launch button').click()
            expect(page).to_have_url(re.compile(r"/trial/trl_[a-z0-9]{26}"), timeout=20000)
            session_id = page.url.split('/trial/')[1].split('?')[0]
            expect(page.locator('#trial-open')).to_be_visible(timeout=60000)
            record["click_to_app_link_seconds"] = round(time.perf_counter()-started, 3)
            record["checks"].append("missing image automatically imported and session ready")
            page.screenshot(path=str(output / "02-ready.png"), full_page=True)
            with page.expect_popup() as popup:
                page.locator('#trial-open').click()
            app = popup.value
            app.wait_for_load_state('domcontentloaded')
            app.locator('#html-input').fill('<h1>端到端试玩</h1><ul><li>真实输入</li><li>实际转换</li></ul>')
            app.locator('#convert').click()
            expect(app.locator('#markdown-output')).to_have_value('# 端到端试玩\n\n* 真实输入\n* 实际转换', timeout=15000)
            record["checks"].append("pasted custom HTML converted to expected Markdown")
            app.locator('#file').set_input_files({"name": "sample.html", "mimeType": "text/html",
                "buffer": '<h2>上传验收</h2><p>文件内容确实参与了转换。</p>'.encode()})
            app.locator('#convert').click()
            expect(app.locator('#markdown-output')).to_have_value('## 上传验收\n\n文件内容确实参与了转换。', timeout=15000)
            record["checks"].append("uploaded UTF-8 file converted")
            with app.expect_download() as download:
                app.locator('#download').click()
            path = download.value.path()
            assert Path(path).read_text(encoding='utf-8') == '## 上传验收\n\n文件内容确实参与了转换。'
            record["checks"].append("downloaded Markdown matches actual conversion")
            app.screenshot(path=str(output / "03-real-conversion.png"), full_page=True)
            app.set_viewport_size({"width": 360, "height": 800})
            assert app.evaluate('document.documentElement.scrollWidth <= innerWidth')
            app.screenshot(path=str(output / "04-mobile.png"), full_page=True)
            record["checks"].append("360px app layout has no overflow")
            expect(page.locator('#trial-tried')).to_be_enabled(timeout=35000)
            with page.expect_response(lambda response: response.url.endswith('/tried')) as confirmation:
                page.locator('#trial-tried').click()
            assert confirmation.value.status == 200 and confirmation.value.json()['ok']
            record["checks"].append("manual tried confirmation after actual 30s ready interval")
            page.locator('.trial-actions form button').click()
            expect(page.locator('#trial-state')).to_contain_text('停止', timeout=10000)
            record["checks"].append("reader stopped the trial")
            assert not errors, errors
            record["checks"].append("no browser script errors")
            browser.close()
    finally:
        if session_id:
            settings = load_settings(data_dir=ROOT / "data")
            factory = get_session_factory(db_path)
            clean_trials(factory, settings, docker)
            with factory() as session:
                row = session.get(TrialSessionRow, session_id)
                record["session_id"] = session_id
                record["ready_seconds"] = (row.ready_at-row.started_at).total_seconds() if row.ready_at else None
                record["key_revoked"] = row.key_hash is None
                assert row.status == "stopped" and row.key_hash is None
            assert not docker.call(["ps", "-a", "--filter", "name="+session_id, "--format", "{{.Names}}"])
            assert not docker.call(["network", "ls", "--filter", "name="+session_id+"-net", "--format", "{{.Name}}"])
            assert not (ROOT / "data" / "trials" / session_id).exists()
            record["resources_reclaimed"] = True
        record["passed"] = bool(session_id and "no browser script errors" in record["checks"])
        path = output / ("runtime.json" if args.runtime_only else "full-chain.json")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
