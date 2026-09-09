"""Wilder ATR / ADX and EMA. Computed on closed bars only (no lookahead)."""

from __future__ import annotations

from mt5_risk_bot.models import Bar


def ema(values: list[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period must be > 0")
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def true_range(bars: list[Bar]) -> list[float]:
    if not bars:
        return []
    out = [bars[0].high - bars[0].low]
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        high = bars[i].high
        low = bars[i].low
        out.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return out


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return [float("nan")] * len(values)
    out = [float("nan")] * len(values)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def atr(bars: list[Bar], period: int = 14) -> list[float]:
    return _wilder_smooth(true_range(bars), period)


def adx(bars: list[Bar], period: int = 14) -> tuple[list[float], list[float], list[float]]:
    """Return (adx, plus_di, minus_di), Wilder 1978."""
    n = len(bars)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = bars[i].high - bars[i - 1].high
        down = bars[i - 1].low - bars[i].low
        if up > down and up > 0:
            plus_dm[i] = up
        if down > up and down > 0:
            minus_dm[i] = down
    atr_s = atr(bars, period)
    plus_s = _wilder_smooth(plus_dm, period)
    minus_s = _wilder_smooth(minus_dm, period)
    plus_di = [0.0] * n
    minus_di = [0.0] * n
    dx = [float("nan")] * n
    for i in range(n):
        a = atr_s[i]
        if a != a or a == 0:  # nan or zero
            continue
        plus_di[i] = 100.0 * plus_s[i] / a
        minus_di[i] = 100.0 * minus_s[i] / a
        denom = plus_di[i] + minus_di[i]
        if denom > 0:
            dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / denom
    adx_s = _wilder_smooth([0.0 if v != v else v for v in dx], period)
    return adx_s, plus_di, minus_di


def last_closed(values: list[float]) -> float:
    """Last finite value. Indicators on the forming bar are ignored by callers
    who pass bars[:-1] or who read index -2 themselves.
    """
    for v in reversed(values):
        if v == v:  # not nan
            return v
    return float("nan")
