"""Per-symbol slippage tolerance, and the gate that refuses one the spread eats.

The defect, measured on the live MT4 rig 2026-09-24 (see
tests/live_measurements.py for the numbers): `[risk] deviation_points` was a
single global, default 20, and a POINT is instrument-specific. 20 points is 2
pips on a 5-digit EURUSD and 20 cents on XAUUSD, where the spread alone was 45
points. Every gold send therefore offered the venue less than half of one spread
of tolerance, `OrderSend` rejected intermittently, and nothing in the log named
the cause: what reached the operator was "trades randomly do not go through".

Two changes, and the second is the one that matters:

1. `[risk.symbol_deviation_points]` overrides the global per symbol. A map alone
   only moves the failure: an operator can mis-key it, forget a symbol, or key
   `XAUUSD` on a venue that calls the instrument `XAUUSD.m`, and the send goes
   out on the global default exactly as before.

2. `deviation_below_spread` turns the whole class into a NAMED REFUSAL at our
   own gate, before the order is sent. A deviation below the spread cannot fill:
   the order has to cross the bid/ask gap. So the misconfiguration stops being
   an intermittent venue rejection with no explanation and becomes one refusal
   that names the symbol, the measurement, and the config key to change.

What this file does NOT claim: nothing here ran against the rig. Every number is
a fixture. That the gate refuses the configuration Conrad measured rejecting is
proven; that a gold send with the seeded 150 points then FILLS is not, and COULD
NOT MEASURE from here (the rig is SSH-keyed from the lead's laptop only).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from live_measurements import (
    FX_SPREAD_POINTS,
    GLOBAL_DEVIATION_POINTS,
    GOLD_ATR,
    GOLD_SPREAD_POINTS,
    gold_spec,
    gold_tick,
)
from straightedge.broker.paper import PaperBroker, default_spec
from straightedge.config import (
    DEVIATION_FROM_DEFAULT,
    DEVIATION_FROM_SYMBOL,
    BotConfig,
    load_config,
    parse_symbol_deviation_points,
)
from straightedge.engine import Engine
from straightedge.models import Account, Signal, SignalKind, Tick
from straightedge.risk import DEVIATION_BELOW_SPREAD, DEVIATION_HEADROOM_MULTIPLE, RiskManager
from straightedge.synthetic import generate_bars
from straightedge.telegram import TgCommand

WED_NOON = datetime(2024, 1, 3, 12, 0, tzinfo=timezone.utc)


def _cfg(tmp_path: Path) -> BotConfig:
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.risk.halt_file = str(tmp_path / "HALT")
    cfg.session.enabled = False
    return cfg


def _acct(equity: float = 100_000.0) -> Account:
    return Account(
        login=1,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=equity,
        profit=0.0,
        leverage=100,
        currency="USD",
    )


def _gold_signal(*, tick: Tick) -> Signal:
    """A gold BUY whose stop and target come from the MEASURED ATR.

    1.5 ATR stop and 2.5 ATR target, the shipped strategy multiples, so rr
    clears min_rr and the only gate under test is the deviation one.
    """
    entry = round(tick.ask, 2)
    return Signal(
        kind=SignalKind.BUY,
        symbol="XAUUSD",
        entry=entry,
        sl=round(entry - 1.5 * GOLD_ATR, 2),
        tp=round(entry + 2.5 * GOLD_ATR, 2),
        atr=GOLD_ATR,
        reason="test",
    )


def _gate(rm: RiskManager, *, signal: Signal, spec, tick: Tick, equity: float = 100_000.0):
    return rm.evaluate(
        account=_acct(equity),
        signal=signal,
        spec=spec,
        tick=tick,
        positions=[],
        now=WED_NOON,
        manual=True,
    )


# --- 1. the resolution: symbol first, then the global default ----------------


def test_a_listed_symbol_gets_its_own_value_and_an_absent_one_gets_the_global() -> None:
    """One assertion cannot pass on both, because the two answers DIFFER.

    A test that only checked "the resolved value is an int" or asserted the same
    number on both paths would pass with the map never consulted. So this pins
    that gold resolves to 150 and EURUSD to 20, and that the two are not equal.
    """
    risk = BotConfig().risk
    risk.symbol_deviation_points = {"XAUUSD": 150}

    gold = risk.resolve_deviation("XAUUSD")
    fx = risk.resolve_deviation("EURUSD")

    assert gold.points == 150
    assert gold.source == DEVIATION_FROM_SYMBOL
    assert fx.points == GLOBAL_DEVIATION_POINTS == risk.deviation_points
    assert fx.source == DEVIATION_FROM_DEFAULT
    assert gold.points != fx.points, (
        "the map and the default resolved to the same number, so this test "
        "cannot tell whether the map was read at all"
    )


def test_the_lookup_is_case_insensitive_and_otherwise_exact() -> None:
    """`xauusd` matches. `XAUUSD.m` does NOT, and that is the deliberate part.

    Stripping a broker decoration would mean guessing which decorations name the
    same instrument. A near-miss falls back to the global default instead, and
    `deviation_below_spread` is what makes that fallback loud.
    """
    risk = BotConfig().risk
    risk.symbol_deviation_points = {"xauusd": 150}

    assert risk.resolve_deviation("XAUUSD").points == 150
    assert risk.resolve_deviation("XauUsd").points == 150

    decorated = risk.resolve_deviation("XAUUSD.m")
    assert decorated.points == GLOBAL_DEVIATION_POINTS
    assert decorated.source == DEVIATION_FROM_DEFAULT


def test_the_source_is_reported_so_the_number_can_be_debugged() -> None:
    """20 points read in a journal says nothing about WHERE it came from."""
    risk = BotConfig().risk
    risk.symbol_deviation_points = {"XAUUSD": 150}
    assert risk.resolve_deviation("XAUUSD").source == DEVIATION_FROM_SYMBOL
    assert risk.resolve_deviation("XAUUSD").symbol == "XAUUSD"
    assert risk.resolve_deviation("GBPUSD").source == DEVIATION_FROM_DEFAULT


# --- 2. the gate: red on the measured configuration -------------------------


def test_gold_at_the_fx_default_is_refused_by_name(tmp_path: Path) -> None:
    """THE RED PROOF. The exact configuration measured rejecting at the venue.

    Gold, 45 points of spread, the global 20-point deviation, no per-symbol
    entry. Nothing else about the signal is wrong: rr clears min_rr, the stop
    clears stops_level, and the 45-cent spread is a sixth of what
    max_spread_atr_frac allows against a $17.22 ATR, so `spread_too_wide`
    passes. The only thing wrong is the number the operator configured.
    """
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    tick = gold_tick()
    decision = _gate(rm, signal=_gold_signal(tick=tick), spec=gold_spec(), tick=tick)

    assert decision.allowed is False
    assert decision.reason.startswith(DEVIATION_BELOW_SPREAD + ":")


def test_the_refusal_names_the_fix_not_just_the_fault(tmp_path: Path) -> None:
    """The reason reaches the operator verbatim, so it has to be actionable.

    desk.py answers `refused: <reason>`. "Slippage too small" without the config
    key and the number to put in it is a refusal the operator cannot act on, and
    an unactionable refusal is how a gate gets switched off.

    `floor` is what the gate enforced (one spread, 45). `set` is larger on
    purpose: an operator who writes the exact floor is refused again by the
    first tick that widens the spread by one point. See
    DEVIATION_HEADROOM_MULTIPLE.
    """
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    tick = gold_tick()
    reason = _gate(rm, signal=_gold_signal(tick=tick), spec=gold_spec(), tick=tick).reason

    assert "XAUUSD" in reason
    assert f"deviation={GLOBAL_DEVIATION_POINTS}" in reason
    assert f"source={DEVIATION_FROM_DEFAULT}" in reason
    assert f"spread={GOLD_SPREAD_POINTS}pt" in reason
    assert f"floor={GOLD_SPREAD_POINTS}pt" in reason
    advised = int(DEVIATION_HEADROOM_MULTIPLE * GOLD_SPREAD_POINTS)
    assert f"set=risk.symbol_deviation_points.XAUUSD>={advised}" in reason, reason


def test_a_mis_keyed_override_is_caught_by_the_gate(tmp_path: Path) -> None:
    """The map can be got wrong; this is what stops that being silent.

    An operator who keys `XAUUSD` on a venue that calls gold `XAUUSD.m` has
    configured nothing, and before this gate the send simply went out on 20
    points and was rejected by the broker with no cause recorded.
    """
    cfg = _cfg(tmp_path)
    cfg.risk.symbol_deviation_points = {"XAUUSD": 150}
    rm = RiskManager(cfg, halt_dir=tmp_path)
    tick = gold_tick()
    signal = _gold_signal(tick=tick)
    decorated = Signal(
        kind=signal.kind,
        symbol="XAUUSD.m",
        entry=signal.entry,
        sl=signal.sl,
        tp=signal.tp,
        atr=signal.atr,
        reason="test",
    )
    decision = _gate(rm, signal=decorated, spec=gold_spec(), tick=tick)

    assert decision.allowed is False
    assert decision.reason.startswith(DEVIATION_BELOW_SPREAD + ":")
    assert "XAUUSD.m" in decision.reason
    assert f"source={DEVIATION_FROM_DEFAULT}" in decision.reason


# --- 3. the gate: green on a correct configuration --------------------------


def test_gold_with_the_seeded_override_passes(tmp_path: Path) -> None:
    """THE GREEN PROOF. Same spec, same tick, the override from the example config.

    150 points is what `config.example.toml` seeds, and it clears the 45-point
    floor with headroom. The decision is `ok` with a volume, so this is the whole
    chain passing and not merely this one gate abstaining.
    """
    cfg = _cfg(tmp_path)
    cfg.risk.symbol_deviation_points = {"XAUUSD": 150}
    rm = RiskManager(cfg, halt_dir=tmp_path)
    tick = gold_tick()
    decision = _gate(rm, signal=_gold_signal(tick=tick), spec=gold_spec(), tick=tick)

    assert decision.allowed is True, decision.reason
    assert decision.reason == "ok"
    assert decision.volume > 0


def test_raising_the_global_default_also_works(tmp_path: Path) -> None:
    """The gate is about the EFFECTIVE number, not about which key supplied it."""
    cfg = _cfg(tmp_path)
    cfg.risk.deviation_points = 150
    rm = RiskManager(cfg, halt_dir=tmp_path)
    tick = gold_tick()
    decision = _gate(rm, signal=_gold_signal(tick=tick), spec=gold_spec(), tick=tick)
    assert decision.reason == "ok"


# --- 4. no regression on normal FX ------------------------------------------


def test_eurusd_at_the_default_is_unaffected(tmp_path: Path) -> None:
    """The measured live FX case: 1 point of spread against 20 of tolerance.

    Twenty times the margin. A gate that touched this would be an outage, not a
    control.
    """
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    spec = default_spec("EURUSD")
    half = (FX_SPREAD_POINTS * spec.point) / 2.0
    tick = Tick(time=0, bid=1.10 - half, ask=1.10 + half, last=1.10)
    assert spec.points(tick.spread) == pytest.approx(FX_SPREAD_POINTS)

    signal = Signal(
        kind=SignalKind.BUY, symbol="EURUSD", entry=1.10, sl=1.095, tp=1.1125, atr=0.003
    )
    assert _gate(rm, signal=signal, spec=spec, tick=tick).reason == "ok"


def test_the_paper_brokers_own_fx_spread_is_unaffected(tmp_path: Path) -> None:
    """The repo's own fixture: 10 points of spread against 20 of tolerance.

    2x, which is ordinary retail FX and fills routinely. This case is why the
    floor multiple is 1.0 and not 3.0: at 3.0 the gate refused 88 tests in this
    suite, all of them encoding a configuration that works.
    """
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    broker = PaperBroker(balance=100_000)
    broker.seed_bars("EURUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=3))
    spec = broker.symbol("EURUSD")
    tick = broker.tick("EURUSD")
    assert round(spec.points(tick.spread)) == 10

    mid = round(tick.ask, spec.digits)
    signal = Signal(
        kind=SignalKind.BUY,
        symbol="EURUSD",
        entry=mid,
        sl=round(mid - 0.0045, spec.digits),
        tp=round(mid + 0.0075, spec.digits),
        atr=0.003,
    )
    assert _gate(rm, signal=signal, spec=spec, tick=tick).reason == "ok"


# --- 5. the boundary, pinned deliberately -----------------------------------


def test_exactly_at_the_floor_passes_and_one_point_under_refuses(tmp_path: Path) -> None:
    """`below` the spread, not `at` it, and the boundary is watched.

    Worth pinning for a second reason: the default tick in
    tests/test_refusal_reasons.py carries 20 points against the 20-point
    default, so most of that roster runs at EXACTLY this boundary. If the
    comparison ever loosens to `<=`, it fails here by name instead of turning
    half the suite red for no stated reason.
    """
    cfg = _cfg(tmp_path)
    spread_points = 40
    cfg.risk.deviation_points = spread_points
    rm = RiskManager(cfg, halt_dir=tmp_path)
    tick = gold_tick(spread_points=spread_points)
    spec = gold_spec(spread_points=spread_points)

    assert _gate(rm, signal=_gold_signal(tick=tick), spec=spec, tick=tick).reason == "ok"

    cfg.risk.deviation_points = spread_points - 1
    under = _gate(rm, signal=_gold_signal(tick=tick), spec=spec, tick=tick)
    assert under.reason.startswith(DEVIATION_BELOW_SPREAD + ":")
    assert f"deviation={spread_points - 1}" in under.reason


def test_a_multiple_above_one_refuses_what_one_spread_allows(tmp_path: Path) -> None:
    """The multiple is a real lever, not a decorative field.

    An operator who wants margin sets it, and the gate then refuses a deviation
    that clears one full spread. Proven by the same inputs answering differently
    on either side of the key.
    """
    cfg = _cfg(tmp_path)
    cfg.risk.symbol_deviation_points = {"XAUUSD": 50}
    rm = RiskManager(cfg, halt_dir=tmp_path)
    tick = gold_tick()
    spec = gold_spec()
    assert _gate(rm, signal=_gold_signal(tick=tick), spec=spec, tick=tick).reason == "ok"

    cfg.risk.min_deviation_spread_multiple = 2.0
    strict = _gate(rm, signal=_gold_signal(tick=tick), spec=spec, tick=tick)
    assert strict.reason.startswith(DEVIATION_BELOW_SPREAD + ":")
    assert "floor=90pt" in strict.reason


def test_the_gate_abstains_on_a_crossed_tick(tmp_path: Path) -> None:
    """Spread <= 0 is a broken tick: nothing to compare, so no verdict.

    Recorded rather than left implicit. This gate must not report a PASS it did
    not measure, and it also must not invent a refusal from a tick fault it does
    not own: no gate currently owns a crossed tick, which is a separate finding.
    """
    cfg = _cfg(tmp_path)
    cfg.risk.deviation_points = 1
    rm = RiskManager(cfg, halt_dir=tmp_path)
    flat = Tick(time=0, bid=4000.00, ask=4000.00, last=4000.00)
    decision = _gate(rm, signal=_gold_signal(tick=gold_tick()), spec=gold_spec(), tick=flat)
    assert not decision.reason.startswith(DEVIATION_BELOW_SPREAD)


# --- 6. it refuses; it does not quietly raise the number --------------------


def test_the_operators_number_is_never_overridden(tmp_path: Path) -> None:
    """A gate that fixed the config would leave a number nobody wrote in force."""
    cfg = _cfg(tmp_path)
    rm = RiskManager(cfg, halt_dir=tmp_path)
    tick = gold_tick()
    _gate(rm, signal=_gold_signal(tick=tick), spec=gold_spec(), tick=tick)

    assert cfg.risk.deviation_points == GLOBAL_DEVIATION_POINTS
    assert cfg.risk.symbol_deviation_points == {}
    assert cfg.risk.resolve_deviation("XAUUSD").points == GLOBAL_DEVIATION_POINTS


# --- 7. which number applied is observable on every send -------------------


def test_the_journal_records_the_deviation_and_where_it_came_from(tmp_path: Path) -> None:
    """Observability is the requirement, so it is asserted on the artifact.

    The journal is the structured channel; the number alone cannot tell an
    operator whether their override was consulted or missed.
    """
    cfg = _cfg(tmp_path)
    cfg.risk.max_spread_atr_frac = 10.0
    cfg.risk.symbol_deviation_points = {"EURUSD": 60}
    broker = PaperBroker(balance=100_000)
    broker.seed_bars("EURUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=3))
    engine = Engine(cfg, broker, halt_dir=str(tmp_path), now_fn=lambda: WED_NOON)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    engine.handle_command(TgCommand("2", 2, "/confirm", 1))

    opened = engine.journal.last_event("open")
    assert opened is not None, "no open record to read the deviation from"
    assert opened["deviation"] == 60
    assert opened["deviation_source"] == DEVIATION_FROM_SYMBOL

    ticket = int(opened["order"])
    engine.handle_command(TgCommand("3", 3, f"/close {ticket}", 1))
    closed = engine.journal.last_event("close")
    assert closed is not None
    assert closed["deviation"] == 60
    assert closed["deviation_source"] == DEVIATION_FROM_SYMBOL
    engine.stop()


# --- 8. the config keys -----------------------------------------------------


def test_the_example_config_seeds_the_measured_metals() -> None:
    """The shipped example must carry gold, and it must resolve.

    A comment recommending a value nobody parsed is not a default.
    """
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.example.toml")
    gold = cfg.risk.resolve_deviation("XAUUSD")
    assert gold.source == DEVIATION_FROM_SYMBOL
    assert gold.points >= DEVIATION_HEADROOM_MULTIPLE * GOLD_SPREAD_POINTS
    assert cfg.risk.resolve_deviation("XAGUSD").source == DEVIATION_FROM_SYMBOL
    assert cfg.risk.resolve_deviation("EURUSD").source == DEVIATION_FROM_DEFAULT


def test_the_handover_config_seeds_them_too() -> None:
    """Gil trades mostly gold, and the handover template is what he is handed."""
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.handover.toml")
    assert cfg.risk.resolve_deviation("XAUUSD").source == DEVIATION_FROM_SYMBOL


def test_an_omitted_multiple_gets_the_dataclass_default(tmp_path: Path) -> None:
    """One number, one home.

    The loader's first draft restated `3.0` here while the field said `1.0`, so a
    config that omitted the key got a different answer from one that spelled the
    default out. The fallback reads the dataclass; this is what holds it there.
    """
    path = tmp_path / "c.toml"
    path.write_text('[risk]\nmax_positions = 3\n', encoding="utf-8")
    cfg = load_config(path)
    assert (
        cfg.risk.min_deviation_spread_multiple
        == BotConfig().risk.min_deviation_spread_multiple
    )


def test_a_scalar_where_a_table_belongs_raises(tmp_path: Path) -> None:
    """An override the loader silently dropped is the worst of the outcomes.

    The send would go out on the global default while the config said otherwise
    and nothing reported the disagreement.
    """
    with pytest.raises(ValueError, match="must be a table"):
        parse_symbol_deviation_points(150)


@pytest.mark.parametrize(
    "raw",
    [
        {"XAUUSD": "150"},
        {"XAUUSD": True},
        {"XAUUSD": 150.5},
    ],
)
def test_a_non_integer_point_count_raises(raw) -> None:
    with pytest.raises(ValueError):
        parse_symbol_deviation_points(raw)


def test_the_multiple_cannot_be_configured_below_one_spread() -> None:
    """There is no value that switches this gate off.

    Below 1.0 the gate would permit a deviation that cannot fill, which is the
    defect it exists to name. A loud config error is the right answer; a silently
    disabled control is the one this repo keeps finding.
    """
    cfg = BotConfig()
    cfg.risk.min_deviation_spread_multiple = 0.0
    with pytest.raises(ValueError, match="min_deviation_spread_multiple"):
        cfg.validate()


def test_a_zero_or_negative_override_is_refused() -> None:
    """0 reaches the MT4 Expert as "use your own input Slippage" (Mt4RiskBot.mq4
    only falls back when the passed value is <= 0), so a 0 here would silently
    hand the number to a default nobody configured on this side."""
    cfg = BotConfig()
    cfg.risk.symbol_deviation_points = {"XAUUSD": 0}
    with pytest.raises(ValueError, match="must be > 0"):
        cfg.validate()


def test_two_keys_for_one_symbol_are_refused() -> None:
    """Case-insensitive matching means `xauusd` and `XAUUSD` are one symbol.

    Left alone, the lookup would pick whichever came first in the file and the
    other would be dead config nobody could see was dead.
    """
    cfg = BotConfig()
    cfg.risk.symbol_deviation_points = {"XAUUSD": 150, "xauusd": 40}
    with pytest.raises(ValueError, match="two entries for the same"):
        cfg.validate()
