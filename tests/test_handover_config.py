"""Handover posture: /approve always and /auto on must be switchable off (#25).

Two independent paths reach a real-money send with no human keystroke:
`/approve always` (desk.py, inside the same handle() as the advice turn) and
`/auto on` (engine.py's step loop, no confirm at all). The handed-over desk
(Gil, Inder) must not be able to enable either. Conrad's own desk is
unaffected unless he sets the same keys.

TelegramConfig.allow_approve_always / allow_auto default True: an operator
who never heard of this issue gets the behaviour they have today. The
handover template sets both to false. A value that IS present but cannot be
read as a clean boolean fails closed to False regardless of that default --
the classic env-var string footgun (`bool("false")` is `True` in Python)
must never be able to grant capability, only ever remove it.

A refusal is journaled as `reject` (source=telegram, stage=approve|auto),
following PR #49's field names, and is never echoed into the chat that
asked for it, following PR #40's `command_rejected` precedent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from straightedge.__main__ import main
from straightedge.broker.paper import PaperBroker
from straightedge.config import BotConfig, TelegramConfig, load_config
from straightedge.desk import Desk
from straightedge.engine import Engine
from straightedge.synthetic import generate_bars
from straightedge.telegram import TgCommand


def _engine(tmp_path, *, allow_approve_always=True, allow_auto=True) -> Engine:
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.session.enabled = False
    cfg.risk.max_spread_atr_frac = 10.0
    cfg.risk.halt_file = str(tmp_path / "HALT")
    cfg.telegram.allow_approve_always = allow_approve_always
    cfg.telegram.allow_auto = allow_auto
    broker = PaperBroker(balance=10_000)
    broker.seed_bars("EURUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=3))
    return Engine(
        cfg,
        broker,
        halt_dir=str(tmp_path),
        now_fn=lambda: datetime(2024, 1, 3, 12, tzinfo=timezone.utc),
    )


# --- 1. /approve always is refused under the handover config ---------------


def test_approve_always_refused_when_disabled(tmp_path) -> None:
    engine = _engine(tmp_path, allow_approve_always=False)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/approve always", 1))
    assert "disabled" in reply
    assert engine.desk.approve_always is False
    rec = engine.journal.last_event("reject")
    assert rec is not None, "the refusal left no structured record"
    assert rec["reason"] == "approve_always_disabled"
    assert rec["source"] == "telegram"
    assert rec["stage"] == "approve"
    assert rec["command"] == "approve"
    engine.stop()


def test_approve_on_spelling_also_refused_when_disabled(tmp_path) -> None:
    engine = _engine(tmp_path, allow_approve_always=False)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/approve on", 1))
    assert "disabled" in reply
    assert engine.desk.approve_always is False
    engine.stop()


def test_approve_off_is_never_refused_when_disabled(tmp_path) -> None:
    """Turning the capability OFF must never itself be refused."""
    engine = _engine(tmp_path, allow_approve_always=False)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/approve off", 1))
    assert reply == "approve off. /confirm required"
    assert engine.journal.last_event("reject") is None
    engine.stop()


def test_approve_always_still_stages_order_when_disabled(tmp_path) -> None:
    """The refusal is on arming, not on trading. /confirm still works."""
    engine = _engine(tmp_path, allow_approve_always=False)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/approve always", 1))
    reply = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 2))
    assert "confirm buy EURUSD" in reply
    assert not engine.broker.positions()
    engine.stop()


# --- 2. /auto on is refused under the handover config -----------------------


def test_auto_on_refused_when_disabled(tmp_path) -> None:
    engine = _engine(tmp_path, allow_auto=False)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/auto on", 1))
    assert "disabled" in reply
    assert engine.cfg.strategy.auto is False
    rec = engine.journal.last_event("reject")
    assert rec is not None, "the refusal left no structured record"
    assert rec["reason"] == "auto_disabled"
    assert rec["source"] == "telegram"
    assert rec["stage"] == "auto"
    assert rec["command"] == "auto"
    engine.stop()


def test_auto_off_is_never_refused_when_disabled(tmp_path) -> None:
    engine = _engine(tmp_path, allow_auto=False)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/auto off", 1))
    assert reply == "auto off"
    assert engine.journal.last_event("reject") is None
    engine.stop()


# --- 3. the existing operator path is unchanged when the switch is not set
#        to the handover value (default True, matching today's behaviour) --


def test_approve_always_unchanged_by_default(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    assert engine.cfg.telegram.allow_approve_always is True
    on = engine.handle_command(TgCommand("1", 1, "/approve always", 1))
    assert "approve always" in on
    reply = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 2))
    assert reply.startswith("sent buy")
    engine.stop()


def test_auto_unchanged_by_default(tmp_path) -> None:
    engine = _engine(tmp_path)
    assert engine.cfg.telegram.allow_auto is True
    assert "auto on" in engine.handle_command(TgCommand("1", 1, "/auto on", 1))
    assert engine.cfg.strategy.auto is True


def test_approve_always_explicitly_enabled_is_unchanged(tmp_path) -> None:
    """Not set to the handover (disabling) value at all: explicit True too."""
    engine = _engine(tmp_path, allow_approve_always=True)
    engine.start()
    on = engine.handle_command(TgCommand("1", 1, "/approve always", 1))
    assert "approve always" in on
    engine.stop()


# --- 4. the refusal is journaled, never echoed into the chat ---------------


def test_refusal_does_not_reach_the_chat(tmp_path) -> None:
    from straightedge.telegram import TelegramClient

    class FakeTransport:
        def __init__(self) -> None:
            self.sent: list[tuple[str, dict]] = []

        def post_json(self, url, payload, timeout=10.0, headers=None) -> dict:
            del timeout, headers
            self.sent.append((url, payload))
            if url.endswith("/getUpdates"):
                return {"ok": True, "result": []}
            return {"ok": True, "result": {"message_id": 1}}

    tr = FakeTransport()
    tg = TelegramClient(
        token="t",
        chat_id="1",
        transport=tr,
        notify_events=frozenset({"reject"}),
    )
    engine = _engine(tmp_path, allow_approve_always=False)
    engine.telegram = tg
    engine.desk.engine = engine
    engine.start()
    before = len(tr.sent)
    engine.handle_command(TgCommand("1", 1, "/approve always", 1))
    assert engine.journal.last_event("reject") is not None
    assert len(tr.sent) == before, "the refusal was broadcast into the chat"
    engine.stop()


def test_refusal_reports_on_stderr_when_no_journal_is_configured(capsys) -> None:
    class _JournallessEngine:
        journal = None

        def __init__(self) -> None:
            self.cfg = BotConfig()
            self.cfg.telegram.allow_approve_always = False

        def _live_needs_flag(self) -> bool:
            return False

    desk = Desk(_JournallessEngine())
    reply = desk._approve("always")
    assert "disabled" in reply
    err = capsys.readouterr().err
    assert "reject" in err
    assert "approve_always_disabled" in err


# --- 5. config loading: default, override, and fail-closed on a bad value --


def _write_cfg(tmp_path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_config_defaults_to_allowed_when_key_absent(tmp_path) -> None:
    path = _write_cfg(tmp_path, "[telegram]\ntoken = \"t\"\nchat_id = \"1\"\n")
    cfg = load_config(path)
    assert cfg.telegram.allow_approve_always is True
    assert cfg.telegram.allow_auto is True


def test_load_config_toml_can_disable_both(tmp_path) -> None:
    path = _write_cfg(
        tmp_path,
        "[telegram]\ntoken = \"t\"\nchat_id = \"1\"\n"
        "allow_approve_always = false\nallow_auto = false\n",
    )
    cfg = load_config(path)
    assert cfg.telegram.allow_approve_always is False
    assert cfg.telegram.allow_auto is False


def test_load_config_env_override_disables(tmp_path, monkeypatch) -> None:
    path = _write_cfg(tmp_path, "[telegram]\ntoken = \"t\"\nchat_id = \"1\"\n")
    monkeypatch.setenv("TELEGRAM_ALLOW_APPROVE_ALWAYS", "false")
    monkeypatch.setenv("TELEGRAM_ALLOW_AUTO", "0")
    cfg = load_config(path)
    assert cfg.telegram.allow_approve_always is False
    assert cfg.telegram.allow_auto is False


def test_load_config_unparseable_value_fails_closed(tmp_path) -> None:
    """A garbled value must never be read as the permissive default.

    allow_approve_always defaults True, so a value that cannot be read as a
    clean boolean must resolve to False, not fall through to that default.
    """
    path = _write_cfg(
        tmp_path,
        "[telegram]\ntoken = \"t\"\nchat_id = \"1\"\n"
        "allow_approve_always = \"banana\"\n",
    )
    cfg = load_config(path)
    assert cfg.telegram.allow_approve_always is False


def test_load_config_env_var_string_false_is_not_truthy(tmp_path, monkeypatch) -> None:
    """The concrete footgun: env vars are always strings, and bool("false")
    is True in Python. An operator disabling this via the environment must
    not accidentally re-enable it."""
    path = _write_cfg(tmp_path, "[telegram]\ntoken = \"t\"\nchat_id = \"1\"\n")
    monkeypatch.setenv("TELEGRAM_ALLOW_APPROVE_ALWAYS", "false")
    cfg = load_config(path)
    assert cfg.telegram.allow_approve_always is False


def test_dataclass_default_matches_load_config_default() -> None:
    assert TelegramConfig().allow_approve_always is True
    assert TelegramConfig().allow_auto is True


# --- 6. the shipped handover template sets both false -----------------------


def test_handover_template_disables_both() -> None:
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.handover.toml")
    assert cfg.telegram.allow_approve_always is False
    assert cfg.telegram.allow_auto is False


# --- 7. the posture is observable at startup, not just in config.toml ------
#
# Coordinator follow-up: default True closes the config gate but leaves it
# invisible. A handover that forgets config.handover.toml hands out both
# keystroke-free paths with nothing telling anyone. The fix is NOT a default
# flip (that was the rejected option -- see module docstring); it is making
# the posture observable: printed on `doctor` and `run`, and journaled in
# the `start` record every session already writes. Assert on the structured
# record, not the prose, per the coordinator's instruction.


def test_start_journals_the_posture_when_allowed(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    rec = engine.journal.last_event("start")
    assert rec is not None
    assert rec["approve_always_allowed"] is True
    assert rec["auto_allowed"] is True
    engine.stop()


def test_start_journals_the_posture_when_disabled(tmp_path) -> None:
    engine = _engine(tmp_path, allow_approve_always=False, allow_auto=False)
    engine.start()
    rec = engine.journal.last_event("start")
    assert rec is not None
    assert rec["approve_always_allowed"] is False
    assert rec["auto_allowed"] is False
    engine.stop()


def test_doctor_prints_the_posture_allowed(capsys, tmp_path) -> None:
    # doctor's rc reflects the telegram ping / paper round-trip, not the
    # posture; assert on the posture line only, not the exit code.
    path = _write_cfg(tmp_path, "[telegram]\ntoken = \"t\"\nchat_id = \"1\"\n")
    main(["--config", str(path), "doctor"])
    out = capsys.readouterr().out
    assert "approve always: allowed" in out
    assert "auto: allowed" in out


def test_doctor_prints_the_posture_disabled(capsys, tmp_path) -> None:
    path = _write_cfg(
        tmp_path,
        "[telegram]\ntoken = \"t\"\nchat_id = \"1\"\n"
        "allow_approve_always = false\nallow_auto = false\n",
    )
    main(["--config", str(path), "doctor"])
    out = capsys.readouterr().out
    assert "approve always: disabled" in out
    assert "auto: disabled" in out


def test_doctor_prints_the_handover_template_posture(capsys) -> None:
    import pathlib as _pathlib

    root = _pathlib.Path(__file__).resolve().parents[1]
    assert main(["--config", str(root / "config.handover.toml"), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "approve always: disabled" in out
    assert "auto: disabled" in out


def test_run_prints_the_posture_before_the_telegram_gate(capsys, tmp_path, monkeypatch) -> None:
    """Printed even on the path that then refuses to start for an unrelated
    reason (no Telegram configured): the operator sees the posture on every
    `run`, not only on a successful one."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert main(["run", "--mode", "paper"]) == 2
    out = capsys.readouterr().out
    assert "approve always: allowed" in out
    assert "auto: allowed" in out
