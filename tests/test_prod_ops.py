import os
import select
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from mt5_risk_bot.broker.paper import PaperBroker
from mt5_risk_bot.config import BotConfig, SessionConfig
from mt5_risk_bot.engine import Engine
from mt5_risk_bot.journal import InstanceLock, InstanceLockError, Journal, lock_path_for
from mt5_risk_bot.synthetic import generate_bars


def _cfg(tmp_path: Path) -> BotConfig:
    cfg = BotConfig()
    cfg.session = SessionConfig(enabled=False)
    cfg.symbols = ["EURUSD"]
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.risk.halt_file = str(tmp_path / "HALT")
    return cfg


def test_heartbeat_file_after_step_all_is_0600(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    broker = PaperBroker(balance=10_000)
    broker.seed_bars("EURUSD", generate_bars(80, drift=0.0004, seed=3))
    engine = Engine(cfg, broker, halt_dir=str(tmp_path))
    engine.start()
    dest = Path(cfg.journal_path).with_name("j.heartbeat")
    assert not dest.exists()
    engine.step_all()
    assert dest.is_file()
    assert dest.stat().st_mode & 0o777 == 0o600
    datetime.fromisoformat(dest.read_text(encoding="utf-8").strip())
    dest.unlink()
    engine.halted = True
    engine.step_all()
    assert dest.is_file()
    assert dest.stat().st_mode & 0o777 == 0o600
    engine.stop()


def test_journal_rotates_when_write_would_pass_cap(tmp_path: Path, monkeypatch) -> None:
    import mt5_risk_bot.journal as journal_mod

    monkeypatch.setattr(journal_mod, "_ROTATE_BYTES", 100)
    path = tmp_path / "journal.jsonl"
    j = Journal(path)
    j.write("first", blob="a" * 80)
    rotated = path.with_name(path.name + ".1")
    assert path.is_file()
    assert not rotated.exists()
    j.write("second", blob="b" * 80)
    assert rotated.is_file()
    live = path.read_text(encoding="utf-8")
    old = rotated.read_text(encoding="utf-8")
    assert '"event": "first"' in old
    assert '"event": "second"' in live
    assert '"event": "first"' not in live
    assert [r.get("event") for r in j.tail(20)] == ["second"]
    assert path.stat().st_mode & 0o777 == 0o600
    j.write("third", blob="c" * 80)
    assert '"event": "second"' in rotated.read_text(encoding="utf-8")
    assert '"event": "first"' not in rotated.read_text(encoding="utf-8")
    assert '"event": "third"' in path.read_text(encoding="utf-8")


def _src_pythonpath() -> str:
    src = str(Path(__file__).resolve().parents[1] / "src")
    cur = os.environ.get("PYTHONPATH", "")
    return src if not cur else src + os.pathsep + cur


def test_overlapping_instance_lock_fails_second(tmp_path: Path) -> None:
    # BSD flock allows the same process to re-lock; overlap needs a second process.
    journal = str(tmp_path / "j.jsonl")
    env = os.environ.copy()
    env["PYTHONPATH"] = _src_pythonpath()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from mt5_risk_bot.journal import InstanceLock; import sys, time; "
            "lock = InstanceLock(sys.argv[1]); lock.acquire(); print('held', flush=True); "
            "time.sleep(60)",
            journal,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert holder.stdout is not None
        ready, _, _ = select.select([holder.stdout], [], [], 5)
        if not ready:
            err = holder.stderr.read() if holder.stderr else ""
            raise AssertionError(f"lock holder produced no output: {err}")
        line = holder.stdout.readline().strip()
        if line != "held":
            err = holder.stderr.read() if holder.stderr else ""
            raise AssertionError(f"lock holder failed: {line!r} {err}")
        try:
            InstanceLock(journal).acquire()
        except InstanceLockError as exc:
            assert "already running" in str(exc)
        else:
            raise AssertionError("second acquire must fail")
        lock_file = lock_path_for(journal)
        assert lock_file.is_file()
        assert lock_file.stat().st_mode & 0o777 == 0o600
    finally:
        holder.kill()
        holder.wait(timeout=5)
    after = InstanceLock(journal)
    after.acquire()
    after.release()

