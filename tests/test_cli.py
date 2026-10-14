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
    args = parser.parse_args(["llm", "stage", "--stage", "enrich", "--tier", "strong"])
    assert args.llm_command == "stage" and args.tier == "strong"
    assert parser.parse_args(["feed", "refresh", "--no-llm", "--json"]).no_llm
    assert parser.parse_args(["recipe", "verify", "--file", "x.yaml", "--by", "curator", "--device", "win11"]).recipe_command == "verify"
    assert parser.parse_args(["try", "clean", "--all"]).all


def test_llm_cli_set_use_stage_list_remove(tmp_path, monkeypatch, capsys):
    import json

    config_path = tmp_path / "llm.json"
    monkeypatch.setenv("VIBERADAR_LLM_CONFIG", str(config_path))

    assert main(["llm", "set", "--name", "work", "--preset", "deepseek", "--api-key", "sk-1234567890"]) == EXIT_OK
    assert main(["llm", "use", "--name", "work"]) == EXIT_OK
    assert main(["llm", "stage", "--stage", "enrich", "--tier", "strong"]) == EXIT_OK
    capsys.readouterr()

    assert main(["llm", "list"]) == EXIT_OK
    listed = capsys.readouterr().out
    assert "* work" in listed
    assert "enrich=strong" in listed

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["active"] == "work"
    assert saved["stages"]["enrich"] == "strong"
    assert saved["profiles"]["work"]["api_key"] == "sk-1234567890"
    assert saved["profiles"]["work"]["model_fast"] == "deepseek-chat"

    assert main(["llm", "show", "--name", "work"]) == EXIT_OK
    shown = capsys.readouterr().out
    assert "sk-1234567890" not in shown and "sk-1" in shown

    assert main(["llm", "remove", "--name", "work"]) == EXIT_OK
    assert main(["llm", "show"]) == EXIT_ERROR
