"""Crypto pairs must count toward the currency-exposure limit (issue #66).

Before this suite, a crypto code was not in the recognised table, so `BTCUSD`
did not resolve as a pair and the currency limit DID NOT APPLY to it. The
trade was allowed and the exclusion was recorded, which is the correct
behaviour for an instrument that genuinely is not a pair (`US30`) and the
WRONG behaviour for `BTCUSD`, whose USD leg is real USD exposure.

Conrad ruled on #66: "Yes, add the crypto pairs".

The failure being closed is a limit that cannot engage, not a limit that says
no. Long BTCUSD, long ETHUSD and long EURUSD are three short-USD tickets; the
limit exists so the book cannot hold several positions that are secretly the
same bet, and it counted one of those three.
"""

from __future__ import annotations

from datetime import datetime, timezone

from straightedge.broker.paper import default_spec
from straightedge.config import BotConfig
from straightedge.currencies import CRYPTO_CODES, CURRENCY_CODES, ISO_AND_METAL_CODES
from straightedge.models import Account, Position, Side, Signal, SignalKind, Tick
from straightedge.risk import (
    RiskManager,
    SYMBOL_FX,
    SYMBOL_NOT_FX,
    classify_symbol,
    currency_exposure,
    parse_fx,
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


def _evaluate(tmp_path, staged: str, positions: list[Position], **risk):
    cfg = _cfg(tmp_path)
    cfg.risk.max_positions = 9
    for key, value in risk.items():
        setattr(cfg.risk, key, value)
    rm = RiskManager(cfg, halt_dir=str(tmp_path))
    return rm.evaluate(
        account=_acct(),
        signal=_sig(staged),
        spec=default_spec(staged),
        tick=Tick(time=0, bid=1.0999, ask=1.1001),
        positions=positions,
        now=_now(),
    )


# --- the table resolves a crypto pair -----------------------------------------

def test_crypto_pairs_resolve_as_pairs() -> None:
    assert parse_fx("BTCUSD") == ("BTC", "USD")
    assert parse_fx("ETHUSD") == ("ETH", "USD")
    assert parse_fx("LTCUSD") == ("LTC", "USD")
    assert parse_fx("XRPUSD") == ("XRP", "USD")
    assert parse_fx("BCHUSD") == ("BCH", "USD")
    assert classify_symbol("BTCUSD") == SYMBOL_FX


def test_no_venue_spelling_is_hardcoded() -> None:
    """Every spelling reaches the same pair through the EXISTING resolver.

    Non-alphabetic characters are dropped and the first six alphabetic
    characters are taken, so the vendor suffix conventions #60 swept already
    cover crypto. `BTCUSDT` folds the Tether leg into USD, which is the
    intended reading: a USD-pegged stablecoin leg is USD exposure for the
    purpose of a correlation count.
    """
    for symbol in (
        "BTCUSD", "BTCUSDT", "BTCUSD.m", "BTCUSDm", "BTCUSD_i", "BTCUSDpro",
        "BTC/USD", "BTC.USD", "BTC_USD", "BTC-USD", "#BTCUSD", "BTCUSD-5",
    ):
        assert parse_fx(symbol) == ("BTC", "USD"), symbol
        assert classify_symbol(symbol) == SYMBOL_FX, symbol


def test_xbt_is_recognised_because_venues_spell_bitcoin_that_way() -> None:
    assert parse_fx("XBTUSD") == ("XBT", "USD")
    assert parse_fx("XBTUSDT") == ("XBT", "USD")


def test_the_crypto_codes_are_in_the_one_table_not_a_parallel_path() -> None:
    # Non-vacuity first. Every assertion after this one also holds for an EMPTY
    # crypto set, so without this line the test cannot go red on a dropped code.
    assert {"BTC", "ETH", "LTC", "XRP", "BCH"} <= CRYPTO_CODES
    assert CRYPTO_CODES <= CURRENCY_CODES
    assert CRYPTO_CODES.isdisjoint(ISO_AND_METAL_CODES)
    assert CURRENCY_CODES == ISO_AND_METAL_CODES | CRYPTO_CODES
    for code in sorted(CRYPTO_CODES):
        assert parse_fx(code + "USD") == (code, "USD"), code


# --- the bucketing decision, as an assertion ---------------------------------

def test_a_crypto_usd_leg_counts_exactly_like_an_fx_or_metal_usd_leg() -> None:
    """One bucket per code, crypto included. This is the #66 decision.

    Long BTC, long gold and long EUR are three expressions of short USD, so
    they consume one USD budget. The gate counts TICKETS, never money, so
    per-unit volatility is not what it compares and a separate crypto bucket
    would let a fourth short-USD ticket in unseen.
    """
    exposure = currency_exposure(
        [_pos(1, "BTCUSD"), _pos(2, "XAUUSD"), _pos(3, "EURUSD")]
    )
    assert exposure["USD"] == -3
    assert exposure["BTC"] == 1
    assert exposure["XAU"] == 1
    assert exposure["EUR"] == 1


def test_the_crypto_base_has_its_own_bucket_too() -> None:
    exposure = currency_exposure([_pos(1, "BTCUSD"), _pos(2, "BTCJPY")])
    assert exposure["BTC"] == 2
    assert exposure["USD"] == -1
    assert exposure["JPY"] == -1


# --- the limit engages -------------------------------------------------------

def test_a_crypto_stack_reaches_the_currency_limit(tmp_path) -> None:
    """Four long crypto pairs are four short-USD tickets, not one.

    RED before the change: nothing resolved, the exposure map was empty, and
    the trade was ALLOWED with all four symbols recorded as excluded.
    """
    decision = _evaluate(
        tmp_path,
        "XRPUSD",
        [_pos(1, "BTCUSD"), _pos(2, "ETHUSD"), _pos(3, "LTCUSD")],
        max_currency_exposure=2,
    )
    assert not decision.allowed
    assert decision.reason == "currency_exposure"
    assert decision.excluded_from_currency_limit == ()


def test_crypto_cannot_hide_short_usd_tickets_from_the_fx_book(tmp_path) -> None:
    """The mixed book is the dangerous one, and it passed before.

    RED before the change: BTCUSD and ETHUSD were excluded, the limit saw TWO
    USD legs where four existed, and the fourth short-USD ticket was allowed.
    """
    decision = _evaluate(
        tmp_path,
        "GBPUSD",
        [_pos(1, "BTCUSD"), _pos(2, "ETHUSD"), _pos(3, "EURUSD")],
        max_currency_exposure=2,
    )
    assert not decision.allowed
    assert decision.reason == "currency_exposure"
    assert decision.excluded_from_currency_limit == ()


def test_crypto_versus_crypto_is_capped_on_the_crypto_code(tmp_path) -> None:
    decision = _evaluate(
        tmp_path,
        "BTCEUR",
        [_pos(1, "BTCUSD"), _pos(2, "BTCJPY")],
        max_currency_exposure=2,
    )
    assert not decision.allowed
    assert decision.reason == "currency_exposure"


def test_a_crypto_pair_inside_the_limit_still_trades(tmp_path) -> None:
    """The gate must still be able to say yes, or it is not a limit."""
    decision = _evaluate(
        tmp_path,
        "BTCUSD",
        [_pos(1, "EURUSD")],
        max_currency_exposure=2,
    )
    assert decision.allowed, decision.reason
    assert decision.excluded_from_currency_limit == ()


def test_the_engine_no_longer_records_a_crypto_exclusion(tmp_path) -> None:
    """End to end: the `currency_limit_not_applicable` note was the symptom.

    RED before the change: the note was journaled for every BTCUSD stage. The
    position assertion is the vacuity guard, the same shape the US30 test uses:
    a suite where the strategy never fires would also emit no note.
    """
    from straightedge.broker.paper import PaperBroker
    from straightedge.engine import Engine
    from straightedge.synthetic import generate_bars

    cfg = _cfg(tmp_path)
    cfg.symbols = ["BTCUSD"]
    cfg.risk.max_spread_atr_frac = 10.0
    cfg.risk.halt_file = str(tmp_path / "HALT")
    broker = PaperBroker(balance=10_000)
    bars = generate_bars(400, drift=0.0004, vol=0.0002, seed=3)
    broker.seed_bars("BTCUSD", bars)
    engine = Engine(cfg, broker, halt_dir=str(tmp_path), now_fn=_now)
    engine.replay_symbol("BTCUSD", bars)
    rows = engine.journal.tail(50)
    assert broker.positions(), "the strategy never fired, so nothing was proved"
    assert not [r for r in rows if r.get("event") == "currency_limit_not_applicable"]


# --- controls: the new codes must not turn other instruments into pairs ------

NON_FX_INSTRUMENTS = (
    "US30", "USTEC", "USOIL", "UKOIL", "GER40", "GER30", "NAS100", "SPX500",
    "UK100", "JP225", "HK50", "AUS200", "FRA40", "ESP35", "ITA40", "NGAS",
    "COPPER", "COCOA", "SUGAR", "BRENT", "US30.cash", "USOIL.cash",
    "TSLA.us", "AAPL", "STOXX50", "BTC-PERP", "ETHER",
)


def test_adding_crypto_codes_does_not_read_other_instruments_as_pairs() -> None:
    """Regression control. Passes on BOTH sides of the change, by design.

    A wider table is a wider false-positive surface: a code that happens to be
    the first three letters of an index name would start consuming a bucket.
    `BTC-PERP` is in this list on purpose; a perpetual named that way resolves
    to BTC/PER, PER is not a code, so it stays not applicable.
    """
    for symbol in NON_FX_INSTRUMENTS:
        assert parse_fx(symbol) is None, symbol
        assert classify_symbol(symbol) == SYMBOL_NOT_FX, symbol


def test_crypto_tickers_longer_than_three_characters_stay_not_applicable() -> None:
    """KNOWN LIMIT, asserted so it is not mistaken for coverage.

    The resolver splits the first six alphabetic characters 3 and 3, so only a
    three-character ticker can resolve. DOGE, AVAX, LINK, MATIC and SHIB
    therefore remain allowed-and-recorded. Widening the split needs a
    longest-match parse against the table, which is a separate unit; see the
    comment in `currencies.py`.
    """
    for symbol in ("DOGEUSD", "AVAXUSD", "LINKUSD", "MATICUSD", "SHIBUSD"):
        assert parse_fx(symbol) is None, symbol
        assert classify_symbol(symbol) == SYMBOL_NOT_FX, symbol
