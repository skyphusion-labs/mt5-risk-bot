"""Issue #13: the model picks the symbol, and nothing bounds how often.

Two independent holes, and they are not the same kind of control.

WHITELIST. `cfg.symbols` is a SCAN list: it drives `/quote` and the auto scan.
`market_signal` accepts anything the broker knows, so with `/approve always` the
model chose the instrument and no gate checked it against anything.

DAILY CAP. Churn was bounded only by `max_positions` plus `daily_loss_pct`, and
`daily_loss_pct` fires after the money is gone. It is also a COST control: the
hosted inference is billed per turn, and an advice turn costs money whether or
not it ends in an order, so a cap that counts only sends cannot see the spend.
Two caps, named separately, because they bound two different things.

Both refusals use the structured shape from #49: `reject` via `journal.write`
with a NAMED reason, never `_emit`, and never echoed back to the chat that
triggered it.
"""

import json
from datetime import datetime, timedelta, timezone

from test_refusal_journal import FakeLlm, _advice_payload, _engine

from mt5_risk_bot.synthetic import generate_bars
from mt5_risk_bot.telegram import TgCommand

ASK = "/ask what should I do"


def _with_gold(engine):
    """The broker knows XAUUSD; the question is whether the desk allows it."""
    engine.broker.seed_bars("XAUUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=5))
    return engine


def _rejects(engine, reason: str) -> list[dict]:
    out = []
    for line in engine.journal.tail(200):
        if line.get("event") == "reject" and line.get("reason") == reason:
            out.append(line)
    return out


# --------------------------------------------------------------------------
# Whitelist: the model may only open what the operator listed
# --------------------------------------------------------------------------


def test_advice_cannot_stage_a_symbol_outside_the_book(tmp_path) -> None:
    engine = _with_gold(_engine(tmp_path, llm=FakeLlm(_advice_payload(symbol="XAUUSD"))))
    engine.cfg.symbols = ["EURUSD"]
    engine.start()
    assert "XAUUSD" not in engine.cfg.symbols
    reply = engine.handle_command(TgCommand("1", 1, ASK, 1))
    rec = engine.journal.last_event("reject")
    assert rec is not None, "the model picked an unlisted symbol and nothing recorded it"
    assert rec["reason"] == "symbol_not_allowed"
    assert rec["source"] == "advice"
    assert rec["symbol"] == "XAUUSD"
    turn = engine.journal.last_event("advice_turn")
    assert turn is not None and turn["staged"] is False
    assert "XAUUSD" not in reply or "not" in reply.lower()
    engine.stop()


def test_advice_can_stage_a_symbol_in_the_book(tmp_path) -> None:
    """Positive control. A whitelist that refuses everything is not a whitelist."""
    engine = _engine(tmp_path, llm=FakeLlm(_advice_payload(symbol="EURUSD")))
    engine.start()
    engine.handle_command(TgCommand("1", 1, ASK, 1))
    assert _rejects(engine, "symbol_not_allowed") == []
    turn = engine.journal.last_event("advice_turn")
    assert turn is not None and turn["staged"] is True
    engine.stop()


def test_advice_symbols_is_a_separate_knob_from_the_scan_list(tmp_path) -> None:
    """An operator may scan three pairs and let the model range over others.

    `advice_symbols` empty means "use the scan list". It never means "allow
    anything": an empty whitelist that permits everything is the defect.
    """
    engine = _with_gold(_engine(tmp_path, llm=FakeLlm(_advice_payload(symbol="XAUUSD"))))
    engine.cfg.advice_symbols = ["XAUUSD"]
    engine.start()
    engine.handle_command(TgCommand("1", 1, ASK, 1))
    assert _rejects(engine, "symbol_not_allowed") == []
    engine.stop()


def test_a_manual_order_is_not_constrained_by_the_advice_whitelist(tmp_path) -> None:
    """The operator typing /buy XAUUSD chose it themselves.

    The whitelist exists because the MODEL chose the instrument. Applying it to
    a human's explicit command would be a different control with a different
    justification, and it would break the case the scan list already allows for.
    """
    engine = _with_gold(_engine(tmp_path))
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/buy XAUUSD", 1))
    assert _rejects(engine, "symbol_not_allowed") == []
    assert "refused: symbol_not_allowed" not in reply
    engine.stop()


def test_advice_may_always_close_a_symbol_outside_the_book(tmp_path) -> None:
    """The whitelist gates OPENING only.

    Blocking a close on an unlisted symbol would trap exposure the desk already
    has. A control that can prevent you reducing risk is not a risk control.
    """
    engine = _with_gold(
        _engine(tmp_path, llm=FakeLlm(_advice_payload(action="close", symbol="XAUUSD", ticket=1)))
    )
    engine.start()
    engine.handle_command(TgCommand("1", 1, ASK, 1))
    assert _rejects(engine, "symbol_not_allowed") == []
    engine.stop()


# --------------------------------------------------------------------------
# Daily send cap: bounds churn, and survives a restart
# --------------------------------------------------------------------------


def _send(engine, n: int) -> str:
    """Both legs joined.

    The cap refuses at the STAGE gate, because `preview` runs the same
    evaluate() the confirm leg does. Reading only the confirm reply would show
    "nothing to confirm" and hide which gate actually said no.
    """
    staged = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", n))
    confirmed = engine.handle_command(TgCommand("1", 1, "/confirm", n + 100))
    return staged + " | " + confirmed


def test_the_daily_send_cap_refuses_by_name(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.cfg.risk.max_trades_per_day = 1
    engine.cfg.risk.max_positions = 9
    engine.start()
    first = _send(engine, 1)
    assert "sent" in first.lower(), first
    assert "max_trades_per_day" not in first, first
    second = _send(engine, 2)
    assert "max_trades_per_day" in second, second
    rec = engine.journal.last_event("reject")
    assert rec is not None
    assert rec["reason"] == "max_trades_per_day"
    engine.stop()


def test_zero_means_no_send_cap(tmp_path) -> None:
    """Positive control on the gate itself."""
    engine = _engine(tmp_path)
    engine.cfg.risk.max_trades_per_day = 0
    engine.cfg.risk.max_positions = 9
    engine.start()
    _send(engine, 1)
    _send(engine, 2)
    assert _rejects(engine, "max_trades_per_day") == []
    engine.stop()


def test_the_send_cap_is_not_reset_by_a_restart(tmp_path) -> None:
    """A cap a crash loop can clear is not a cap.

    Same reasoning as the daily loss budget in 1.1.3: the count is persisted
    beside the journal and restored, so restarting inside the same UTC day does
    not hand out a fresh allowance.
    """
    engine = _engine(tmp_path)
    engine.cfg.risk.max_trades_per_day = 1
    engine.cfg.risk.max_positions = 9
    engine.start()
    _send(engine, 1)
    engine.stop()

    again = _engine(tmp_path)
    again.cfg.risk.max_trades_per_day = 1
    again.cfg.risk.max_positions = 9
    again.start()
    reply = _send(again, 2)
    assert "max_trades_per_day" in reply, reply
    again.stop()


def test_the_send_cap_resets_on_a_new_utc_day(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.cfg.risk.max_trades_per_day = 1
    engine.cfg.risk.max_positions = 9
    engine.start()
    _send(engine, 1)
    engine.stop()

    tomorrow = _engine(tmp_path)
    tomorrow.cfg.risk.max_trades_per_day = 1
    tomorrow.cfg.risk.max_positions = 9
    tomorrow.now_fn = lambda: datetime(2024, 1, 3, 12, tzinfo=timezone.utc) + timedelta(days=1)
    tomorrow.start()
    reply = _send(tomorrow, 2)
    assert "max_trades_per_day" not in reply, reply
    tomorrow.stop()


# --------------------------------------------------------------------------
# Daily advice cap: bounds the bill, which the send cap cannot see
# --------------------------------------------------------------------------


class CountingLlm(FakeLlm):
    def __init__(self, payload) -> None:
        super().__init__(payload)
        self.calls = 0

    def post_json(self, url, payload, timeout=10.0, headers=None) -> dict:
        self.calls += 1
        return super().post_json(url, payload, timeout, headers)


def test_the_advice_cap_refuses_before_the_provider_is_billed(tmp_path) -> None:
    """The refusal has to land BEFORE the call, or the cap costs what it saves."""
    llm = CountingLlm(_advice_payload(symbol="EURUSD"))
    engine = _engine(tmp_path, llm=llm)
    engine.cfg.advice.max_turns_per_day = 1
    engine.start()
    engine.handle_command(TgCommand("1", 1, ASK, 1))
    assert llm.calls == 1
    engine.handle_command(TgCommand("1", 1, ASK, 2))
    assert llm.calls == 1, "the provider was called after the cap was reached"
    rec = engine.journal.last_event("reject")
    assert rec is not None
    assert rec["reason"] == "max_advice_turns_per_day"
    assert rec["source"] == "advice"
    engine.stop()


def test_an_advice_turn_that_produces_no_order_still_counts(tmp_path) -> None:
    """The whole reason there are two caps.

    A `hold` costs exactly as much inference as a `buy` and produces no send, so
    a send-only cap cannot see it at all.
    """
    llm = CountingLlm(_advice_payload(action="hold", symbol=""))
    engine = _engine(tmp_path, llm=llm)
    engine.cfg.advice.max_turns_per_day = 1
    engine.start()
    engine.handle_command(TgCommand("1", 1, ASK, 1))
    engine.handle_command(TgCommand("1", 1, ASK, 2))
    assert llm.calls == 1
    assert _rejects(engine, "max_advice_turns_per_day"), "a no-order turn was not counted"
    engine.stop()


def test_zero_means_no_advice_cap(tmp_path) -> None:
    llm = CountingLlm(_advice_payload(action="hold", symbol=""))
    engine = _engine(tmp_path, llm=llm)
    engine.cfg.advice.max_turns_per_day = 0
    engine.start()
    engine.handle_command(TgCommand("1", 1, ASK, 1))
    engine.handle_command(TgCommand("1", 1, ASK, 2))
    assert llm.calls == 2
    assert _rejects(engine, "max_advice_turns_per_day") == []
    engine.stop()


def test_the_advice_cap_is_not_reset_by_a_restart(tmp_path) -> None:
    llm = CountingLlm(_advice_payload(action="hold", symbol=""))
    engine = _engine(tmp_path, llm=llm)
    engine.cfg.advice.max_turns_per_day = 1
    engine.start()
    engine.handle_command(TgCommand("1", 1, ASK, 1))
    engine.stop()

    llm2 = CountingLlm(_advice_payload(action="hold", symbol=""))
    again = _engine(tmp_path, llm=llm2)
    again.cfg.advice.max_turns_per_day = 1
    again.start()
    again.handle_command(TgCommand("1", 1, ASK, 2))
    assert llm2.calls == 0, "a restart handed out a fresh inference allowance"
    again.stop()


# --------------------------------------------------------------------------
# The refusals obey the estate's structured shape
# --------------------------------------------------------------------------


def test_neither_refusal_is_broadcast_to_the_chat_that_caused_it(tmp_path) -> None:
    """#49's rule: a refusal is journaled, never pushed back as an alert."""
    from test_refusal_journal import FakeTransport

    from mt5_risk_bot.telegram import TelegramClient

    transport = FakeTransport()
    tg = TelegramClient(
        token="t",
        chat_id="1",
        transport=transport,
        # Subscribed on purpose: if these events were broadcast, this client
        # would receive them, so the assertion can actually go red.
        notify_events=frozenset({"reject", "advice_turn", "advice_circuit_block"}),
    )
    engine = _with_gold(
        _engine(tmp_path, llm=FakeLlm(_advice_payload(symbol="XAUUSD")), telegram=tg)
    )
    engine.start()
    before = len(transport.sent)
    engine.handle_command(TgCommand("1", 1, ASK, 1))
    assert engine.journal.last_event("reject") is not None, "nothing refused, so nothing proven"
    pushed = json.dumps(transport.sent[before:])
    assert "symbol_not_allowed" not in pushed, "the refusal was broadcast into the chat"
    engine.stop()


def test_a_version_one_snapshot_still_loads_after_the_schema_grew(tmp_path) -> None:
    """An upgrade must not halt on state written by the previous build.

    The counters are new fields in the risk snapshot. A reader that rejected the
    older version would fail CLOSED on every existing install, which is a
    self-inflicted outage rather than a safety property.
    """
    from mt5_risk_bot.state import load_snapshot

    path = tmp_path / "j.equity.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "written_at": "2024-01-03T12:00:00+00:00",
                "time": 1,
                "balance": 10000.0,
                "equity": 10000.0,
                "peak_equity": 10000.0,
                "day_start_equity": 10000.0,
                "day_key": "2024-01-03",
            }
        ),
        encoding="utf-8",
    )
    snap = load_snapshot(path)
    assert snap is not None
    assert snap.day_key == "2024-01-03"
    assert snap.trades_today == 0
    assert snap.advice_turns_today == 0


def test_a_rule_saying_no_beats_could_not_measure(tmp_path) -> None:
    """Precedence, made explicit because #13 created the interaction.

    An unlisted symbol with no bars could report either `symbol_not_allowed`
    (a rule refused) or `advice_stage_failed` (nothing could be measured). The
    whitelist runs FIRST, so it reports the rule. That is the right order on
    both counts: a rule genuinely did say no, and checking it first avoids
    spending broker calls building an order that was never permitted.

    #49's own test used GBPJPY to reach the unmeasured path and this shadowed
    it; that test moved to GBPUSD, which is in the book and still has no bars,
    so the COULD NOT MEASURE path is still covered for allowed symbols.
    """
    engine = _engine(tmp_path, llm=FakeLlm(_advice_payload(symbol="GBPJPY")))
    engine.cfg.symbols = ["EURUSD"]
    engine.start()
    engine.handle_command(TgCommand("1", 1, ASK, 1))
    assert _rejects(engine, "symbol_not_allowed"), "the rule did not fire first"
    assert engine.journal.last_event("advice_stage_failed") is None, (
        "an order was built for a symbol that was never permitted"
    )
    engine.stop()


def test_the_send_cap_does_not_stop_a_close(tmp_path) -> None:
    """A cap that can trap exposure is not a risk control.

    `max_trades_per_day` counts OPENS. Once it is reached the desk must still
    be able to close what it already has.
    """
    engine = _engine(tmp_path)
    engine.cfg.risk.max_trades_per_day = 1
    engine.cfg.risk.max_positions = 9
    engine.start()
    _send(engine, 1)
    positions = engine.broker.positions(magic=engine.cfg.risk.magic)
    assert positions, "nothing was opened, so the close path is not exercised"
    assert "max_trades_per_day" in _send(engine, 2)
    reply = engine.close_ticket(positions[0].ticket, "telegram")
    assert "max_trades_per_day" not in reply, reply
    assert reply.startswith("closed"), reply
    engine.stop()


def test_the_toml_keys_actually_load(tmp_path) -> None:
    """The config plumbing, not just the dataclass defaults.

    A setting that parses nowhere is a setting that silently does not exist, so
    the shipped example is the thing this asserts against.
    """
    from mt5_risk_bot.config import load_config

    cfg = load_config("config.example.toml")
    cfg.validate()
    assert cfg.risk.max_trades_per_day == 0
    assert cfg.advice.max_turns_per_day == 0
    assert cfg.advice_symbols == []
    assert cfg.advice_allows("EURUSD") is True
    assert cfg.advice_allows("XAUUSD") is False

    custom = tmp_path / "c.toml"
    custom.write_text(
        "\n".join(
            [
                "[risk]",
                "max_trades_per_day = 4",
                "[symbols]",
                'names = ["EURUSD"]',
                "[advice]",
                "max_turns_per_day = 7",
                'symbols = ["xauusd"]',
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_config(str(custom))
    loaded.validate()
    assert loaded.risk.max_trades_per_day == 4
    assert loaded.advice.max_turns_per_day == 7
    # Upper-cased on load, and case-insensitive on lookup.
    assert loaded.advice_symbols == ["XAUUSD"]
    assert loaded.advice_allows("xauusd") is True
    assert loaded.advice_allows("EURUSD") is False, (
        "an explicit advice list REPLACES the scan list rather than adding to it"
    )


def test_a_negative_cap_is_rejected_at_validate() -> None:
    """A negative cap would make the comparison silently false forever."""
    import pytest

    from mt5_risk_bot.config import BotConfig

    cfg = BotConfig()
    cfg.risk.max_trades_per_day = -1
    with pytest.raises(ValueError, match="max_trades_per_day"):
        cfg.validate()

    cfg = BotConfig()
    cfg.advice.max_turns_per_day = -1
    with pytest.raises(ValueError, match="max_turns_per_day"):
        cfg.validate()
