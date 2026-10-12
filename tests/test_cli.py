"""CLI 拒绝路径与可识别退出码（PRD 9.2/E13/E25）。"""

from __future__ import annotations

from viberadar.ops.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_REFUSED,
    build_parser,
    main,
)


def test_eval_refuses_without_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBERADAR_DATA_DIR", str(tmp_path / "data"))
    code = main(["eval", "--dataset-version", "v0-nonexistent"])
    assert code == EXIT_REFUSED


def test_replay_without_confirm_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBERADAR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VIBERADAR_DB_PATH", str(tmp_path / "data" / "viberadar.sqlite3"))
    from viberadar.store import get_engine

    get_engine(str(tmp_path / "data" / "viberadar.sqlite3"), reset=True)
    code = main(["replay", "--run-id", "run_missing", "--no-dry-run"])
    assert code == EXIT_ERROR  # run 不存在先失败


def test_publish_missing_edition_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBERADAR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VIBERADAR_DB_PATH", str(tmp_path / "data" / "viberadar.sqlite3"))
    from viberadar.store import get_engine

    get_engine(str(tmp_path / "data" / "viberadar.sqlite3"), reset=True)
    code = main(["publish", "--date", "2030-01-01"])
    assert code == EXIT_ERROR


def test_ingest_manual_dry_and_deliver_unknown_channel(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("VIBERADAR_DATA_DIR", str(data))
    monkeypatch.setenv("VIBERADAR_DB_PATH", str(data / "viberadar.sqlite3"))
    from viberadar.store import get_engine

    get_engine(str(data / "viberadar.sqlite3"), reset=True)
    manual = tmp_path / "manual.jsonl"
    manual.write_text(
        '{"url": "https://example.com/ok", "title": "OK"}\n'
        '{"url": "http://127.0.0.1/x", "title": "BAD"}\n',
        encoding="utf-8",
    )
    code = main(["inject", "--file", str(manual)])
    assert code == EXIT_OK


def test_parser_commands_exist():
    parser = build_parser()
    args = parser.parse_args(["status", "--json"])
    assert args.command == "status"
    args = parser.parse_args(["pipeline", "--date", "2026-10-12", "--llm", "none"])
    assert args.llm == "none"
