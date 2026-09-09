from mt5_risk_bot.broker.paper import default_spec
from mt5_risk_bot.sizing import lots_for_risk, money_per_lot_at_stop, normalize_volume


def test_normalize_floors_not_ceils() -> None:
    spec = default_spec("EURUSD")
    assert normalize_volume(0.019, spec) == 0.01
    assert normalize_volume(0.009, spec) == 0.0
    assert normalize_volume(0.01, spec) == 0.01


def test_lots_risk_about_half_percent() -> None:
    spec = default_spec("EURUSD")
    entry, sl = 1.10000, 1.09500  # 50 pips = 500 points = 500 ticks
    lots = lots_for_risk(10_000, 0.005, entry, sl, spec)
    loss = money_per_lot_at_stop(entry, sl, spec) * lots
    assert lots > 0
    assert loss <= 10_000 * 0.005 + 1e-6
    assert loss >= 10_000 * 0.005 * 0.5


def test_zero_inputs() -> None:
    spec = default_spec("EURUSD")
    assert lots_for_risk(0, 0.005, 1.1, 1.09, spec) == 0.0
    assert lots_for_risk(10_000, 0.005, 1.1, 1.1, spec) == 0.0


def test_skip_when_min_lot_exceeds_risk() -> None:
    spec = default_spec("EURUSD")
    # Tiny account, wide stop: 0.01 lot would exceed 0.5% of $100.
    lots = lots_for_risk(100, 0.005, 1.10, 1.09, spec)
    assert lots == 0.0
