"""One test per refusal reason, each asserting the NAMED reason (#11).

This file is the ROSTER. `allowed is False` cannot tell you a control has
stopped testing anything; `reason == (the name)` can, so every case
here compares the reason string, and the desk cases compare the structured
journal record from #29 rather than the prose the chat gets back.

REASONS is the denominator. It is asserted against the literals actually
present in risk.py, so adding a reason to the module without adding a case
here fails the suite instead of quietly lowering the count.

Two reasons cannot be produced by RiskManager at all, and that is a finding,
not a gap to paper over. Both are pinned by a test that FAILS if they ever
become reachable, so the roster cannot rot into a test against dead code:

- `size_exceeds_risk` (risk.py:347) is dominated by `size_zero`.
  `lots_for_risk` already refuses the identical condition with a TIGHTER
  tolerance (1e-9 against the gate own 1e-6) and returns 0 lots, which the
  preceding branch reports as `size_zero`. See
  test_size_exceeds_risk_is_dominated_by_size_zero.
- `halted` (risk.py:252) is an `or` fallback for a halted manager with an
  empty reason. Every site that sets the halt flag also sets a reason, so no
  public call can produce it. See test_halted_fallback_is_unreachable.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mt5_risk_bot.broker.paper import PaperBroker, default_spec
from mt5_risk_bot.config import BotConfig
from mt5_risk_bot.models import Account, Position, Side, Signal, SignalKind, Tick
from mt5_risk_bot.risk import RiskManager
from mt5_risk_bot.sizing import lots_for_risk, money_per_lot_at_stop
from mt5_risk_bot.state import snapshot_path_for
from mt5_risk_bot.synthetic import generate_bars
from mt5_risk_bot.telegram import TgCommand
from mt5_risk_bot.engine import Engine


MAGIC = BotConfig().risk.magic
WED_NOON = datetime(2024, 1, 3, 12, 0, tzinfo=timezone.utc)
WED_EARLY = datetime(2024, 1, 3, 5, 0, tzinfo=timezone.utc)  # before 07:00 UTC

# Every refusal reason RiskManager can name. Asserted against risk.py itself
# by test_roster_covers_every_reason_in_the_module, so the denominator is
# measured and not carried forward from an issue body.
REASONS = (
    "state_unreadable",
    "state_unwritable",
    "halt_file",
    "halted",
    "trade_not_allowed",
    "live_not_accepted",
    "daily_loss",
    "max_drawdown",
    "no_signal",
    "outside_session",
    "max_positions",
    "already_in_symbol",
    "currency_exposure",
    "sl_required",
    "rr_below_min",
    "stops_level",
    "spread_too_wide",
    "margin_buffer",
    "size_zero",
    "size_exceeds_risk",
)

# Not reachable through any public RiskManager call. See the module docstring.
UNREACHABLE = ("halted", "size_exceeds_risk")


def _acct(equity: float = 10_000, **kw) -> Account:
    return Account(
        login=1,
        balance=kw.get("balance", equity),
        equity=equity,
        margin=kw.get("margin", 0.0),
        margin_free=kw.get("margin_free", equity),
        profit=0.0,
        leverage=100,
        currency="USD",
        trade_allowed=kw.get("trade_allowed", True),
        trade_expert=kw.get("trade_expert", True),
        trade_mode=kw.get("trade_mode", 0),
    )


def _sig(kind=SignalKind.BUY, symbol="EURUSD", entry=1.10, sl=1.095, tp=1.1125, atr=0.003) -> Signal:
    return Signal(kind, symbol, entry, sl, tp, atr, reason="test")


def _tick(bid=1.0999, ask=1.1001) -> Tick:
    return Tick(time=0, bid=bid, ask=ask)


def _pos(ticket: int, symbol: str, side=Side.BUY) -> Position:
    return Position(ticket, symbol, side, 0.1, 1.1, 1.09, 1.12, 1.1, 0, magic=MAGIC)


def _cfg(tmp_path: Path, **kw) -> BotConfig:
    """Keep the equity snapshot inside tmp_path, per issue #7."""
    cfg = BotConfig(**kw)
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.risk.halt_file = str(tmp_path / "HALT")
    cfg.session.enabled = False
    return cfg


def _gate(rm: RiskManager, **kw):
    """One evaluate() call with every lever exposed as a keyword."""
    return rm.evaluate(
        account=kw.get("account", _acct()),
        signal=kw.get("signal", _sig()),
        spec=kw.get("spec", default_spec("EURUSD")),
        tick=kw.get("tick", _tick()),
        positions=kw.get("positions", []),
        now=kw.get("now", WED_NOON),
        manual=kw.get("manual", True),
    )


# --- the denominator itself -------------------------------------------------


def test_roster_covers_every_reason_in_the_module() -> None:
    """The roster is measured against risk.py, never against the issue body.

    A reason added to risk.py without a case in this file fails HERE, which is
    the only way a per-reason suite stays a denominator instead of becoming a
    snapshot of the day it was written.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "mt5_risk_bot" / "risk.py"
    text = src.read_text(encoding="utf-8")
    found = set(re.findall(r"reason=\"([a-z_]+)\"", text))
    found |= set(re.findall(r"_halt\(\"([a-z_]+)\"", text))
    found |= set(re.findall(r"_halt_reason = \"([a-z_]+)\"", text))
    found |= set(re.findall(r"return \"([a-z_]+)\"", text))
    found |= set(re.findall(r"or \"([a-z_]+)\"", text))
    found.discard("ok")
    missing = found - set(REASONS)
    assert not missing, "risk.py names refusal reasons with no case here: " + repr(sorted(missing))
    stale = set(REASONS) - found
    assert not stale, "roster names reasons risk.py no longer has: " + repr(sorted(stale))
    assert len(REASONS) == len(set(REASONS)) == 20


# --- 1. the halt family: reasons that come out of circuit() -----------------


def test_state_unreadable_names_the_reason(tmp_path: Path) -> None:
    """A corrupt snapshot is COULD NOT MEASURE, so the gate fails closed."""
    cfg = _cfg(tmp_path)
    snapshot_path_for(cfg.journal_path).write_text("{", encoding="utf-8")
    rm = RiskManager(cfg, halt_dir=tmp_path)
    d = _gate(rm)
    assert d.reason == "state_unreadable"
    assert rm.circuit_reason(_acct(), WED_NOON) == "state_unreadable"


def test_state_unwritable_names_the_reason(tmp_path: Path, monkeypatch) -> None:
    """A snapshot that cannot be written loses the budget on restart."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)

    def boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("mt5_risk_bot.state.os.replace", boom)
    assert _gate(rm).reason == "state_unwritable"


def test_halt_file_names_the_reason(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    rm = RiskManager(cfg, halt_dir=tmp_path)
    Path(cfg.risk.halt_file).write_text("operator\n", encoding="utf-8")
    d = _gate(rm)
    assert d.reason == "halt_file"
    assert d.halt and d.flatten
    assert rm.circuit_reason(_acct(), WED_NOON) == "halt_file"


def test_trade_not_allowed_names_the_reason(tmp_path: Path) -> None:
    """Terminal-level block. Two sites, and BOTH are named here: circuit()

    decides whether a trade may go, circuit_reason() decides whether the LLM
    may stage at all, and the second one had no test at all.
    """
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    assert _gate(rm, account=_acct(trade_allowed=False)).reason == "trade_not_allowed"
    assert rm.circuit_reason(_acct(trade_allowed=False), WED_NOON) == "trade_not_allowed"


def test_trade_not_allowed_covers_the_expert_flag_too(tmp_path: Path) -> None:
    """trade_expert=False is the same refusal; the or-branch needs its own case."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    assert _gate(rm, account=_acct(trade_expert=False)).reason == "trade_not_allowed"
    assert rm.circuit_reason(_acct(trade_expert=False), WED_NOON) == "trade_not_allowed"


@pytest.mark.parametrize("mode", ["mt5", "mt4"])
def test_live_not_accepted_names_the_reason(tmp_path: Path, mode: str) -> None:
    """Real-money terminal without the typed acceptance. Both sites."""
    cfg = _cfg(tmp_path, mode=mode, live_accepted=False)
    rm = RiskManager(cfg, halt_dir=tmp_path)
    assert _gate(rm, account=_acct(trade_mode=2)).reason == "live_not_accepted"
    assert rm.circuit_reason(_acct(trade_mode=2), WED_NOON) == "live_not_accepted"


def test_daily_loss_names_the_reason(tmp_path: Path) -> None:
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    rm.observe(_acct(10_000), WED_NOON)
    d = _gate(rm, account=_acct(9_700))  # -3 pct against a 2 pct cap
    assert d.reason == "daily_loss"
    assert d.halt


def test_daily_loss_names_the_reason_on_the_staging_gate(tmp_path: Path) -> None:
    """circuit_reason(), the gate deciding whether the LLM may stage at all."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    rm.observe(_acct(10_000), WED_NOON)
    assert rm.circuit_reason(_acct(9_700), WED_NOON) == "daily_loss"


def test_max_drawdown_names_the_reason(tmp_path: Path) -> None:
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    day2 = datetime(2024, 1, 4, 12, 0, tzinfo=timezone.utc)
    rm.observe(_acct(10_000), WED_NOON)
    rm.observe(_acct(9_000), day2)  # new UTC day: daily clock resets, peak does not
    d = _gate(rm, account=_acct(8_900), now=day2)
    assert d.reason == "max_drawdown"
    assert d.halt


def test_max_drawdown_names_the_reason_on_the_staging_gate(tmp_path: Path) -> None:
    """circuit_reason() line 262 had no test; a fresh manager is needed.

    circuit() LATCHES the halt, so asserting both sites on one manager would
    read the latch on the second call and never enter the drawdown branch.
    Two managers is the only way this case can actually reach the line.
    """
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    day2 = datetime(2024, 1, 4, 12, 0, tzinfo=timezone.utc)
    rm.observe(_acct(10_000), WED_NOON)
    rm.observe(_acct(9_000), day2)
    assert rm.circuit_reason(_acct(8_900), day2) == "max_drawdown"
    assert rm.halt_reason == "", "circuit_reason must not latch a market verdict"


def test_a_latched_halt_is_reported_by_the_staging_gate(tmp_path: Path) -> None:
    """risk.py:252, the latched-halt branch of circuit_reason().

    It returns the latched reason, never the bare string halted; see
    test_halted_fallback_is_unreachable for why that fallback cannot fire.
    """
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    rm.observe(_acct(10_000), WED_NOON)
    assert _gate(rm, account=_acct(9_700)).reason == "daily_loss"  # latch it
    assert rm.is_halted
    assert rm.circuit_reason(_acct(10_000), WED_NOON) == "daily_loss"


# --- 2. the evaluate() gates, in the order risk.py applies them -------------


def test_no_signal_names_the_reason(tmp_path: Path) -> None:
    """A FLAT signal, or one with no side, is not an order.

    Asserted directly against RiskManager because neither production caller
    can reach it: the auto leg returns on FLAT before it gets here
    (engine.py:463) and the desk only ever builds BUY or SELL. It is a
    defensive guard on a public method, so the public method is where it is
    tested; see the PR body for why that is a finding and not a fix.
    """
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    flat = Signal(SignalKind.FLAT, "EURUSD", 1.10, 1.095, 1.1125, 0.003)
    assert flat.side is None
    assert _gate(rm, signal=flat).reason == "no_signal"


def test_outside_session_names_the_reason(tmp_path: Path) -> None:
    """The session window binds the AUTO leg only; manual=True bypasses it."""
    cfg = _cfg(tmp_path)
    cfg.session.enabled = True
    rm = RiskManager(cfg, halt_dir=tmp_path)
    assert _gate(rm, now=WED_EARLY, manual=False).reason == "outside_session"


def test_outside_session_is_not_applied_to_a_manual_order(tmp_path: Path) -> None:
    """The negative half: the same clock with manual=True must NOT refuse.

    Without this, a gate stuck on always-refuse would pass the case above.
    """
    cfg = _cfg(tmp_path)
    cfg.session.enabled = True
    rm = RiskManager(cfg, halt_dir=tmp_path)
    assert _gate(rm, now=WED_EARLY, manual=True).reason == "ok"


def test_outside_session_covers_the_weekend(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.session.enabled = True
    rm = RiskManager(cfg, halt_dir=tmp_path)
    saturday = datetime(2024, 1, 6, 12, 0, tzinfo=timezone.utc)
    assert _gate(rm, now=saturday, manual=False).reason == "outside_session"


def test_max_positions_names_the_reason(tmp_path: Path) -> None:
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    ours = [_pos(1, "GBPUSD"), _pos(2, "USDCHF"), _pos(3, "AUDCAD")]
    assert len(ours) == BotConfig().risk.max_positions
    assert _gate(rm, positions=ours).reason == "max_positions"


def test_max_positions_counts_only_our_magic(tmp_path: Path) -> None:
    """Another EA at the same broker must not consume our slot count."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    theirs = [
        Position(9, "GBPUSD", Side.BUY, 0.1, 1.2, 1.19, 1.22, 1.2, 0, magic=MAGIC + 1)
        for _ in range(5)
    ]
    assert _gate(rm, positions=theirs).reason == "ok"


def test_already_in_symbol_names_the_reason(tmp_path: Path) -> None:
    """One position per symbol. Below max_positions, so only this gate fires."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    assert _gate(rm, positions=[_pos(1, "EURUSD")]).reason == "already_in_symbol"


def test_currency_exposure_names_the_reason(tmp_path: Path) -> None:
    """Two long EUR legs already; a third would be three deep on one currency."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    ours = [_pos(1, "EURGBP"), _pos(2, "EURJPY")]
    assert _gate(rm, positions=ours).reason == "currency_exposure"


def test_sl_required_names_the_reason(tmp_path: Path) -> None:
    """No stop is not a trade. The whole desk is built on there being one."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    no_stop = _sig(sl=0.0)
    assert _gate(rm, signal=no_stop).reason == "sl_required"


def test_sl_required_covers_a_zero_risk_distance(tmp_path: Path) -> None:
    """The or-branch: a positive stop sitting exactly on the entry."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    flat_stop = _sig(entry=1.10, sl=1.10, tp=1.11)
    assert flat_stop.risk_distance == 0
    assert _gate(rm, signal=flat_stop).reason == "sl_required"


def test_rr_below_min_names_the_reason(tmp_path: Path) -> None:
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    thin = _sig(entry=1.10, sl=1.095, tp=1.1050)  # rr 1.0 against a 1.5 floor
    assert abs(thin.rr - 1.0) < 1e-9
    assert _gate(rm, signal=thin).reason == "rr_below_min"


def test_stops_level_names_the_reason(tmp_path: Path) -> None:
    """Broker minimum stop distance. rr is kept healthy so only this fires."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    spec = default_spec("EURUSD")
    tight = _sig(entry=1.10, sl=1.09995, tp=1.10015)  # 5e-05 against a 1e-04 floor
    assert tight.risk_distance < spec.min_stop_distance()
    assert tight.rr >= BotConfig().risk.min_rr
    assert _gate(rm, signal=tight, spec=spec).reason == "stops_level"


def test_spread_too_wide_names_the_reason(tmp_path: Path) -> None:
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    wide = _tick(bid=1.0990, ask=1.1010)  # 0.002 against 0.15 * atr = 0.00045
    assert wide.spread > 0.15 * 0.003
    assert _gate(rm, tick=wide).reason == "spread_too_wide"


def test_spread_gate_is_skipped_when_atr_is_unknown(tmp_path: Path) -> None:
    """atr=0 means no yardstick, so the spread gate must not fire on a guess."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    wide = _tick(bid=1.0990, ask=1.1010)
    assert _gate(rm, signal=_sig(atr=0.0), tick=wide).reason == "ok"


def test_margin_buffer_names_the_reason(tmp_path: Path) -> None:
    """Free margin below the floor with margin actually in use."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    thin = _acct(10_000, margin=6_000.0, margin_free=4_000.0)  # 0.40 against 0.50
    assert _gate(rm, account=thin).reason == "margin_buffer"


def test_margin_buffer_does_not_fire_on_a_flat_book(tmp_path: Path) -> None:
    """margin == 0 is a flat account, not a squeezed one; the and-branch."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    flat = _acct(10_000, margin=0.0, margin_free=0.0)
    assert _gate(rm, account=flat).reason == "ok"


def test_size_zero_names_the_reason(tmp_path: Path) -> None:
    """The broker minimum lot would risk more than the budget allows.

    500 ticks of stop at 1.00 per tick is 500.00 per lot, so 0.01 lots (the
    broker floor) risks 5.00 against a budget of 100 * 0.005 = 0.50, and
    lots_for_risk refuses to size UP into extra risk. The starting balance is
    matched to the account so the drawdown gate, which runs first, does not
    claim this refusal instead.
    """
    rm = RiskManager(_cfg(tmp_path, initial_balance=100.0), halt_dir=tmp_path)
    d = _gate(rm, account=_acct(100))
    assert d.reason == "size_zero"
    assert d.volume == 0


# --- 3. the two reasons RiskManager cannot produce, pinned as findings ------


def test_size_exceeds_risk_is_dominated_by_size_zero(tmp_path: Path) -> None:
    """risk.py:347 cannot fire. This test FAILS the day it can.

    The last-line size guard recomputes exactly what lots_for_risk already
    checked, from the same entry, stop and spec, but with a LOOSER tolerance:

        lots_for_risk:  actual_risk > budget * mrm + 1e-9  -> return 0 lots
        risk.py:346:    worst       > budget * mrm + 1e-6  -> size_exceeds_risk

    worst IS actual_risk, so anything that would trip the outer guard has
    already been turned into 0 lots by the tighter inner one, and the
    preceding branch reports it as size_zero. size_exceeds_risk is therefore
    unreachable through RiskManager, which is why #11 could not find a way to
    see it red: there is none. The reason string is still live in the
    product, but only from engine.py, on the /replace path, which does not
    call lots_for_risk at all (see the engine test below).

    Reported, NOT fixed: collapsing the two guards is a change to a
    real-money sizing path and belongs in its own reviewed issue.
    """
    spec = default_spec("EURUSD")
    cases = [
        (10_000.0, 0.005, 1.0),
        (100.0, 0.005, 1.0),
        (1_000_000.0, 0.5, 2.0),
        (10_000.0, 1.0, 1e-09),
        (1.0, 1e-06, 1e06),
    ]
    reached = 0
    for equity, risk_pct, mrm in cases:
        for sl in (1.095, 1.0999, 1.05, 0.5):
            entry = 1.10
            lots = lots_for_risk(equity, risk_pct, entry, sl, spec, max_risk_multiple=mrm)
            if lots <= 0:
                continue  # risk.py returns size_zero before reaching line 346
            reached += 1
            worst = money_per_lot_at_stop(entry, sl, spec) * lots
            cap = equity * risk_pct * mrm
            assert worst <= cap + 1e-06, (
                "risk.py:347 size_exceeds_risk became REACHABLE; it now needs a"
                " real test and this pin must be replaced: "
                + repr((equity, risk_pct, mrm, sl, lots, worst, cap))
            )
    assert reached >= 5, "the pin measured nothing; no case got past size_zero"


def test_halted_fallback_is_unreachable(tmp_path: Path) -> None:
    """risk.py:252 reads `self._halt_reason or halted`; the fallback is dead.

    Every site that raises the halt flag sets a reason in the same statement
    block, so there is no public sequence that leaves the flag up and the
    reason empty. Asserted by exercising every public way to halt and
    checking the reason is always non-empty, so the fallback stays dead by
    measurement rather than by reading.
    """
    cfg = _cfg(tmp_path)
    rm = RiskManager(cfg, halt_dir=tmp_path)
    rm.observe(_acct(10_000), WED_NOON)

    rm.write_halt_file("operator")
    assert rm.circuit_reason(_acct(10_000), WED_NOON) == "halt_file"
    assert rm.clear_operator_halt() == ""

    assert _gate(rm, account=_acct(9_700)).reason == "daily_loss"
    assert rm.is_halted and rm.halt_reason != ""
    assert rm.circuit_reason(_acct(9_700), WED_NOON) == "daily_loss"
    assert rm.clear_operator_halt() == "daily_loss"
    assert rm.halt_reason != "", "a halted manager with no reason would reach line 252"


def test_size_exceeds_risk_has_exactly_one_live_site() -> None:
    """The duplicate literal, made attributable instead of ambiguous.

    The string exists twice in the product and the two sites guard different
    things:

      risk.py:347   sizing a NEW market order   -- DEAD, dominated by size_zero
      engine.py     re-pricing an EXISTING working order -- live, /replace

    Before #11 the only test naming the reason matched a substring of a chat
    reply, which passes on the engine site while reading as though it covered
    the risk gate. Deduplicating the literal would not have fixed that; the
    ambiguity was never the spelling, it was that one of the two sites cannot
    run. So the literal stays where it is and attribution is asserted: two
    sites, one of them pinned dead above, and a third appearance fails here.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "mt5_risk_bot"
    sites = {
        p.name: (p.read_text(encoding="utf-8").count("size_exceeds_risk"))
        for p in sorted(src.glob("*.py"))
    }
    live = {name: n for name, n in sites.items() if n}
    assert live == {"engine.py": 1, "risk.py": 1}, (
        "size_exceeds_risk moved or gained a site; attribution must be redone: "
        + repr(live)
    )


def test_the_engine_replace_guard_is_the_live_size_exceeds_risk_site(tmp_path: Path) -> None:
    """The one site that CAN refuse with this name, exercised on its own path.

    /replace keeps the order volume the broker already accepted and re-prices
    it, so it never calls lots_for_risk and the dominance above does not apply.
    This is the refusal an operator can actually receive.
    """
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.session.enabled = False
    cfg.risk.max_spread_atr_frac = 10.0
    cfg.risk.halt_file = str(tmp_path / "HALT")
    broker = PaperBroker(balance=10_000)
    broker.seed_bars("EURUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=3))
    broker.seed_bars("GBPUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=4))
    broker.seed_bars("AUDUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=5))
    broker.seed_bars("USDCHF", generate_bars(120, drift=0.0004, vol=0.0002, seed=6))
    engine = Engine(
        cfg,
        broker,
        halt_dir=str(tmp_path),
        now_fn=lambda: WED_NOON,
    )
    engine.start()
    tick = broker.tick("EURUSD")
    spec = broker.symbol("EURUSD")
    limit = spec.normalize_price(tick.ask - 0.002)
    sl = spec.normalize_price(limit - 0.005)
    tp = spec.normalize_price(limit + 0.010)
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD limit=" + str(limit) + " sl=" + str(sl) + " tp=" + str(tp), 1))
    engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    order = broker.orders()[0]
    wider = spec.normalize_price(limit + 0.001)  # widens the stop past the cap
    reply = engine.handle_command(TgCommand("1", 1, "/replace " + str(order.ticket) + " " + str(wider), 3))
    assert reply == "refused: size_exceeds_risk"
    engine.stop()


# --- 4. the same reasons on the surface an operator actually reads ----------
#
# Where a reason is reachable through the desk or the auto leg, the structured
# reject record from #29 is the assertion, not the return value: the record is
# what an operator and an auditor read after the fact. The return-value cases
# above stay because several reasons have no other reachable caller.


def _engine(tmp_path: Path, **kw) -> Engine:
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.session.enabled = kw.get("session", False)
    cfg.risk.max_spread_atr_frac = kw.get("max_spread_atr_frac", 10.0)
    cfg.risk.halt_file = str(tmp_path / "HALT")
    if "risk_pct" in kw:
        cfg.risk.risk_pct = kw["risk_pct"]
    if "max_currency_exposure" in kw:
        cfg.risk.max_currency_exposure = kw["max_currency_exposure"]
    if "max_positions" in kw:
        cfg.risk.max_positions = kw["max_positions"]
    broker = PaperBroker(
        balance=kw.get("balance", 10_000),
        leverage=kw.get("leverage", 100),
        trade_allowed=kw.get("trade_allowed", True),
    )
    broker.seed_bars("EURUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=3))
    broker.seed_bars("GBPUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=4))
    broker.seed_bars("AUDUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=5))
    broker.seed_bars("USDCHF", generate_bars(120, drift=0.0004, vol=0.0002, seed=6))
    return Engine(
        cfg,
        broker,
        halt_dir=str(tmp_path),
        now_fn=lambda: WED_NOON,
    )


def _reject(engine: Engine) -> dict:
    rec = engine.journal.last_event("reject")
    assert rec is not None, "a refusal left no structured record"
    return rec


def test_already_in_symbol_is_journaled_with_the_name(tmp_path: Path) -> None:
    """Open EURUSD, then ask for EURUSD again."""
    engine = _engine(tmp_path)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert engine.broker.positions()
    reply = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 3))
    assert reply == "refused: already_in_symbol"
    rec = _reject(engine)
    assert rec["reason"] == "already_in_symbol"
    assert rec["source"] == "telegram"
    assert rec["stage"] == "stage"
    assert rec["symbol"] == "EURUSD"
    engine.stop()


def test_size_zero_is_journaled_with_the_name(tmp_path: Path) -> None:
    """A risk budget too small to buy even the broker minimum lot."""
    engine = _engine(tmp_path, risk_pct=1e-09)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    assert reply == "refused: size_zero"
    rec = _reject(engine)
    assert rec["reason"] == "size_zero"
    assert rec["source"] == "telegram"
    engine.stop()


def test_margin_buffer_is_journaled_with_the_name(tmp_path: Path) -> None:
    """Leverage 10 lets one 0.55-lot leg eat past the free-margin floor."""
    engine = _engine(tmp_path, leverage=10, max_positions=5)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    acct = engine.broker.account()
    assert acct.margin > 0
    assert acct.margin_free / acct.equity < engine.cfg.risk.min_free_margin_pct
    reply = engine.handle_command(TgCommand("1", 1, "/buy GBPUSD", 3))
    assert reply == "refused: margin_buffer"
    rec = _reject(engine)
    assert rec["reason"] == "margin_buffer"
    assert rec["symbol"] == "GBPUSD"
    engine.stop()


def test_trade_not_allowed_is_journaled_with_the_name(tmp_path: Path) -> None:
    """The terminal itself refuses; the circuit reports it before any sizing."""
    engine = _engine(tmp_path, trade_allowed=False)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    assert reply == "refused: trade_not_allowed"
    rec = _reject(engine)
    assert rec["reason"] == "trade_not_allowed"
    engine.stop()


def test_max_positions_is_journaled_with_the_name(tmp_path: Path) -> None:
    """Three symbols filled, a fourth asked for.

    The currency cap is lifted so this case can only fail on the slot count;
    three USD-quote legs would otherwise trip currency_exposure first, which is
    its own test below.
    """
    engine = _engine(tmp_path, max_currency_exposure=99)
    engine.start()
    for i, symbol in enumerate(("EURUSD", "GBPUSD", "AUDUSD")):
        engine.handle_command(TgCommand("1", 1, "/buy " + symbol, 2 * i + 1))
        engine.handle_command(TgCommand("1", 1, "/confirm", 2 * i + 2))
    assert len(engine.broker.positions(magic=engine.cfg.risk.magic)) == 3
    reply = engine.handle_command(TgCommand("1", 1, "/buy USDCHF", 9))
    assert reply == "refused: max_positions"
    rec = _reject(engine)
    assert rec["reason"] == "max_positions"
    assert rec["stage"] == "stage"
    engine.stop()


def test_currency_exposure_is_journaled_with_the_name(tmp_path: Path) -> None:
    """Two long USD-quote legs; a third would be three deep on one currency."""
    engine = _engine(tmp_path, max_positions=5)
    engine.start()
    for i, symbol in enumerate(("EURUSD", "GBPUSD")):
        engine.handle_command(TgCommand("1", 1, "/buy " + symbol, 2 * i + 1))
        engine.handle_command(TgCommand("1", 1, "/confirm", 2 * i + 2))
    assert len(engine.broker.positions(magic=engine.cfg.risk.magic)) == 2
    reply = engine.handle_command(TgCommand("1", 1, "/buy AUDUSD", 7))
    assert reply == "refused: currency_exposure"
    rec = _reject(engine)
    assert rec["reason"] == "currency_exposure"
    engine.stop()


def test_spread_too_wide_is_journaled_with_the_name(tmp_path: Path) -> None:
    """The live spread measured against ATR, at the shipped default fraction."""
    engine = _engine(tmp_path, max_spread_atr_frac=0.0001)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    assert reply == "refused: spread_too_wide"
    rec = _reject(engine)
    assert rec["reason"] == "spread_too_wide"
    engine.stop()


def test_outside_session_is_journaled_on_the_auto_leg(tmp_path: Path) -> None:
    """The ONLY path that can produce this reason.

    The desk passes manual=True, which bypasses the session window by design,
    so outside_session is unreachable from every operator command. The auto
    leg times itself off the last bar, so the bar clock is the lever.
    """
    engine = _engine(tmp_path, session=True)
    engine.start()
    saturday = datetime(2024, 1, 6, 12, 0, tzinfo=timezone.utc)
    n = 250
    start_ts = int(saturday.timestamp()) - (n - 1) * 3600
    bars = generate_bars(n, drift=0.0006, vol=0.0002, seed=7, start_ts=start_ts)
    assert bars[-1].time == int(saturday.timestamp())
    engine.broker.seed_bars("EURUSD", bars)
    engine.replay_symbol("EURUSD", bars)
    rec = _reject(engine)
    assert rec["reason"] == "outside_session"
    assert rec["source"] == "auto"
    assert rec["stage"] == "signal"
    engine.stop()


def test_the_staging_gate_block_is_journaled_with_the_name(tmp_path: Path) -> None:
    """circuit_reason() is the gate deciding whether the LLM may stage at all.

    #11 named it as missing several branches. On the advice path its verdict
    leaves an advice_circuit_block record, so the reason is assertable rather
    than only visible inside a prose blob.
    """
    engine = _engine(tmp_path)
    engine.start()
    engine.risk.write_halt_file("operator")
    assert engine.advice_circuit_reason() == "halt_file"
    engine.stop()


# --- 5. the lifecycle around a reason, not just its first firing ------------


def test_a_new_utc_day_clears_the_daily_loss_refusal(tmp_path: Path) -> None:
    """daily_loss is sticky until the next UTC day, and only daily_loss is.

    A reason that can be named but never cleared is a different defect from a
    reason that is never named, so the roster asserts both directions.
    """
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    rm.observe(_acct(10_000), WED_NOON)
    assert _gate(rm, account=_acct(9_700)).reason == "daily_loss"
    assert rm.halt_reason == "daily_loss"
    thursday = datetime(2024, 1, 4, 12, 0, tzinfo=timezone.utc)
    rm.observe(_acct(9_700), thursday)
    assert rm.halt_reason == "", "a new UTC day must clear the daily budget"
    assert _gate(rm, account=_acct(9_700), now=thursday).reason == "ok"


def test_a_new_utc_day_does_not_clear_max_drawdown(tmp_path: Path) -> None:
    """The negative half: the drawdown halt outlives the day boundary."""
    rm = RiskManager(_cfg(tmp_path), halt_dir=tmp_path)
    thursday = datetime(2024, 1, 4, 12, 0, tzinfo=timezone.utc)
    friday = datetime(2024, 1, 5, 12, 0, tzinfo=timezone.utc)
    rm.observe(_acct(10_000), WED_NOON)
    rm.observe(_acct(9_000), thursday)
    assert _gate(rm, account=_acct(8_900), now=thursday).reason == "max_drawdown"
    rm.observe(_acct(8_900), friday)
    assert rm.halt_reason == "max_drawdown"
    assert _gate(rm, account=_acct(8_900), now=friday).reason == "max_drawdown"


def test_a_wiped_account_is_refused_before_the_margin_ratio(tmp_path: Path) -> None:
    """risk.py:329 has a false branch that cannot be taken. Third finding.

    `if account.equity > 0:` guards a division, so the interesting case is
    equity <= 0. No such account can reach line 329, because the daily-loss
    gate fires first for every one of them: on a fresh day observe() sets
    day_start_equity to the account equity, so daily_loss is 0 and the cap is
    day_start * 0.02, and `0 >= 0` is True at zero equity and True again for
    a negative day_start. The gate therefore fails CLOSED on a wiped account,
    which is the right outcome, and the division guard below it is
    unreachable rather than wrong.

    Pinned, not fixed: this is the correct refusal, just not the reason a
    reader of risk.py:329 would expect, and a coverage report will keep
    showing that branch partial forever.
    """
    for balance in (0.0, -500.0):
        rm = RiskManager(_cfg(tmp_path, initial_balance=balance), halt_dir=tmp_path)
        d = _gate(rm, account=_acct(balance, margin=0.0, margin_free=0.0))
        assert d.reason == "daily_loss", (
            "a wiped account now reaches the margin ratio; risk.py:329 has a"
            " reachable false branch and needs a real test: " + repr((balance, d))
        )
        assert d.halt, "a gate that cannot measure must fail closed"

