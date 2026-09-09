"""Risk-based position sizing. Lot size is a function of equity, stop distance,
and the symbol's tick value. Never round UP into extra risk.
"""

from __future__ import annotations

import math

from mt5_risk_bot.models import SymbolSpec


def ticks_between(a: float, b: float, spec: SymbolSpec) -> float:
    tick = spec.trade_tick_size or spec.point
    if tick <= 0:
        return 0.0
    return abs(a - b) / tick


def money_per_lot_at_stop(entry: float, sl: float, spec: SymbolSpec) -> float:
    ticks = ticks_between(entry, sl, spec)
    return ticks * spec.trade_tick_value


def normalize_volume(raw: float, spec: SymbolSpec) -> float:
    """Floor to volume_step, clamp to [min, max]. Returns 0 if below min."""
    if raw <= 0 or spec.volume_step <= 0:
        return 0.0
    stepped = math.floor(raw / spec.volume_step + 1e-12) * spec.volume_step
    digits = max(0, round(-math.log10(spec.volume_step))) if spec.volume_step < 1 else 0
    stepped = round(stepped, digits)
    if stepped < spec.volume_min - 1e-12:
        return 0.0
    if stepped > spec.volume_max:
        stepped = spec.volume_max
    return stepped


def lots_for_risk(
    equity: float,
    risk_pct: float,
    entry: float,
    sl: float,
    spec: SymbolSpec,
    *,
    max_risk_multiple: float = 1.0,
) -> float:
    """Lots such that a full stop-out loses about equity * risk_pct.

    If the broker minimum lot would risk more than equity * risk_pct *
    max_risk_multiple, return 0 (skip the trade). Never size up to min lot.
    """
    if equity <= 0 or risk_pct <= 0:
        return 0.0
    per_lot = money_per_lot_at_stop(entry, sl, spec)
    if per_lot <= 0:
        return 0.0
    budget = equity * risk_pct
    raw = budget / per_lot
    lots = normalize_volume(raw, spec)
    if lots <= 0:
        return 0.0
    actual_risk = per_lot * lots
    if actual_risk > budget * max_risk_multiple + 1e-9:
        return 0.0
    return lots
