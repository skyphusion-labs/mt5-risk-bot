"""ADX-filtered EMA regime. Stops and targets in ATR units."""

from __future__ import annotations

from mt5_risk_bot.config import StrategyConfig
from mt5_risk_bot.indicators import adx, atr, ema
from mt5_risk_bot.models import Bar, Position, Side, Signal, SignalKind, SymbolSpec


def _last(xs: list[float]) -> float:
    for v in reversed(xs):
        if v == v:
            return v
    return float("nan")


class TrendStrategy:
    def __init__(self, cfg: StrategyConfig) -> None:
        self.cfg = cfg

    def needed_bars(self) -> int:
        return max(self.cfg.slow_ema, self.cfg.adx_period * 3, self.cfg.atr_period * 3) + 5

    def signal(self, symbol: str, bars: list[Bar], spec: SymbolSpec) -> Signal:
        need = self.needed_bars()
        if len(bars) < need:
            return Signal(SignalKind.FLAT, symbol, 0, 0, 0, 0, reason="warmup")

        closes = [b.close for b in bars]
        fast = ema(closes, self.cfg.fast_ema)
        slow = ema(closes, self.cfg.slow_ema)
        atr_s = atr(bars, self.cfg.atr_period)
        adx_s, plus_di, minus_di = adx(bars, self.cfg.adx_period)

        f0 = fast[-1]
        s0 = slow[-1]
        a0 = _last(atr_s)
        adx0 = _last(adx_s)
        pdi = plus_di[-1]
        mdi = minus_di[-1]
        close = bars[-1].close

        if a0 != a0 or a0 <= 0 or adx0 != adx0:
            return Signal(SignalKind.FLAT, symbol, 0, 0, 0, 0, reason="indicator_nan")
        if adx0 < self.cfg.adx_min:
            return Signal(
                SignalKind.FLAT, symbol, close, 0, 0, a0, reason="no_trend", adx=adx0
            )

        long_reg = f0 > s0 and pdi > mdi
        short_reg = f0 < s0 and mdi > pdi
        if long_reg:
            kind = SignalKind.BUY
        elif short_reg:
            kind = SignalKind.SELL
        else:
            return Signal(
                SignalKind.FLAT,
                symbol,
                close,
                0,
                0,
                a0,
                reason="no_regime",
                fast_ema=f0,
                slow_ema=s0,
                adx=adx0,
            )
        stop_dist = self.cfg.atr_stop_mult * a0
        tp_dist = self.cfg.atr_tp_mult * a0
        if kind is SignalKind.BUY:
            entry = close
            sl = spec.normalize_price(entry - stop_dist)
            tp = spec.normalize_price(entry + tp_dist)
        else:
            entry = close
            sl = spec.normalize_price(entry + stop_dist)
            tp = spec.normalize_price(entry - tp_dist)
        return Signal(
            kind=kind,
            symbol=symbol,
            entry=spec.normalize_price(entry),
            sl=sl,
            tp=tp,
            atr=a0,
            reason="regime",
            fast_ema=f0,
            slow_ema=s0,
            adx=adx0,
        )

    def manage(self, pos: Position, bars: list[Bar], spec: SymbolSpec) -> tuple[float, float]:
        """Return (new_sl, new_tp). Never loosens the stop."""
        if len(bars) < self.cfg.atr_period + 1:
            return pos.sl, pos.tp
        a0 = _last(atr(bars, self.cfg.atr_period))
        if a0 != a0 or a0 <= 0 or pos.risk_distance <= 0:
            return pos.sl, pos.tp
        r_dist = pos.risk_distance
        tick_px = bars[-1].close
        if pos.side is Side.BUY:
            favorable = tick_px - pos.price_open
        else:
            favorable = pos.price_open - tick_px
        r_mult = favorable / r_dist if r_dist else 0.0
        sl = pos.sl
        tp = pos.tp
        if r_mult >= self.cfg.breakeven_r:
            be = pos.price_open
            if pos.side is Side.BUY:
                sl = max(sl, be)
            else:
                sl = min(sl, be) if sl > 0 else be
        if r_mult >= self.cfg.trail_r:
            trail = self.cfg.trail_atr_mult * a0
            if pos.side is Side.BUY:
                candidate = spec.normalize_price(tick_px - trail)
                sl = max(sl, candidate)
            else:
                candidate = spec.normalize_price(tick_px + trail)
                sl = min(sl, candidate) if sl > 0 else candidate
        return spec.normalize_price(sl), spec.normalize_price(tp) if tp else 0.0
