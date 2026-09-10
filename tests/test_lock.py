from pathlib import Path

from mt5_risk_bot.__main__ import build_parser, cmd_run
from mt5_risk_bot.journal import InstanceLock


def test_cmd_run_second_instance_exits_when_lock_held(tmp_path: Path, monkeypatch, capsys) -> None:
    journal = tmp_path / "journal.jsonl"
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[engine]\njournal_path = "{journal.as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    args = build_parser().parse_args(["--config", str(cfg), "run", "--mode", "paper"])
    held = InstanceLock(journal)
    held.acquire()
    try:
        rc = cmd_run(args)
    finally:
        held.release()
    assert rc == 2
    err = capsys.readouterr().err
    assert "already running" in err
