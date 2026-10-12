"""Vibe Radar 运维 CLI（PRD 9.2）。

命令必须显示运行目标、输入窗口与是否外发，返回可识别的退出码。
公开重发、实体合并和正式环境恢复有参数预览；默认 dry-run。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from viberadar import __version__
from viberadar.compose import create_correction, freeze_edition, publish_edition
from viberadar.compose.render import export_site
from viberadar.contracts import IssueStatus
from viberadar.deliver import dispatch_feishu, record_web_delivery, retry_failed_deliveries
from viberadar.feedback import create_issue, resolve_issue
from viberadar.llm import (
    PRESETS,
    LLMClient,
    LLMConfig,
    LLMPrice,
    LLMProfile,
    load_store,
    make_gateway,
    preset_profile,
)
from viberadar.ops import backup as backup_mod
from viberadar.ops import health, review
from viberadar.pipeline import run_pipeline
from viberadar.settings import ConfigError, Settings, get_settings
from viberadar.store import (
    get_session_factory,
    repo_editions,
    repo_feedback,
    repo_ops,
    repo_pipeline,
    repo_products,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_PARTIAL = 3
EXIT_REFUSED = 4


def _outbound_note(outbound: bool) -> str:
    return "将对外发送" if outbound else "不外发（dry-run/本地）"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_date(value: str | None, settings: Settings) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(timezone.utc).astimezone(settings.pipeline.tz).date()


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="viberadar", description="Vibe Radar 运维 CLI（PRD v1.2）"
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="按源采集原始条目")
    p.add_argument("--source", action="append", default=None, help="源 key，可重复")
    p.add_argument("--since", default=None, help="ISO 时间")
    p.add_argument("--until", default=None, help="ISO 时间")
    p.add_argument("--manual-file", default=None, help="人工注入 JSONL 文件")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("pipeline", help="运行每日管线（采集→候选池）")
    p.add_argument("--date", default=None, help="期号日期 YYYY-MM-DD")
    p.add_argument("--cutoff", default=None, help="截点 ISO 时间（默认当日 07:30）")
    p.add_argument("--skip-ingest", action="store_true", help="复用既有原始条目（重放）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--llm", choices=["auto", "config", "mock", "deepseek", "none"], default=None)
    p.add_argument("--manual-file", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("review", help="复核候选（交互或参数模式）")
    p.add_argument("--date", default=None)
    p.add_argument("--list", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--approve", action="append", default=[])
    p.add_argument("--reject", action="append", default=[])
    p.add_argument("--lock", action="append", default=[])
    p.add_argument("--undo", action="append", default=[])
    p.add_argument("--reason", default=None)
    p.add_argument("--operator", default="curator")

    p = sub.add_parser("freeze", help="冻结一期（应用人工修订层）")
    p.add_argument("--date", default=None)
    p.add_argument("--coverage", default=None, help="覆盖缺失提示，如“今日部分信源缺失”")
    p.add_argument("--llm", choices=["auto", "config", "mock", "deepseek", "none"], default=None,
                   help="开场白生成；默认按配置（无配置时模板）")

    p = sub.add_parser("publish", help="公开一期（默认排队到 09:00；--now 立即）")
    p.add_argument("--date", default=None)
    p.add_argument("--now", action="store_true", help="立即公开（显式操作）")
    p.add_argument("--schedule", action="store_true", default=False)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-feishu", action="store_true")

    p = sub.add_parser("deliver", help="投递指定渠道")
    p.add_argument("--date", default=None)
    p.add_argument("--channel", default="feishu")
    p.add_argument("--dispatch-version", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("retry-deliveries", help="仅重试明确失败（retryable_failed）的投递")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("replay", help="按 run 重放（默认 dry-run，不外发）")
    p.add_argument("--run-id", required=True)
    p.add_argument("--from-stage", default="classify")
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--llm", choices=["auto", "config", "mock", "deepseek", "none"], default=None)

    p = sub.add_parser("eval", help="评测（缺少样本时拒绝生成虚假指标）")
    p.add_argument("--dataset-version", required=True)
    p.add_argument("--predictions", default=None)

    p = sub.add_parser("backup", help="备份数据库与公开站点")
    p = sub.add_parser("restore", help="恢复到隔离 staging 目录")
    p.add_argument("--backup-dir", required=True)
    p.add_argument("--staging", required=True)

    p = sub.add_parser("status", help="运行状态")
    p.add_argument("--date", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("serve", help="启动 Web 服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("scheduler", help="启动调度器")
    p.add_argument("--once", action="store_true", help="仅执行当前到期任务一次")

    p = sub.add_parser("merge", help="人工合并两个产品实体")
    p.add_argument("--from", dest="from_id", required=True)
    p.add_argument("--into", dest="into_id", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--operator", default="curator")

    p = sub.add_parser("unmerge", help="撤销一次合并")
    p.add_argument("--action-id", required=True)
    p.add_argument("--operator", default="curator")

    p = sub.add_parser("correct", help="创建期号更正（新 revision）")
    p.add_argument("--date", required=True)
    p.add_argument("--item-id", required=True)
    p.add_argument("--fields", required=True, help="JSON，如 {\"access_text\":\"收费\"}")
    p.add_argument("--reason", required=True)
    p.add_argument("--notify-again", action="store_true")
    p.add_argument("--operator", default="curator")

    p = sub.add_parser("issues", help="内容报错队列")
    p.add_argument("--date", default=None)
    p.add_argument("--resolve", default=None, help="报错 ID")
    p.add_argument("--status", choices=[s.value for s in IssueStatus], default=IssueStatus.CORRECTED.value)
    p.add_argument("--note", default="")
    p.add_argument("--operator", default="curator")

    p = sub.add_parser("inject", help="人工注入候选（JSONL）")
    p.add_argument("--file", required=True)
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)

    p = sub.add_parser("llm", help="LLM 配置窗口（档案/阶段映射/连通性测试）")
    llm_sub = p.add_subparsers(dest="llm_command", required=True)
    llm_sub.add_parser("list", help="列出档案、激活项与阶段映射")
    ps = llm_sub.add_parser("show", help="查看某个档案（密钥掩码）")
    ps.add_argument("--name", default=None)
    ps = llm_sub.add_parser("set", help="新增或更新档案")
    ps.add_argument("--name", required=True)
    ps.add_argument("--preset", default=None, help=f"预设之一：{', '.join(sorted(PRESETS))}")
    ps.add_argument("--label", default=None)
    ps.add_argument("--protocol", choices=["openai", "anthropic"], default=None)
    ps.add_argument("--base-url", default=None)
    ps.add_argument("--api-key", default=None, help="留空则保留原值；新档案不传则视为未设置")
    ps.add_argument("--model-fast", default=None)
    ps.add_argument("--model-strong", default=None)
    ps.add_argument("--temperature", type=float, default=None)
    ps.add_argument("--max-output-tokens", type=int, default=None)
    ps.add_argument("--no-json-mode", dest="json_mode", action="store_false", default=None)
    ps.add_argument("--price-in", type=float, default=None, help="美元/百万 token")
    ps.add_argument("--price-out", type=float, default=None)
    ps.add_argument("--price-cache-in", type=float, default=None)
    ps = llm_sub.add_parser("use", help="设为激活档案")
    ps.add_argument("--name", required=True)
    ps = llm_sub.add_parser("stage", help="设置阶段档位（fast/strong）")
    ps.add_argument("--stage", required=True, choices=["classify", "interest", "enrich", "compose", "api", "default"])
    ps.add_argument("--tier", required=True, choices=["fast", "strong"])
    ps = llm_sub.add_parser("test", help="连通性测试（一次最小调用）")
    ps.add_argument("--name", default=None, help="默认测试激活档案")
    ps = llm_sub.add_parser("remove", help="删除档案")
    ps.add_argument("--name", required=True)
    llm_sub.add_parser("presets", help="列出内置预设")

    return parser


def _cmd_ingest(args, settings: Settings) -> int:
    from viberadar.ingest import run_all_sources

    until = _parse_dt(args.until) or datetime.now(timezone.utc)
    since = _parse_dt(args.since) or until - timedelta(hours=24)
    manual_entries = _load_manual(args.manual_file) if args.manual_file else None
    print(f"采集目标：{args.source or '全部启用源'}；窗口 {since.isoformat()} → {until.isoformat()}；{_outbound_note(False)}")
    factory = get_session_factory()
    with factory() as session:
        reports = run_all_sources(
            session,
            since=since,
            until=until,
            settings=settings,
            only=args.source,
            manual_entries=manual_entries,
        )
        session.commit()
        payload = [
            {
                "source": r.source_key,
                "status": r.status.value,
                "fetched": r.raw_fetched,
                "created": r.created,
                "updated": r.updated,
                "errors": r.errors,
                "coverage": r.coverage_note,
            }
            for r in reports
        ]
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for item in payload:
                print(
                    f"  {item['source']}: {item['status']} 抓取 {item['fetched']}，新建 {item['created']}，更新 {item['updated']}"
                )
                for err in item["errors"]:
                    print(f"    - {err}")
        if any(r.status.value == "failed" for r in reports):
            return EXIT_PARTIAL
    return EXIT_OK


def _load_manual(path: str) -> list[dict]:
    entries = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def _make_llm(settings: Settings, choice: str | None):
    if choice == "none":
        return None
    return make_gateway(get_session_factory(), settings, choice=choice)


def _cmd_pipeline(args, settings: Settings) -> int:
    edition_date = _parse_date(args.date, settings)
    cutoff = _parse_dt(args.cutoff)
    llm = _make_llm(settings, args.llm)
    manual_entries = _load_manual(args.manual_file) if args.manual_file else None
    print(
        f"管线目标：{edition_date}；截点 {cutoff.isoformat() if cutoff else '当日 07:30'}"
        f"；LLM={llm.provider if llm else 'disabled'}；{_outbound_note(False)}"
    )
    factory = get_session_factory()
    with factory() as session:
        try:
            report = run_pipeline(
                session,
                edition_date,
                settings=settings,
                cutoff_at=cutoff,
                llm=llm,
                manual_entries=manual_entries,
                skip_ingest=args.skip_ingest,
                dry_run=args.dry_run,
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(f"管线失败：{exc}", file=sys.stderr)
            return EXIT_ERROR
    payload = {
        "run_id": report.run_id,
        "date": report.edition_date.isoformat(),
        "candidates": report.candidate_count,
        "auto_selected": report.auto_selected,
        "stages": {
            name: {
                "status": stage.status.value,
                "output": stage.output_count,
                "errors": (stage.detail or {}).get("errors"),
            }
            for name, stage in report.stages.items()
        },
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"run_id={report.run_id} 候选 {report.candidate_count} 条，自动入选 {report.auto_selected} 条")
        for name, stage in report.stages.items():
            errors = (stage.detail or {}).get("errors") or []
            print(f"  [{stage.status.value}] {name}: {stage.output_count}{' 错误: ' + '; '.join(map(str, errors[:3])) if errors else ''}")
    ingest_stage = report.stages.get("ingest")
    if ingest_stage and ingest_stage.status.value in ("failed", "partial"):
        return EXIT_PARTIAL
    return EXIT_OK


def _cmd_review(args, settings: Settings) -> int:
    edition_date = _parse_date(args.date, settings)
    factory = get_session_factory()
    with factory() as session:
        if args.approve or args.reject or args.lock or args.undo:
            for product_id in args.approve:
                review.apply_decision(session, edition_date, product_id, args.operator, "approve")
            for product_id in args.reject:
                review.apply_decision(
                    session, edition_date, product_id, args.operator, "reject",
                    reason=args.reason or "未注明",
                )
            for product_id in args.lock:
                review.apply_decision(session, edition_date, product_id, args.operator, "lock", lock=True)
            for product_id in args.undo:
                review.apply_decision(session, edition_date, product_id, args.operator, "undo")
            session.commit()
            print("已保存复核操作。")
            return EXIT_OK
        if args.list or args.json or not sys.stdin.isatty():
            review.print_candidates(session, edition_date, as_json=args.json)
            return EXIT_OK
        result = review.review_interactive(session, edition_date, settings, operator=args.operator)
        if not result.get("within_budget", True):
            print("提示：复核超过 15 分钟预算，优先改进候选质量与信息密度（PRD M5.5）。")
        return EXIT_OK


def _cmd_freeze(args, settings: Settings) -> int:
    edition_date = _parse_date(args.date, settings)
    llm = _make_llm(settings, getattr(args, "llm", None))
    factory = get_session_factory()
    with factory() as session:
        edition = freeze_edition(
            session,
            edition_date,
            settings=settings,
            source_coverage=args.coverage,
            operator="curator",
            llm=llm,
        )
        session.commit()
        print(
            f"已冻结 {edition_date}：状态 {edition.status}，内容状态 {edition.content_status}，"
            f"revision {edition.revision}。如需修改已冻结内容，请使用 correct 创建修订版本。"
        )
    return EXIT_OK


def _cmd_publish(args, settings: Settings) -> int:
    edition_date = _parse_date(args.date, settings)
    factory = get_session_factory()
    with factory() as session:
        edition = repo_editions.get_edition_by_date(
            session, edition_date, settings.pipeline.timezone
        )
        if edition is None:
            print(f"{edition_date} 尚未生成期号；请先运行 pipeline/freeze。", file=sys.stderr)
            return EXIT_ERROR
        if args.schedule or not args.now:
            if edition.status == "draft":
                freeze_edition(
                    session,
                    edition_date,
                    settings=settings,
                    operator="curator",
                    llm=_make_llm(settings, None),
                )
                edition = repo_editions.get_edition_by_date(
                    session, edition_date, settings.pipeline.timezone
                )
            session.commit()
            print(
                f"已确认并排入 09:00 队列：{edition_date}（状态 {edition.status}）。"
                "提前公开需要显式 --now。"
            )
            return EXIT_OK
        # --now：立即公开
        print(f"立即公开目标：{edition_date}；{_outbound_note(not args.dry_run and not args.skip_feishu)}")
        if args.dry_run:
            print("dry-run：不公开、不发送。")
            return EXIT_OK
        publish_edition(session, edition)
        export_error = None
        try:
            export_site(session, settings)
            record_web_delivery(session, edition.id, success=True)
        except Exception as exc:  # noqa: BLE001
            export_error = str(exc)
            record_web_delivery(session, edition.id, success=False, error=export_error)
        session.commit()
        print(f"网页已公开：{settings.site_base_url}/d/{edition_date.isoformat()}")
        if export_error:
            print(f"静态导出失败：{export_error}", file=sys.stderr)
        if not args.skip_feishu:
            result = dispatch_feishu(session, edition, settings=settings)
            session.commit()
            print(f"飞书：{result.status if result else 'unknown'} {result.error or ''}")
            if result and result.status in ("retryable_failed", "unknown", "permanent_failed"):
                return EXIT_PARTIAL
    return EXIT_OK


def _cmd_deliver(args, settings: Settings) -> int:
    edition_date = _parse_date(args.date, settings)
    factory = get_session_factory()
    with factory() as session:
        edition = repo_editions.get_edition_by_date(
            session, edition_date, settings.pipeline.timezone
        )
        if edition is None:
            print(f"{edition_date} 不存在", file=sys.stderr)
            return EXIT_ERROR
        print(
            f"投递目标：{edition_date}；渠道 {args.channel}；{_outbound_note(not args.dry_run)}"
        )
        if args.channel != "feishu":
            print("P0 仅支持 feishu 渠道。", file=sys.stderr)
            return EXIT_REFUSED
        result = dispatch_feishu(
            session,
            edition,
            settings=settings,
            dispatch_version=args.dispatch_version,
            dry_run=args.dry_run,
        )
        session.commit()
        if result is None:
            print("dry-run：未发送。")
            return EXIT_OK
        print(f"结果：{result.status} {result.error or ''}")
        return EXIT_OK if result.status == "sent" else EXIT_PARTIAL


def _cmd_retry(args, settings: Settings) -> int:
    factory = get_session_factory()
    with factory() as session:
        results = retry_failed_deliveries(session, settings=settings, dry_run=args.dry_run)
        session.commit()
        for attempt_id, result in results:
            print(f"{attempt_id}: {result.status if result else 'dry-run'}")
        return EXIT_OK


def _cmd_replay(args, settings: Settings) -> int:
    factory = get_session_factory()
    with factory() as session:
        run = repo_pipeline.get_pipeline_run(session, args.run_id)
        if run is None:
            print(f"run 不存在：{args.run_id}", file=sys.stderr)
            return EXIT_ERROR
        if not args.dry_run and not args.confirm:
            print(
                "重放非 dry-run 需 --confirm 显式确认；默认不外发。",
                file=sys.stderr,
            )
            return EXIT_REFUSED
        edition_date = run.cutoff.astimezone(settings.pipeline.tz).date()
        llm = _make_llm(settings, args.llm)
        print(
            f"重放 run={args.run_id}；沿用原窗口 {run.window_start.isoformat()} → {run.window_end.isoformat()}"
            f"；从 {args.from_stage} 开始；dry_run={args.dry_run}；{_outbound_note(False)}"
        )
        report = run_pipeline(
            session,
            edition_date,
            settings=settings,
            cutoff_at=run.cutoff,
            llm=llm,
            skip_ingest=True,
            dry_run=args.dry_run,
            replay_of=args.run_id,
            owner="replay",
        )
        session.commit()
        print(
            f"重放完成：新 run={report.run_id}，候选 {report.candidate_count}，自动入选 {report.auto_selected}。"
            "已锁定内容未被覆盖。"
        )
    return EXIT_OK


def _cmd_eval(args, settings: Settings) -> int:
    dataset_dir = Path(__file__).resolve().parents[3] / "evals" / args.dataset_version
    required = ["manifest.json", "raw_items.jsonl", "labels.jsonl", "entities.jsonl", "expected_editions.json"]
    missing = [name for name in required if not (dataset_dir / name).exists()]
    if missing:
        print(
            "评测资料不完整：" + ", ".join(missing) + "\n"
            "只提交精选名单而没有全量输入与负例时，拒绝生成虚假的召回率（E25）。",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    print(f"数据集 {args.dataset_version}：{json.dumps(manifest, ensure_ascii=False)[:400]}")
    if not args.predictions:
        print("未提供 --predictions；只校验数据集完整性。")
        return EXIT_OK
    labels = [
        json.loads(line)
        for line in (dataset_dir / "labels.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    predictions = [
        json.loads(line)
        for line in Path(args.predictions).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    truth = {row["item_id"]: bool(row.get("relevant")) for row in labels if row.get("relevant") is not None}
    pred = {row["item_id"]: bool(row.get("relevant")) for row in predictions}
    tp = sum(1 for k, v in truth.items() if v and pred.get(k))
    fp = sum(1 for k, v in truth.items() if not v and pred.get(k))
    fn = sum(1 for k, v in truth.items() if v and not pred.get(k))
    recall = tp / (tp + fn) if (tp + fn) else None
    precision = tp / (tp + fp) if (tp + fp) else None
    result = {
        "sample_size": len(truth),
        "recall": recall,
        "precision": precision,
        "recall_pass": recall is not None and recall >= 0.90,
        "precision_pass": precision is not None and precision >= 0.85,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK if result["recall_pass"] and result["precision_pass"] else EXIT_PARTIAL


def _cmd_backup(args, settings: Settings) -> int:
    result = backup_mod.backup_database(settings)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


def _cmd_restore(args, settings: Settings) -> int:
    result = backup_mod.restore_to_staging(
        settings, Path(args.backup_dir), Path(args.staging)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


def _cmd_status(args, settings: Settings) -> int:
    edition_date = _parse_date(args.date, settings) if args.date else None
    factory = get_session_factory()
    with factory() as session:
        report = health.status_report(session, settings, edition_date)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"时区：{report['timezone']}；调度：{report['schedule']}")
            print("源状态：")
            for source in report["sources"]:
                print(
                    f"  {source['key']}: enabled={source['enabled']} "
                    f"last_success={source['last_success_at']}"
                )
            for edition in report["editions"]:
                print(
                    f"期号 {edition['date']}: {edition['status']}/{edition['content_status']} "
                    f"published={edition['published_at']}"
                )
                for delivery in edition["deliveries"]:
                    print(
                        f"  投递 {delivery['channel']}@{delivery['dispatch_version']}: {delivery['status']} {delivery['error'] or ''}"
                    )
                for stage in edition["stages"]:
                    print(f"  阶段 {stage['stage']}: {stage['status']}")
            if report["delivery_alerts"]:
                print("投递告警：")
                for alert in report["delivery_alerts"]:
                    print(f"  {alert['id']}: {alert['status']} {alert['error']}")
            cost = report["cost"]
            print(
                f"成本：日 ${cost['daily_usd']:.4f}（目标 ≤{cost['daily_optimize_target']}，"
                f"告警 {cost['daily_alert']}，上限 {cost['daily_hard_cap']}）；月 ${cost['monthly_usd']:.4f}"
            )
    return EXIT_OK


def _cmd_serve(args, settings: Settings) -> int:
    import uvicorn

    from viberadar.web.app import create_app

    app = create_app()
    print(f"启动 Web：http://{args.host}:{args.port}（公开站与反馈 API）")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return EXIT_OK


def _cmd_scheduler(args, settings: Settings) -> int:
    from viberadar.jobs import build_scheduler, run_due_jobs

    factory = get_session_factory()
    if args.once:
        executed = run_due_jobs(factory, settings)
        print(f"执行到期任务：{executed or '无'}")
        return EXIT_OK
    scheduler = build_scheduler(settings, factory)
    print("调度器启动（单 worker）。Ctrl+C 退出。")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("调度器已停止。")
    return EXIT_OK


def _resolve_product_id(session, token: str) -> str:
    product = repo_products.get_product(session, token)
    if product is None:
        product = repo_products.get_product_by_slug(session, token)
    if product is None:
        raise KeyError(f"产品不存在：{token}")
    return product.id


def _cmd_merge(args, settings: Settings) -> int:
    factory = get_session_factory()
    with factory() as session:
        from_id = _resolve_product_id(session, args.from_id)
        into_id = _resolve_product_id(session, args.into_id)
        preview = {
            "from": from_id,
            "into": into_id,
            "reason": args.reason,
            "operator": args.operator,
            "note": "合并会移动来源关联并建立旧 slug 301；可用 unmerge 撤销",
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        repo_products.merge_products(
            session,
            from_id,
            into_id,
            reason=args.reason,
            operator=args.operator,
            rule_version="manual-v1",
            now=repo_ops.utcnow(),
        )
        repo_ops.record_audit(
            session, actor=args.operator, action="merge_products",
            target=f"{from_id}->{into_id}", detail=preview,
        )
        session.commit()
        print("已合并。")
    return EXIT_OK


def _cmd_unmerge(args, settings: Settings) -> int:
    factory = get_session_factory()
    with factory() as session:
        repo_products.undo_merge(
            session, args.action_id, operator=args.operator, now=repo_ops.utcnow()
        )
        repo_ops.record_audit(
            session, actor=args.operator, action="undo_merge", target=args.action_id
        )
        session.commit()
        print("已撤销合并，关联与旧 slug 已恢复。")
    return EXIT_OK


def _cmd_correct(args, settings: Settings) -> int:
    edition_date = _parse_date(args.date, settings)
    fields = json.loads(args.fields)
    factory = get_session_factory()
    with factory() as session:
        edition = repo_editions.get_edition_by_date(
            session, edition_date, settings.pipeline.timezone
        )
        if edition is None:
            print(f"{edition_date} 不存在", file=sys.stderr)
            return EXIT_ERROR
        notify = args.notify_again
        if notify and not args.reason:
            print("再次通知必须提供理由。", file=sys.stderr)
            return EXIT_REFUSED
        create_correction(
            session,
            edition,
            changes={args.item_id: fields},
            reason=args.reason,
            operator=args.operator,
            notify_again=notify,
        )
        repo_ops.record_audit(
            session, actor=args.operator, action="correct", target=edition.id,
            detail={"item": args.item_id, "fields": fields, "reason": args.reason},
        )
        session.commit()
        print(
            f"已创建更正 revision {edition.revision}；历史版本保留审计。"
            + ("（需再次通知：将增加 dispatch_version）" if notify else "")
        )
    return EXIT_OK


def _cmd_issues(args, settings: Settings) -> int:
    factory = get_session_factory()
    with factory() as session:
        if args.resolve:
            view = resolve_issue(
                session,
                args.resolve,
                IssueStatus(args.status),
                args.note,
                args.operator,
            )
            session.commit()
            print(f"报错 {view.id} → {view.status.value}")
            return EXIT_OK
        rows = repo_feedback.list_content_issues(session)
        for row in rows:
            print(
                f"{row.id} [{row.status}] {row.issue_type} item={row.edition_item_id} "
                f"{row.note}（{row.submitted_at.isoformat()}）"
            )
        print(f"共 {len(rows)} 条。内容问题当日进入复核；核实后更正或撤下。")
    return EXIT_OK


def _cmd_inject(args, settings: Settings) -> int:
    entries = _load_manual(args.file)
    until = _parse_dt(args.until) or datetime.now(timezone.utc)
    since = _parse_dt(args.since) or until - timedelta(hours=24)
    factory = get_session_factory()
    with factory() as session:
        from viberadar.ingest import run_all_sources

        print(f"人工注入：{len(entries)} 条；窗口 {since.isoformat()} → {until.isoformat()}；{_outbound_note(False)}")
        reports = run_all_sources(
            session,
            since=since,
            until=until,
            settings=settings,
            only=["manual"],
            manual_entries=entries,
        )
        session.commit()
        for report in reports:
            print(
                f"  manual: {report.status.value} 抓取 {report.raw_fetched}，新建 {report.created}"
            )
            for err in report.errors:
                print(f"    - {err}")
    return EXIT_OK


def _llm_store(settings: Settings):
    return load_store(settings)


def _load_config(store) -> LLMConfig:  # noqa: ANN001
    return store.load_optional() or LLMConfig()


def _cmd_llm(args, settings: Settings) -> int:
    store = _llm_store(settings)
    command = args.llm_command

    if command == "presets":
        for key, preset in sorted(PRESETS.items()):
            print(
                f"{key}: {preset['label']} | {preset['protocol']} | {preset['base_url']} | "
                f"{preset['model_fast']} / {preset['model_strong']} | {preset['docs']}"
            )
        return EXIT_OK

    if command == "list":
        config = store.load_optional()
        print(f"配置文件：{store.path}（{'存在' if config else '不存在'}）")
        print(f"provider 解析：{settings.llm_provider}（auto=有档案用档案，否则 mock）")
        if config is None or not config.profiles:
            print("尚无档案。先 `viberadar llm set --name work --preset deepseek --api-key ...` 或在 /admin/llm 配置。")
            return EXIT_OK
        for name in config.unique_names():
            profile = config.profiles[name]
            marker = "*" if name == (config.active or "") else " "
            key_state = profile.masked_key() or "未设置密钥"
            print(
                f"{marker} {name}: {profile.label or profile.protocol} | {profile.base_url} | "
                f"{profile.model_fast or '-'} / {profile.model_strong or '-'} | {key_state}"
            )
        stages = config.stages or {}
        parts = []
        for stage in ("classify", "interest", "enrich", "compose", "api"):
            explicit = stages.get(stage) or stages.get("default")
            if explicit:
                suffix = "" if stages.get(stage) else "(default)"
                parts.append(f"{stage}={explicit}{suffix}")
            else:
                parts.append(f"{stage}=预设")
        print(
            "阶段映射：" + "；".join(parts)
            + "（预设：classify/interest/api=fast，enrich/compose=strong）"
        )
        return EXIT_OK

    if command == "show":
        config = store.load_optional()
        if config is None or not config.profiles:
            print("尚无档案。", file=sys.stderr)
            return EXIT_ERROR
        profile = config.get_profile(args.name) if args.name else config.active_profile()
        print(json.dumps(profile.public_dict(), ensure_ascii=False, indent=2))
        return EXIT_OK

    if command == "set":
        config = _load_config(store)
        existing = config.profiles.get(args.name)
        profile = preset_profile(args.preset, name=args.name) if args.preset else existing
        if profile is None:
            if not args.base_url:
                print("新档案需要 --preset 或 --base-url。", file=sys.stderr)
                return EXIT_REFUSED
            profile = LLMProfile(name=args.name, base_url=args.base_url)
        updates: dict = {}
        for attr in ("label", "protocol", "base_url", "model_fast", "model_strong"):
            value = getattr(args, attr)
            if value is not None:
                updates[attr] = value
        if args.api_key is not None:
            updates["api_key"] = args.api_key
        if args.temperature is not None:
            updates["temperature"] = args.temperature
        if args.max_output_tokens is not None:
            updates["max_output_tokens"] = args.max_output_tokens
        if args.json_mode is not None:
            updates["json_mode"] = args.json_mode
        if any(
            value is not None
            for value in (args.price_in, args.price_out, args.price_cache_in)
        ):
            base_price = profile.price or LLMPrice()
            updates["price"] = base_price.model_copy(
                update={
                    key: value
                    for key, value in (
                        ("input", args.price_in),
                        ("output", args.price_out),
                        ("cache_input", args.price_cache_in),
                    )
                    if value is not None
                }
            )
        try:
            profile = profile.model_copy(update=updates)
        except Exception as exc:  # noqa: BLE001
            print(f"档案校验失败：{exc}", file=sys.stderr)
            return EXIT_CONFIG
        config.profiles[args.name] = profile
        if not config.active:
            config.active = args.name
        try:
            store.save(config)
        except ConfigError as exc:
            print(f"保存失败：{exc}", file=sys.stderr)
            return EXIT_CONFIG
        print(
            f"已保存档案 {args.name}（{profile.protocol} {profile.base_url}；密钥 {profile.masked_key() or '未设置'}）。"
        )
        return EXIT_OK

    if command == "use":
        config = _load_config(store)
        config.get_profile(args.name)
        config.active = args.name
        store.save(config)
        print(f"激活档案：{args.name}")
        return EXIT_OK

    if command == "stage":
        config = _load_config(store)
        config.stages[args.stage] = args.tier
        store.save(config)
        print(f"阶段 {args.stage} → {args.tier}")
        return EXIT_OK

    if command == "remove":
        config = _load_config(store)
        if args.name not in config.profiles:
            print(f"档案不存在：{args.name}", file=sys.stderr)
            return EXIT_ERROR
        config.profiles.pop(args.name)
        if config.active == args.name:
            config.active = next(iter(config.profiles), "")
        store.save(config)
        print(f"已删除档案：{args.name}")
        return EXIT_OK

    if command == "test":
        config = store.load_optional()
        if config is None or not config.profiles:
            print("尚无档案，无法测试。", file=sys.stderr)
            return EXIT_REFUSED
        profile = config.get_profile(args.name) if args.name else config.active_profile()
        if not profile.api_key:
            print(f"档案 {profile.name} 未设置 API key。", file=sys.stderr)
            return EXIT_REFUSED
        client = LLMClient(profile)
        result = client.chat_text(
            system="你是连通性探针，只回复一行结果。",
            user="请只回复：OK",
            model=profile.model_fast or profile.model_strong,
            max_output_tokens=16,
        )
        if result.ok:
            print(
                f"连通成功：{profile.name} → {result.model}；{result.latency_ms}ms；"
                f"回复：{(result.text or '').strip()[:80]}"
            )
            return EXIT_OK
        print(f"连通失败：{result.error}", file=sys.stderr)
        return EXIT_ERROR

    print(f"未知 llm 子命令：{command}", file=sys.stderr)
    return EXIT_ERROR


COMMANDS = {
    "ingest": _cmd_ingest,
    "pipeline": _cmd_pipeline,
    "review": _cmd_review,
    "freeze": _cmd_freeze,
    "publish": _cmd_publish,
    "deliver": _cmd_deliver,
    "retry-deliveries": _cmd_retry,
    "replay": _cmd_replay,
    "eval": _cmd_eval,
    "backup": _cmd_backup,
    "restore": _cmd_restore,
    "status": _cmd_status,
    "serve": _cmd_serve,
    "scheduler": _cmd_scheduler,
    "merge": _cmd_merge,
    "unmerge": _cmd_unmerge,
    "correct": _cmd_correct,
    "issues": _cmd_issues,
    "inject": _cmd_inject,
    "llm": _cmd_llm,
}


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return EXIT_CONFIG
    handler = COMMANDS[args.command]
    try:
        return handler(args, settings)
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001
        print(f"命令失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
