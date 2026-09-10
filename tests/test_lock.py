import fcntl
from pathlib import Path

from mt5_risk_bot.__main__ import build_parser, cmd_run


def test_cmd_run_second_instance_exits_when_lock_held(tmp_path: Path, monkeypatch, capsys) -> None:
    journal = tmp_path / "journal.jsonl"
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[engine]\njournal_path = "{journal.as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    lock_path = tmp_path / "journal.lock"
    args = build_parser().parse_args(["--config", str(cfg), "run", "--mode", "paper"])
    with lock_path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = cmd_run(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "already running" in err
