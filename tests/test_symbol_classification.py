"""parse_fx must classify or refuse, never silently exempt (issue #10)."""

from __future__ import annotations

import itertools
import string
from datetime import datetime, timezone

import pytest

from mt5_risk_bot.broker.paper import default_spec
from mt5_risk_bot.config import BotConfig
from mt5_risk_bot.currencies import CURRENCY_CODES
from mt5_risk_bot.models import Account, Position, Side, Signal, SignalKind, Tick
from mt5_risk_bot.risk import (
    RiskManager,
    UnclassifiedSymbol,
    currency_exposure,
    classify_symbol,
    parse_fx,
    SYMBOL_FX,
    SYMBOL_NOT_FX,
)

CODES = ["".join(t) for t in itertools.product(string.ascii_uppercase, repeat=3)]

SUFFIX_CONVENTIONS = (
    "", "m", "M", "c", "C", "pro", "PRO", "mini", "micro", "ecn", "raw",
    "e", "i", "z", "r", "sb", ".a", ".m", ".r", ".pro", "_i", "_SB",
    "-5", "#", "+",
)


def _acct(equity: float = 10_000) -> Account:
    return Account(
        login=1,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=equity,
        profit=0.0,
        leverage=100,
        currency="USD",
        trade_allowed=True,
        trade_expert=True,
        trade_mode=0,
    )


def _sig(symbol: str = "EURUSD") -> Signal:
    return Signal(SignalKind.BUY, symbol, 1.10, 1.095, 1.1125, 0.003, reason="test")


def _pos(ticket: int, symbol: str, side: Side = Side.BUY) -> Position:
    return Position(ticket, symbol, side, 0.1, 1.1, 1.09, 1.12, 1.1, 0, magic=20260909)


def _cfg(tmp_path) -> BotConfig:
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.session.enabled = False
    return cfg


def _now() -> datetime:
    return datetime(2024, 1, 3, 12, 0, tzinfo=timezone.utc)


def test_m_bearing_codes_parse() -> None:
    assert parse_fx("USDMXN") == ("USD", "MXN")
    assert parse_fx("MXNJPY") == ("MXN", "JPY")
    assert parse_fx("EURMXN") == ("EUR", "MXN")
    assert parse_fx("USDMXNm") == ("USD", "MXN")
    assert parse_fx("USDMXN.m") == ("USD", "MXN")
    assert parse_fx("MXNJPYmini") == ("MXN", "JPY")


def test_existing_shapes_still_parse() -> None:
    assert parse_fx("EURUSD") == ("EUR", "USD")
    assert parse_fx("USDJPYm") == ("USD", "JPY")
    assert parse_fx("XAUUSD") == ("XAU", "USD")


def test_sweep_recognised_codes_parse_in_every_suffix_convention() -> None:
    bad: list[str] = []
    cases = 0
    for code in sorted(CURRENCY_CODES):
        for suf in SUFFIX_CONVENTIONS:
            cases += 2
            if parse_fx(code + "USD" + suf) != (code, "USD"):
                bad.append(code + "USD" + suf)
            if parse_fx("EUR" + code + suf) != ("EUR", code):
                bad.append("EUR" + code + suf)
    assert cases == len(CURRENCY_CODES) * len(SUFFIX_CONVENTIONS) * 2
    assert bad == [], f"{len(bad)} of {cases} failed to parse, first: {bad[:5]}"


def test_sweep_unrecognised_codes_are_not_treated_as_pairs() -> None:
    bad: list[str] = []
    cases = 0
    for trio in itertools.product(string.ascii_uppercase, repeat=3):
        code = "".join(trio)
        if code in CURRENCY_CODES:
            continue
        for suf in SUFFIX_CONVENTIONS:
            cases += 2
            for sym in (code + "USD" + suf, "EUR" + code + suf):
                if parse_fx(sym) is not None or classify_symbol(sym) != SYMBOL_NOT_FX:
                    bad.append(sym)
    assert cases == (17_576 - len(CURRENCY_CODES)) * len(SUFFIX_CONVENTIONS) * 2
    assert bad == [], f"{len(bad)} of {cases} wrongly read as a pair, first: {bad[:5]}"

def test_too_short_to_classify_returns_none() -> None:
    assert parse_fx("US30") is None
    assert parse_fx("GER40") is None
    assert parse_fx("EUR") is None
    assert parse_fx("") is None


def test_currency_exposure_counts_m_bearing_pairs() -> None:
    positions = [_pos(1, "USDMXN"), _pos(2, "MXNJPY", Side.SELL)]
    exp = currency_exposure(positions)
    assert exp["MXN"] == -2
    assert exp["USD"] == 1
    assert exp["JPY"] == 1


def test_currency_exposure_refuses_an_unclassifiable_symbol() -> None:
    with pytest.raises(UnclassifiedSymbol):
        currency_exposure([_pos(1, "US30")])
    with pytest.raises(UnclassifiedSymbol):
        currency_exposure([], extra=("US30", Side.BUY))


def test_m_bearing_positions_reach_the_currency_limit(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    cfg.risk.max_currency_exposure = 2
    rm = RiskManager(cfg, halt_dir=str(tmp_path))
    positions = [
        _pos(1, "USDMXN", Side.SELL),
        _pos(2, "EURMXN", Side.SELL),
        _pos(3, "GBPMXN", Side.SELL),
        _pos(4, "AUDMXN", Side.SELL),
    ]
    cfg.risk.max_positions = 9
    d = rm.evaluate(
        account=_acct(),
        signal=_sig("CADMXN"),
        spec=default_spec("CADMXN"),
        tick=Tick(time=0, bid=1.0999, ask=1.1001),
        positions=positions,
        now=_now(),
    )
    assert not d.allowed
    assert d.reason == "currency_exposure"


def test_classify_symbol_separates_fx_from_not_fx() -> None:
    assert classify_symbol("EURUSD") == SYMBOL_FX
    assert classify_symbol("EURUSDm") == SYMBOL_FX
    assert classify_symbol("XAUUSD") == SYMBOL_FX
    assert classify_symbol("USDMXN") == SYMBOL_FX
    assert classify_symbol("US30") == SYMBOL_NOT_FX
    assert classify_symbol("GER40") == SYMBOL_NOT_FX
    assert classify_symbol("USOIL") == SYMBOL_NOT_FX
    assert classify_symbol("EUR") == SYMBOL_NOT_FX


def test_non_fx_symbol_is_allowed_and_the_exclusion_is_named(tmp_path) -> None:
    rm = RiskManager(_cfg(tmp_path), halt_dir=str(tmp_path))
    d = rm.evaluate(
        account=_acct(),
        signal=_sig("US30"),
        spec=default_spec("US30"),
        tick=Tick(time=0, bid=1.0999, ask=1.1001),
        positions=[],
        now=_now(),
    )
    assert d.allowed, d.reason
    assert d.excluded_from_currency_limit == ("US30",)


def test_non_fx_open_position_is_excluded_not_refused(tmp_path) -> None:
    rm = RiskManager(_cfg(tmp_path), halt_dir=str(tmp_path))
    d = rm.evaluate(
        account=_acct(),
        signal=_sig("EURUSD"),
        spec=default_spec("EURUSD"),
        tick=Tick(time=0, bid=1.0999, ask=1.1001),
        positions=[_pos(1, "US30")],
        now=_now(),
    )
    assert d.allowed, d.reason
    assert d.excluded_from_currency_limit == ("US30",)


def test_non_fx_position_does_not_hide_a_real_fx_breach(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    cfg.risk.max_positions = 9
    rm = RiskManager(cfg, halt_dir=str(tmp_path))
    positions = [
        _pos(1, "US30"),
        _pos(2, "USDMXN", Side.SELL),
        _pos(3, "EURMXN", Side.SELL),
        _pos(4, "GBPMXN", Side.SELL),
        _pos(5, "AUDMXN", Side.SELL),
    ]
    d = rm.evaluate(
        account=_acct(),
        signal=_sig("CADMXN"),
        spec=default_spec("CADMXN"),
        tick=Tick(time=0, bid=1.0999, ask=1.1001),
        positions=positions,
        now=_now(),
    )
    assert not d.allowed
    assert d.reason == "currency_exposure"
    assert d.excluded_from_currency_limit == ("US30",)


def test_not_applicable_is_recorded_and_the_trade_proceeds(tmp_path) -> None:
    from mt5_risk_bot.broker.paper import PaperBroker
    from mt5_risk_bot.engine import Engine
    from mt5_risk_bot.synthetic import generate_bars

    cfg = _cfg(tmp_path)
    cfg.symbols = ["US30"]
    cfg.risk.max_spread_atr_frac = 10.0
    cfg.risk.halt_file = str(tmp_path / "HALT")
    broker = PaperBroker(balance=10_000)
    bars = generate_bars(400, drift=0.0004, vol=0.0002, seed=3)
    broker.seed_bars("US30", bars)
    engine = Engine(cfg, broker, halt_dir=str(tmp_path), now_fn=_now)
    engine.replay_symbol("US30", bars)
    rows = engine.journal.tail(50)
    notes = [r for r in rows if r.get("event") == "currency_limit_not_applicable"]
    assert notes, "the exclusion was not recorded, so the limit went quiet"
    assert notes[-1]["excluded"] == ["US30"]
    assert not [r for r in rows if r.get("event") == "reject"]
    assert broker.positions(), "a non-FX instrument must still be tradeable"


def test_sweep_short_stems_are_never_fx() -> None:
    tails = ("", "30", "40", "100", "500", ".cash")
    bad: list[str] = []
    cases = 0
    for n in (1, 2, 3):
        for t in itertools.product(string.ascii_uppercase, repeat=n):
            stem = "".join(t)
            for tail in tails:
                cases += 1
                sym = stem + tail
                if classify_symbol(sym) != SYMBOL_NOT_FX or parse_fx(sym) is not None:
                    bad.append(sym)
    assert cases == (26 + 676 + 17_576) * len(tails)
    assert bad == [], f"{len(bad)} of {cases} wrongly read as a pair, first: {bad[:5]}"


def test_dotted_instrument_is_not_a_pair() -> None:
    for sym in ("US30.cash", "USOIL.cash", "GER40.cash", "UK100.cash"):
        assert classify_symbol(sym) == SYMBOL_NOT_FX, sym
        assert parse_fx(sym) is None, sym


def test_unrecognised_first_six_is_not_a_pair() -> None:
    for sym in ("mEURUSD", "FXEURUSD", "US30.cash", "USOIL.cash", "UK100.cash"):
        assert parse_fx(sym) is None, sym
        assert classify_symbol(sym) == SYMBOL_NOT_FX, sym


def test_separator_inside_the_pair_still_parses() -> None:
    assert parse_fx("EUR.USD") == ("EUR", "USD")
    assert parse_fx("EUR/USD") == ("EUR", "USD")
    assert parse_fx("EUR_USD") == ("EUR", "USD")
    assert classify_symbol("EUR.USD") == SYMBOL_FX


def test_metals_parse_because_iso_assigns_them_codes() -> None:
    assert parse_fx("XAUUSD") == ("XAU", "USD")
    assert parse_fx("XAGUSD") == ("XAG", "USD")
    assert parse_fx("XAUUSDm") == ("XAU", "USD")


def test_sweep_separated_pairs_parse_because_the_table_confirms() -> None:
    seps = (".", "/", "_", "-", " ")
    bad: list[str] = []
    cases = 0
    codes = sorted(CURRENCY_CODES)
    for a in codes:
        for b in codes:
            for sep in seps:
                cases += 1
                if parse_fx(a + sep + b) != (a, b):
                    bad.append(a + sep + b)
    assert cases == len(codes) * len(codes) * len(seps)
    assert bad == [], f"{len(bad)} of {cases} failed to parse, first: {bad[:5]}"
