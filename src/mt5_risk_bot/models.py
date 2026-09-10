"""Shared domain types. Broker adapters map MT5 namedtuples onto these."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def order_type(self) -> int:
        from mt5_risk_bot.constants import ORDER_TYPE_BUY, ORDER_TYPE_SELL

        return ORDER_TYPE_BUY if self is Side.BUY else ORDER_TYPE_SELL

    @property
    def close_type(self) -> int:
        from mt5_risk_bot.constants import ORDER_TYPE_BUY, ORDER_TYPE_SELL

        return ORDER_TYPE_SELL if self is Side.BUY else ORDER_TYPE_BUY

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class SignalKind(str, Enum):
    BUY = "buy"
    SELL = "sell"
    FLAT = "flat"


@dataclass(frozen=True)
class MarketOrder:
    """Venue-neutral market send. Adapters map this to MT5, IBKR, etc."""

    symbol: str
    side: Side
    volume: float
    sl: float = 0.0
    tp: float = 0.0
    comment: str = ""
    magic: int = 0
    deviation: int = 20
    ticket: int | None = None


@dataclass(frozen=True)
class WorkingOrder:
    """Venue-neutral working (limit/stop) send."""

    symbol: str
    side: Side
    kind: str
    volume: float
    price: float
    sl: float = 0.0
    tp: float = 0.0
    comment: str = ""
    magic: int = 0
    ticket: int | None = None


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 0
    spread: int = 0
    real_volume: int = 0


@dataclass(frozen=True)
class Tick:
    time: int
    bid: float
    ask: float
    last: float = 0.0
    volume: int = 0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class Account:
    login: int
    balance: float
    equity: float
    margin: float
    margin_free: float
    profit: float
    leverage: int
    currency: str
    trade_allowed: bool = True
    trade_expert: bool = True
    server: str = ""
    name: str = ""
    fifo_close: bool = False
    credit: float = 0.0
    margin_level: float = 0.0
    trade_mode: int = 0  # 0 demo, 1 contest, 2 real


@dataclass(frozen=True)
class SymbolSpec:
    name: str
    digits: int
    point: float
    trade_tick_size: float
    trade_tick_value: float
    trade_contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    trade_stops_level: int
    trade_freeze_level: int
    filling_mode: int
    currency_base: str
    currency_profit: str
    currency_margin: str
    trade_mode: int = 4  # full
    visible: bool = True
    spread: int = 0

    def normalize_price(self, price: float) -> float:
        return round(price, self.digits)

    def min_stop_distance(self) -> float:
        return self.trade_stops_level * self.point


@dataclass(frozen=True)
class PendingOrder:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price: float
    sl: float
    tp: float
    magic: int = 0
    comment: str = ""
    kind: str = ""  # "limit"|"stop"
    time: int = 0


@dataclass
class Position:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    sl: float
    tp: float
    price_current: float
    profit: float
    swap: float = 0.0
    magic: int = 0
    comment: str = ""
    time: int = 0
    identifier: int = 0

    @property
    def risk_distance(self) -> float:
        if self.sl <= 0:
            return 0.0
        return abs(self.price_open - self.sl)


@dataclass(frozen=True)
class OrderResult:
    retcode: int
    comment: str = ""
    deal: int = 0
    order: int = 0
    volume: float = 0.0
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    request: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        from mt5_risk_bot.constants import RETCODE_OK

        return self.retcode in RETCODE_OK


@dataclass(frozen=True)
class Signal:
    kind: SignalKind
    symbol: str
    entry: float
    sl: float
    tp: float
    atr: float
    reason: str = ""
    fast_ema: float = 0.0
    slow_ema: float = 0.0
    adx: float = 0.0
    pending_kind: str = ""

    @property
    def side(self) -> Side | None:
        if self.kind is SignalKind.BUY:
            return Side.BUY
        if self.kind is SignalKind.SELL:
            return Side.SELL
        return None

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.sl)

    @property
    def reward_distance(self) -> float:
        return abs(self.tp - self.entry)

    @property
    def rr(self) -> float:
        if self.risk_distance <= 0:
            return 0.0
        return self.reward_distance / self.risk_distance

    def reprice(self, entry: float, spec: SymbolSpec) -> Signal:
        """Keep ATR distances, move entry to the live bid/ask."""
        if self.kind is SignalKind.BUY:
            sl = spec.normalize_price(entry - (self.entry - self.sl))
            tp = spec.normalize_price(entry + (self.tp - self.entry))
        elif self.kind is SignalKind.SELL:
            sl = spec.normalize_price(entry + (self.sl - self.entry))
            tp = spec.normalize_price(entry - (self.entry - self.tp))
        else:
            return self
        return Signal(
            kind=self.kind,
            symbol=self.symbol,
            entry=spec.normalize_price(entry),
            sl=sl,
            tp=tp,
            atr=self.atr,
            reason=self.reason,
            fast_ema=self.fast_ema,
            slow_ema=self.slow_ema,
            adx=self.adx,
            pending_kind=self.pending_kind,
        )


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    volume: float = 0.0
    halt: bool = False
    flatten: bool = False


@dataclass
class EquitySnapshot:
    time: int
    balance: float
    equity: float
    peak_equity: float
    day_start_equity: float
    day_key: str
