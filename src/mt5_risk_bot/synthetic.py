"""Deterministic OHLC generators for tests and offline backtests."""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone

from mt5_risk_bot.models import Bar


def generate_bars(
    n: int,
    *,
    start: float = 1.1000,
    drift: float = 0.00015,
    vol: float = 0.0008,
    seed: int = 1,
    start_ts: int | None = None,
    step: int = 3600,
    digits: int = 5,
) -> list[Bar]:
    rng = random.Random(seed)
    if start_ts is None:
        start_ts = int(datetime(2024, 1, 2, 7, 0, tzinfo=timezone.utc).timestamp())
    price = start
    bars: list[Bar] = []
    t = start_ts
    for i in range(n):
        shock = rng.gauss(0.0, 1.0)
        ret = drift + vol * shock
        o = price
        c = max(1e-6, price * (1.0 + ret))
        wiggle = abs(rng.gauss(0.0, vol * 0.4))
        high = max(o, c) * (1.0 + wiggle)
        low = min(o, c) * (1.0 - wiggle)
        if low <= 0:
            low = min(o, c) * 0.5
        bars.append(
            Bar(
                time=t,
                open=round(o, digits),
                high=round(high, digits),
                low=round(low, digits),
                close=round(c, digits),
                tick_volume=100 + i % 50,
                spread=10,
            )
        )
        price = c
        t += step
    return bars


def generate_ranging(
    n: int,
    *,
    start: float = 1.1000,
    amplitude: float = 0.004,
    seed: int = 2,
    start_ts: int | None = None,
    step: int = 3600,
) -> list[Bar]:
    rng = random.Random(seed)
    if start_ts is None:
        start_ts = int(datetime(2024, 1, 2, 7, 0, tzinfo=timezone.utc).timestamp())
    bars: list[Bar] = []
    t = start_ts
    for i in range(n):
        phase = 2 * math.pi * i / 40.0
        mid = start + amplitude * math.sin(phase)
        noise = rng.gauss(0.0, amplitude * 0.05)
        o = mid
        c = mid + noise
        high = max(o, c) + abs(rng.gauss(0, amplitude * 0.03))
        low = min(o, c) - abs(rng.gauss(0, amplitude * 0.03))
        bars.append(
            Bar(
                time=t,
                open=round(o, 5),
                high=round(high, 5),
                low=round(low, 5),
                close=round(c, 5),
                tick_volume=80,
                spread=10,
            )
        )
        t += step
    return bars
