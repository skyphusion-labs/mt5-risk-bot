from mt5_risk_bot.indicators import adx, atr, ema
from mt5_risk_bot.synthetic import generate_bars, generate_ranging


def test_ema_follows_uptrend() -> None:
    bars = generate_bars(200, drift=0.001, vol=0.0002, seed=1)
    closes = [b.close for b in bars]
    fast = ema(closes, 21)
    slow = ema(closes, 55)
    assert fast[-1] > slow[-1]
    assert fast[-1] > fast[50]


def test_atr_positive() -> None:
    bars = generate_bars(80, seed=3)
    values = atr(bars, 14)
    finite = [v for v in values if v == v]
    assert finite
    assert all(v > 0 for v in finite)


def test_adx_higher_in_trend_than_range() -> None:
    trend = generate_bars(400, drift=0.0008, vol=0.0003, seed=4)
    rang = generate_ranging(400, seed=4)
    t_adx, _, _ = adx(trend, 14)
    r_adx, _, _ = adx(rang, 14)
    t_last = next(v for v in reversed(t_adx) if v == v)
    r_last = next(v for v in reversed(r_adx) if v == v)
    assert t_last > r_last
    assert t_last > 20
