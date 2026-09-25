"""The heartbeat reader: three states, a derived threshold, and both driven RED.

`journal.heartbeat` was written on every tick since 1.0.0 and read by nothing,
which is the failure this repo keeps finding in a new costume: an instrument
that exists, looks official in the runbook, and cannot report the condition it
was installed for. These tests therefore do two things a smoke test would not.

**They drive the alarm red.** A stale heartbeat, a desk that came back DISARMED
after a restart, a halted desk, a file that predates the format, a file with no
timestamp and a desk with a wrong clock each produce their own named state and
their own exit code. A watchdog nobody has watched go red is not a watchdog.

**They separate the two states an up-or-down check would merge.** The same live
`Engine`, one `live_accepted` field apart, writes a heartbeat that reads
`ALIVE NOT TRADING (live_not_accepted)` and one that reads `ALIVE ARMED`. That
is the whole point: arming is per process on purpose (fc34), so a restart brings
the desk back ticking and refusing to trade, and an operator told only "it is
up" has been told the reassuring half of a two-state answer.

The numeric pins below are arithmetic over constants that live in
`straightedge.telegram` and the config sections, printed on every run. They are
here so that changing `RETRY_CAP_S` or a venue `timeout_ms` default cannot move
the alarm's latency silently: the number an operator was promised in `doctor`
and in the runbook changes in the same diff.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from straightedge import watchdog
from straightedge.broker.mt4_net import NET_GRACE_SEC
from straightedge.broker.paper import PaperBroker
from straightedge.config import BotConfig, SessionConfig, TelegramConfig
from straightedge.engine import Engine
from straightedge.journal import InstanceLock
from straightedge.synthetic import generate_bars
from straightedge.telegram import POLL_TIMEOUT_MARGIN_S, RETRY_CAP_S, RETRY_TRIES, TelegramClient
from test_telegram import FakeTransport
from wincompat import assert_owner_mode

#: One long poll, retried to Telegram's own ceiling: RETRY_TRIES attempts at
#: (poll_seconds + POLL_TIMEOUT_MARGIN_S) each, with RETRY_TRIES - 1 gaps that a
#: `retry_after` can stretch to RETRY_CAP_S. With poll_seconds = 1, the value
#: the example config ships: 4 * 6 + 3 * 60.
CEILING_POLL_1 = 204.0
#: The MT4 steady-state budget is timeout_ms = 5000, and `step_all` spends two
#: commands (ensure_connected, account) before it can write the heartbeat.
MT4_PREFIX = 10.0
#: 204 + 10, doubled for the part of a tick no config value bounds.
MT4_BUDGET = 214
MT4_STALE = 428
#: MT5 ships timeout_ms = 60000, so the same desk shape gets a wider alarm. A
#: single constant could not have been right for both.
MT5_BUDGET = 324
MT5_STALE = 648


def _cfg(tmp_path: Path, *, mode: str = "paper", poll: int = 1, telegram: bool = True) -> BotConfig:
    cfg = BotConfig()
    cfg.mode = mode
    cfg.poll_seconds = poll
    cfg.session = SessionConfig(enabled=False)
    cfg.symbols = ["EURUSD"]
    cfg.journal_path = str(tmp_path / "journal.jsonl")
    cfg.risk.halt_file = str(tmp_path / "HALT")
    if telegram:
        cfg.telegram = TelegramConfig(token="t" * 10, chat_id="42")
    else:
        cfg.telegram = TelegramConfig()
    return cfg


class _RealMoneyPaper(PaperBroker):
    """A paper book that reports `trade_mode=2`.

    The un-stubbable seam this suite needs is the REAL `Engine`, the REAL risk
    gate and the REAL heartbeat file; the only thing faked is the one field that
    makes the account a real-money account, because CI has no live terminal.
    Everything downstream of it -- `circuit_reason`, `_write_heartbeat`,
    `watchdog.decide` -- is the shipped code path.
    """

    def account(self):  # type: ignore[no-untyped-def]
        return replace(super().account(), trade_mode=2)


def _engine(cfg: BotConfig, tmp_path: Path, *, real_money: bool = False) -> Engine:
    broker = _RealMoneyPaper(balance=10_000) if real_money else PaperBroker(balance=10_000)
    broker.seed_bars("EURUSD", generate_bars(80, drift=0.0004, seed=3))
    engine = Engine(cfg, broker, halt_dir=str(tmp_path))
    engine.start()
    return engine


# --- the derived threshold -------------------------------------------------------------


def test_tick_budget_is_derived_per_venue(tmp_path: Path) -> None:
    """The same desk shape gets a different alarm on each venue, from config."""
    mt4 = _cfg(tmp_path, mode="mt4")
    mt5 = _cfg(tmp_path, mode="mt5")
    print(
        f"watchdog: telegram ceiling at poll_seconds=1 = "
        f"{watchdog.telegram_poll_ceiling_seconds(mt4)}/{CEILING_POLL_1}"
    )
    assert watchdog.telegram_poll_ceiling_seconds(mt4) == CEILING_POLL_1
    assert RETRY_TRIES * (1 + POLL_TIMEOUT_MARGIN_S) + (RETRY_TRIES - 1) * RETRY_CAP_S == (
        CEILING_POLL_1
    )
    assert watchdog.venue_timeout_seconds(mt4) * watchdog.VENUE_CALLS_BEFORE_HEARTBEAT == (
        MT4_PREFIX
    )
    print(f"watchdog: mt4 budget {watchdog.tick_budget_seconds(mt4)}/{MT4_BUDGET}")
    print(f"watchdog: mt5 budget {watchdog.tick_budget_seconds(mt5)}/{MT5_BUDGET}")
    assert watchdog.tick_budget_seconds(mt4) == MT4_BUDGET
    assert watchdog.stale_after_seconds(mt4) == MT4_STALE
    assert watchdog.tick_budget_seconds(mt5) == MT5_BUDGET
    assert watchdog.stale_after_seconds(mt5) == MT5_STALE


def test_network_transport_widens_the_budget_by_its_own_grace(tmp_path: Path) -> None:
    """A remote shim adds `NET_GRACE_SEC` per command, so the alarm follows it."""
    cfg = _cfg(tmp_path, mode="mt4")
    cfg.mt4.mailbox_url = "http://127.0.0.1:8730/"
    expected = watchdog.VENUE_CALLS_BEFORE_HEARTBEAT * (5.0 + NET_GRACE_SEC)
    assert watchdog.venue_timeout_seconds(cfg) * 2 == expected
    assert watchdog.tick_budget_seconds(cfg) > MT4_BUDGET


def test_a_quiet_paper_desk_gets_no_venue_term(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, telegram=False)
    assert watchdog.venue_timeout_seconds(cfg) == 0.0
    assert watchdog.telegram_poll_ceiling_seconds(cfg) == 0.0
    # Never zero: a threshold of zero seconds alarms on every healthy desk.
    assert watchdog.tick_budget_seconds(cfg) == 1


def test_poll_seconds_moves_the_threshold(tmp_path: Path) -> None:
    """A 15 second long poll cannot be judged by a 1 second desk's threshold."""
    fast = watchdog.stale_after_seconds(_cfg(tmp_path, mode="mt4", poll=1))
    slow = watchdog.stale_after_seconds(_cfg(tmp_path, mode="mt4", poll=15))
    print(f"watchdog: stale_after poll=1 {fast}s vs poll=15 {slow}s")
    assert slow > fast


# --- the three states, from a live Engine ----------------------------------------------


def test_disarmed_after_restart_is_not_reported_healthy(tmp_path: Path) -> None:
    """State 2. The desk ticks, refuses real money, and SAYS which one it is."""
    cfg = _cfg(tmp_path, mode="mt4")
    engine = _engine(cfg, tmp_path, real_money=True)
    engine.step_all()
    engine.stop()
    path = watchdog.heartbeat_path_for(cfg.journal_path)
    assert path.is_file()
    assert_owner_mode(path)
    report = watchdog.decide(path, now=datetime.now(timezone.utc), cfg=cfg)
    print(f"watchdog: disarmed desk -> {report.state} ({report.reason})")
    assert report.state == watchdog.STATE_NOT_TRADING
    assert report.reason == "live_not_accepted"
    assert report.exit_code == watchdog.EXIT_NOT_TRADING
    assert "I-ACCEPT-RISK" in report.text
    # The remedy is the chat command, never a flag on the scheduled task.
    assert "--i-accept-risk" in report.text and "do NOT" in report.text


def test_armed_desk_reads_armed(tmp_path: Path) -> None:
    """State 3. One field apart from the test above, and a different answer."""
    cfg = _cfg(tmp_path, mode="mt4")
    cfg.live_accepted = True
    engine = _engine(cfg, tmp_path, real_money=True)
    engine.step_all()
    engine.stop()
    path = watchdog.heartbeat_path_for(cfg.journal_path)
    report = watchdog.decide(path, now=datetime.now(timezone.utc), cfg=cfg)
    print(f"watchdog: armed desk -> {report.state}")
    assert report.state == watchdog.STATE_ARMED
    assert report.exit_code == watchdog.EXIT_ARMED
    assert report.ok


def test_a_halted_desk_is_ticking_and_not_trading(tmp_path: Path) -> None:
    """The fourth state the three-state framing leaves out, named not merged."""
    cfg = _cfg(tmp_path, mode="mt4")
    cfg.live_accepted = True
    engine = _engine(cfg, tmp_path, real_money=True)
    Path(cfg.risk.halt_file).write_text("halt\n", encoding="utf-8")
    engine.step_all()
    engine.stop()
    path = watchdog.heartbeat_path_for(cfg.journal_path)
    report = watchdog.decide(path, now=datetime.now(timezone.utc), cfg=cfg)
    print(f"watchdog: halted desk -> {report.state} ({report.reason})")
    assert report.state == watchdog.STATE_NOT_TRADING
    assert report.reason == "halt_file"
    assert report.exit_code == watchdog.EXIT_NOT_TRADING


def test_stale_heartbeat_drives_the_alarm_red(tmp_path: Path) -> None:
    """State 1. The same healthy file, judged one threshold later."""
    cfg = _cfg(tmp_path, mode="mt4")
    cfg.live_accepted = True
    engine = _engine(cfg, tmp_path, real_money=True)
    engine.step_all()
    engine.stop()
    path = watchdog.heartbeat_path_for(cfg.journal_path)
    fresh = watchdog.decide(path, now=datetime.now(timezone.utc), cfg=cfg)
    assert fresh.state == watchdog.STATE_ARMED
    later = datetime.now(timezone.utc) + timedelta(seconds=MT4_STALE + 1)
    report = watchdog.decide(path, now=later, cfg=cfg)
    print(f"watchdog: {MT4_STALE + 1}s later -> {report.state}")
    assert report.state == watchdog.STATE_STALE
    assert report.exit_code == watchdog.EXIT_STALE
    # Named honestly: this check cannot see the process, only the file.
    assert "STALE" in report.text and "reconnect" in report.text


def test_one_second_before_the_threshold_is_still_alive(tmp_path: Path) -> None:
    """The gate is not simply always red: the boundary is the published one."""
    cfg = _cfg(tmp_path, mode="mt4")
    cfg.live_accepted = True
    engine = _engine(cfg, tmp_path, real_money=True)
    engine.step_all()
    engine.stop()
    path = watchdog.heartbeat_path_for(cfg.journal_path)
    just_inside = datetime.now(timezone.utc) + timedelta(seconds=MT4_STALE - 5)
    assert watchdog.decide(path, now=just_inside, cfg=cfg).state == watchdog.STATE_ARMED


# --- what the reader refuses to guess --------------------------------------------------


def test_no_heartbeat_file_is_unknown_not_dead(tmp_path: Path) -> None:
    """A missing file is also a watcher pointed at the wrong path."""
    cfg = _cfg(tmp_path)
    report = watchdog.decide(tmp_path / "nope.heartbeat", now=datetime.now(timezone.utc), cfg=cfg)
    assert report.state == watchdog.STATE_UNKNOWN
    assert report.reason == "no_heartbeat"
    assert report.exit_code == watchdog.EXIT_UNKNOWN
    assert "--config" in report.text


def test_a_heartbeat_with_no_threshold_refuses_to_be_judged(tmp_path: Path) -> None:
    """The old format: an ISO timestamp alone. Derive or refuse (#68)."""
    path = tmp_path / "journal.heartbeat"
    path.write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")
    report = watchdog.decide(path, now=datetime.now(timezone.utc), cfg=_cfg(tmp_path))
    assert report.state == watchdog.STATE_UNKNOWN
    assert report.reason == "no_threshold"
    assert "will not invent one" in report.text


def test_a_heartbeat_with_no_gate_state_is_not_called_armed(tmp_path: Path) -> None:
    path = tmp_path / "journal.heartbeat"
    path.write_text(
        datetime.now(timezone.utc).isoformat() + "\nstale_after_s=400\n", encoding="utf-8"
    )
    report = watchdog.decide(path, now=datetime.now(timezone.utc), cfg=_cfg(tmp_path))
    assert report.state == watchdog.STATE_UNKNOWN
    assert report.reason == "no_gate_state"


def test_an_unparseable_first_line_is_unknown(tmp_path: Path) -> None:
    path = tmp_path / "journal.heartbeat"
    path.write_text("not a timestamp\nblocked=\nstale_after_s=400\n", encoding="utf-8")
    report = watchdog.decide(path, now=datetime.now(timezone.utc), cfg=_cfg(tmp_path))
    assert report.state == watchdog.STATE_UNKNOWN
    assert report.reason == "unparseable"


def test_a_wrong_desk_clock_reads_as_stale_not_as_healthy(tmp_path: Path) -> None:
    """Age is taken from the stamp AND the filesystem, whichever is worse.

    A desk whose clock is wrong is not a healthy desk: `day_key` sets the
    daily-loss budget from that clock. So the two readings are not averaged and
    the reassuring one is not preferred.
    """
    path = tmp_path / "journal.heartbeat"
    old = datetime.now(timezone.utc) - timedelta(seconds=MT4_STALE * 3)
    path.write_text(f"{old.isoformat()}\nblocked=\nstale_after_s={MT4_STALE}\n", encoding="utf-8")
    report = watchdog.decide(path, now=datetime.now(timezone.utc), cfg=_cfg(tmp_path, mode="mt4"))
    assert report.state == watchdog.STATE_STALE


def test_a_config_mismatch_is_reported_not_resolved(tmp_path: Path) -> None:
    """Two configs in play is a real install fault, and it is named."""
    path = tmp_path / "journal.heartbeat"
    now = datetime.now(timezone.utc)
    path.write_text(f"{now.isoformat()}\nblocked=\nstale_after_s=9999\n", encoding="utf-8")
    report = watchdog.decide(path, now=now, cfg=_cfg(tmp_path, mode="mt4"))
    assert report.state == watchdog.STATE_ARMED
    assert "9999s" in report.text and "428s" in report.text


def test_exit_codes_are_distinct() -> None:
    """A caller that can only see 0 or 1 is back to up-or-down."""
    codes = {
        watchdog.EXIT_ARMED,
        watchdog.EXIT_NOT_TRADING,
        watchdog.EXIT_STALE,
        watchdog.EXIT_UNKNOWN,
    }
    print(f"watchdog: distinct exit codes {sorted(codes)}/4")
    assert len(codes) == 4


# --- the file format ------------------------------------------------------------------


def test_first_line_is_still_a_bare_iso_timestamp(tmp_path: Path) -> None:
    """The 1.0.0 contract. Every older reader and doc keeps working."""
    cfg = _cfg(tmp_path)
    engine = _engine(cfg, tmp_path)
    engine.step_all()
    engine.stop()
    text = watchdog.heartbeat_path_for(cfg.journal_path).read_text(encoding="utf-8")
    first = text.splitlines()[0]
    datetime.fromisoformat(first)
    assert "=" not in first
    assert "blocked=" in text and "stale_after_s=" in text


def test_a_gap_over_budget_is_reported_and_the_threshold_is_not_widened(
    tmp_path: Path, monkeypatch
) -> None:
    """The allowance is checked against reality, not trusted.

    The doubling in `stale_after_seconds` covers the part of a tick no config
    value bounds, so it is the one term here that is not a measurement. A desk
    that overruns it says so; it must NOT quietly widen its own alarm, because a
    gate that relaxes itself until it stops firing cannot go red any more.
    """
    import straightedge.engine as engine_mod

    cfg = _cfg(tmp_path, mode="mt4")
    engine = _engine(cfg, tmp_path)
    clock = [0.0]
    monkeypatch.setattr(engine_mod.time, "monotonic", lambda: clock[0])
    engine.step_all()
    clock[0] = float(MT4_BUDGET) * 10
    engine.step_all()
    engine.stop()
    text = watchdog.heartbeat_path_for(cfg.journal_path).read_text(encoding="utf-8")
    assert "over_budget=1" in text
    assert f"stale_after_s={MT4_STALE}" in text
    report = watchdog.decide(
        watchdog.heartbeat_path_for(cfg.journal_path),
        now=datetime.now(timezone.utc),
        cfg=cfg,
    )
    assert "NOT widened" in report.text


# --- the watch loop -------------------------------------------------------------------


def _client() -> tuple[TelegramClient, FakeTransport]:
    transport = FakeTransport()
    client = TelegramClient(token="t" * 10, chat_id="42", transport=transport)
    return client, transport


def test_watch_never_calls_getupdates(tmp_path: Path) -> None:
    """Two processes polling one bot token steal each other's commands.

    `docs/RUNBOOK.md` forbids two loops for exactly this reason, and a watcher
    beside a desk is the obvious way to reintroduce it. The guard is this
    assertion, not the intention of the author.
    """
    cfg = _cfg(tmp_path, mode="mt4")
    engine = _engine(cfg, tmp_path, real_money=True)
    engine.step_all()
    engine.stop()
    client, transport = _client()
    code = watchdog.watch(
        watchdog.heartbeat_path_for(cfg.journal_path),
        cfg,
        send=client.send,
        out=lambda line: None,
        loop=True,
        sleep_fn=lambda seconds: None,
        max_checks=3,
    )
    urls = [url for url, _ in transport.sent]
    print(f"watchdog: {len(urls)} telegram calls, getUpdates in {sum('getUpdates' in u for u in urls)}")
    assert urls, "the watcher sent nothing at all, so this proves nothing"
    assert not any("getUpdates" in url for url in urls)
    assert all(url.endswith("/sendMessage") for url in urls)
    assert code == watchdog.EXIT_NOT_TRADING


def test_watch_tells_the_operator_once_then_on_change(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, mode="mt4")
    engine = _engine(cfg, tmp_path, real_money=True)
    engine.step_all()
    client, transport = _client()
    path = watchdog.heartbeat_path_for(cfg.journal_path)
    watchdog.watch(path, cfg, send=client.send, out=lambda line: None, loop=True,
                   sleep_fn=lambda seconds: None, max_checks=4)
    first = len(transport.sent)
    assert first == 1, f"expected one alert for one unchanging state, got {first}"
    # Now arm it: the state changes, so the operator hears about it again.
    cfg.live_accepted = True
    engine.step_all()
    engine.stop()
    watchdog.watch(path, cfg, send=client.send, out=lambda line: None, loop=True,
                   sleep_fn=lambda seconds: None, max_checks=2)
    assert len(transport.sent) == first + 1
    assert "ALIVE ARMED" in transport.sent[-1][1]["text"]


def test_watch_sleeps_to_the_published_deadline(tmp_path: Path) -> None:
    """No polling interval anybody picked: the desk's own deadline is the wait."""
    cfg = _cfg(tmp_path, mode="mt4")
    engine = _engine(cfg, tmp_path)
    engine.step_all()
    engine.stop()
    slept: list[float] = []
    watchdog.watch(
        watchdog.heartbeat_path_for(cfg.journal_path),
        cfg,
        out=lambda line: None,
        loop=True,
        sleep_fn=slept.append,
        max_checks=2,
    )
    assert slept and slept[0] <= MT4_STALE
    assert slept[0] > MT4_STALE - 30


def test_watch_ok_beat_makes_silence_a_signal(tmp_path: Path) -> None:
    """A dead watcher is silent, and nothing on the box can observe that.

    With `ok_every` set, a healthy desk is confirmed on a cadence, so the
    operator's absence of messages becomes evidence rather than the default.
    Driven on an injected clock: a cadence rule timed by real elapsed
    microseconds is a rule the test cannot actually hold to account.
    """
    cfg = _cfg(tmp_path, mode="mt4")
    cfg.live_accepted = True
    engine = _engine(cfg, tmp_path, real_money=True)
    engine.step_all()
    engine.stop()
    path = watchdog.heartbeat_path_for(cfg.journal_path)

    def _clock(step: int):
        base = datetime.now(timezone.utc)
        ticks = [0]

        def _now() -> datetime:
            value = base + timedelta(seconds=step * ticks[0])
            ticks[0] += 1
            return value

        return _now

    client, transport = _client()
    watchdog.watch(path, cfg, send=client.send, out=lambda line: None, loop=True,
                   sleep_fn=lambda seconds: None, now_fn=_clock(10), ok_every=0.0,
                   max_checks=4)
    quiet = len(transport.sent)
    print(f"watchdog: ok_every off -> {quiet} alert(s) over 4 checks")
    assert quiet == 1

    client, transport = _client()
    watchdog.watch(path, cfg, send=client.send, out=lambda line: None, loop=True,
                   sleep_fn=lambda seconds: None, now_fn=_clock(10), ok_every=10.0,
                   max_checks=4)
    beats = len(transport.sent)
    print(f"watchdog: ok_every=10s on a 10s clock -> {beats} alert(s) over 4 checks")
    assert beats == 4


def test_watch_reports_a_failed_send_instead_of_swallowing_it(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, mode="mt4")
    engine = _engine(cfg, tmp_path)
    engine.step_all()
    engine.stop()
    client, transport = _client()
    transport.fail = True
    lines: list[str] = []
    watchdog.watch(
        watchdog.heartbeat_path_for(cfg.journal_path),
        cfg,
        send=client.send,
        out=lines.append,
        loop=False,
    )
    assert any("Telegram send FAILED" in line for line in lines)


def test_watch_does_not_touch_the_run_lock(tmp_path: Path) -> None:
    """A watchdog that can hold `journal.lock` can make the desk exit 2.

    The desk's second-instance guard is an exclusive lock, so a watcher that
    acquired it for even a moment could make the scheduled task's restart fail
    with `already running`. This drives the real seam: the lock is HELD for the
    whole check, exactly as it is while a desk runs.
    """
    cfg = _cfg(tmp_path, mode="mt4")
    engine = _engine(cfg, tmp_path)
    engine.step_all()
    engine.stop()
    lock = InstanceLock(cfg.journal_path)
    lock.acquire()
    try:
        code = watchdog.watch(
            watchdog.heartbeat_path_for(cfg.journal_path),
            cfg,
            out=lambda line: None,
            loop=False,
        )
    finally:
        lock.release()
    assert code == watchdog.EXIT_ARMED


def test_heartbeat_path_sits_next_to_the_journal(tmp_path: Path) -> None:
    path = watchdog.heartbeat_path_for(tmp_path / "journal.jsonl")
    assert path == tmp_path / "journal.heartbeat"


@pytest.mark.parametrize("blocked", ["daily_loss", "max_drawdown", "state_unwritable"])
def test_every_circuit_reason_survives_the_round_trip(tmp_path: Path, blocked: str) -> None:
    """The reader does not carry a list of reasons it recognises.

    `circuit_reason` can grow a new refusal and this must report it by name
    rather than fall back to "not trading, cause unknown", which is how a
    reason nobody enumerated becomes invisible.
    """
    path = tmp_path / "journal.heartbeat"
    now = datetime.now(timezone.utc)
    path.write_text(
        f"{now.isoformat()}\nblocked={blocked}\nstale_after_s=428\n", encoding="utf-8"
    )
    report = watchdog.decide(path, now=now, cfg=_cfg(tmp_path, mode="mt4"))
    assert report.state == watchdog.STATE_NOT_TRADING
    assert report.reason == blocked
    assert blocked in report.text


# --- the parser's own edges ------------------------------------------------------------


def test_an_empty_heartbeat_is_unknown(tmp_path: Path) -> None:
    """A zero-byte file is what a truncated write leaves behind."""
    path = tmp_path / "journal.heartbeat"
    path.write_text("", encoding="utf-8")
    report = watchdog.decide(path, now=datetime.now(timezone.utc), cfg=_cfg(tmp_path))
    assert report.state == watchdog.STATE_UNKNOWN
    assert report.reason == "unparseable"


def test_a_naive_timestamp_is_read_as_utc_never_as_local(tmp_path: Path) -> None:
    """A local-time reading would move the age by the host's UTC offset.

    Conrad's own seat is US Central, so reading a naive stamp as local time
    would make a healthy desk look five or six hours stale, or a dead one look
    fresh, depending on the sign.
    """
    path = tmp_path / "journal.heartbeat"
    naive = datetime.now(timezone.utc).replace(tzinfo=None)
    path.write_text(f"{naive.isoformat()}\nblocked=\nstale_after_s=428\n", encoding="utf-8")
    hb = watchdog.read(path)
    assert hb is not None and hb.ts is not None and hb.ts.tzinfo is timezone.utc
    report = watchdog.decide(path, now=datetime.now(timezone.utc), cfg=_cfg(tmp_path, mode="mt4"))
    assert report.state == watchdog.STATE_ARMED


def test_a_junk_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "journal.heartbeat"
    now = datetime.now(timezone.utc)
    path.write_text(
        f"{now.isoformat()}\nthis line has no equals sign\nblocked=\nstale_after_s=428\n",
        encoding="utf-8",
    )
    assert watchdog.decide(path, now=now, cfg=_cfg(tmp_path, mode="mt4")).state == (
        watchdog.STATE_ARMED
    )


def test_a_non_numeric_threshold_is_refused_not_coerced(tmp_path: Path) -> None:
    path = tmp_path / "journal.heartbeat"
    now = datetime.now(timezone.utc)
    path.write_text(f"{now.isoformat()}\nblocked=\nstale_after_s=soon\n", encoding="utf-8")
    report = watchdog.decide(path, now=now, cfg=_cfg(tmp_path))
    assert report.state == watchdog.STATE_UNKNOWN
    assert report.reason == "no_threshold"


def test_decide_works_with_no_config_at_all(tmp_path: Path) -> None:
    """The desk's published threshold is enough; the config is a cross-check."""
    path = tmp_path / "journal.heartbeat"
    now = datetime.now(timezone.utc)
    path.write_text(f"{now.isoformat()}\nblocked=\nstale_after_s=428\n", encoding="utf-8")
    report = watchdog.decide(path, now=now)
    assert report.state == watchdog.STATE_ARMED
    assert "NOTE:" not in report.text
    missing = watchdog.decide(tmp_path / "gone.heartbeat", now=now)
    assert missing.state == watchdog.STATE_UNKNOWN


def test_doctor_line_does_not_print_a_number_a_running_desk_cannot_get(
    tmp_path: Path,
) -> None:
    """`doctor` states the threshold, and says when the figure is incomplete.

    With no Telegram token the long poll is absent, so the derived budget
    collapses to its floor. `run` refuses to start without Telegram at all, so
    printing that bare figure would describe a desk that cannot exist.
    """
    from straightedge.__main__ import watchdog_line

    quiet = watchdog_line(_cfg(tmp_path, mode="mt4", telegram=False))
    live = watchdog_line(_cfg(tmp_path, mode="mt4"))
    print(f"watchdog: doctor line, telegram unset -> {quiet}")
    print(f"watchdog: doctor line, telegram set   -> {live}")
    assert "telegram unset" in quiet
    assert "telegram unset" not in live
    assert f"stale after {MT4_STALE}s" in live
