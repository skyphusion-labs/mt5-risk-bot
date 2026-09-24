"""Shared domain types. Broker adapters map MT5 namedtuples onto these."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

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
    # Exposure left behind by a send that reported failure.
    #   0    the venue checked and nothing survived
    #   N    ticket N is still on the book and the desk was told otherwise
    #   None the venue did not answer, which is COULD NOT MEASURE
    # Venues that attach the stop in the same call as the entry have no
    # such window and leave this at 0.
    survivor_ticket: int | None = 0

    @property
    def ok(self) -> bool:
        from mt5_risk_bot.constants import RETCODE_OK

        return self.retcode in RETCODE_OK

    @property
    def measured(self) -> bool:
        """True when the broker actually answered.

        False means the call produced no result, so nothing was measured and
        neither `ok` nor `retcode` carries a verdict. A caller that gates on a
        pre-trade check must abort on this, because an unmeasured check is not
        a passed check.
        """
        from mt5_risk_bot.constants import RETCODE_UNKNOWN

        return self.retcode != RETCODE_UNKNOWN

    @classmethod
    def unknown(cls, comment: str, request: dict[str, Any] | None = None) -> OrderResult:
        """No result came back: COULD NOT MEASURE, never PASSED."""
        from mt5_risk_bot.constants import RETCODE_UNKNOWN

        return cls(retcode=RETCODE_UNKNOWN, comment=comment, request=request or {})

    @classmethod
    def unchanged(cls) -> OrderResult:
        from mt5_risk_bot.constants import TRADE_RETCODE_DONE

        return cls(retcode=TRADE_RETCODE_DONE, comment="unchanged")

    @classmethod
    def invalid_stops(cls, comment: str) -> OrderResult:
        from mt5_risk_bot.constants import TRADE_RETCODE_INVALID_STOPS

        return cls(retcode=TRADE_RETCODE_INVALID_STOPS, comment=comment)


@dataclass(frozen=True)
class FlattenReport:
    """What a flatten sweep actually achieved. Counts, never a bare boolean.

    positions_requested         open positions read before the sweep (the denominator)
    positions_confirmed_closed  closes whose filled volume covered the whole position
    positions_closed_elsewhere  absent from the post-sweep read and never confirmed by
                                us, i.e. an SL/TP or a manual close landed between the
                                read and the sweep. Not exposure, so not an alarm.
    survivors                   tickets that may still carry exposure: present in the
                                post-sweep read, or a close that reported residual
                                volume, or unverifiable because the read failed
    residual                    the subset of survivors whose close claimed success
                                while filling less volume than requested
    measured                    False when the post-sweep read could not be taken. For
                                a flatten, COULD NOT MEASURE is an incomplete sweep,
                                never a clean one: every requested ticket is counted as
                                a survivor, because the broker that could not be read is
                                the same broker whose close confirmations would have to
                                be believed. The count is then an upper bound.

    The denominator is an identity, not a vibe:
    confirmed_closed + closed_elsewhere + |survivors from the requested set| == requested.
    Each instrument can only move survivors up, never down.
    """

    reason: str
    positions_requested: int = 0
    positions_confirmed_closed: int = 0
    positions_closed_elsewhere: int = 0
    survivors: tuple[int, ...] = ()
    residual: tuple[int, ...] = ()
    orders_requested: int = 0
    orders_confirmed_cancelled: int = 0
    orders_gone_elsewhere: int = 0
    order_survivors: tuple[int, ...] = ()
    measured: bool = True

    @property
    def survivor_count(self) -> int:
        return len(self.survivors)

    @property
    def complete(self) -> bool:
        return (
            self.measured
            and not self.survivors
            and not self.order_survivors
            and self.positions_confirmed_closed + self.positions_closed_elsewhere
            == self.positions_requested
            and self.orders_confirmed_cancelled + self.orders_gone_elsewhere
            == self.orders_requested
        )

    def summary(self) -> str:
        """One line a human can act on. Never a fixed string."""
        if self.complete:
            extra = ""
            if self.positions_closed_elsewhere:
                extra = f" ({self.positions_closed_elsewhere} closed elsewhere)"
            return (
                f"flattened {self.positions_confirmed_closed}/{self.positions_requested} "
                f"positions{extra}, cancelled "
                f"{self.orders_confirmed_cancelled}/{self.orders_requested} orders; halted."
            )
        bits = [f"FLATTEN INCOMPLETE: {self.survivor_count} still open"]
        if self.survivors:
            bits.append("(" + ", ".join(f"#{t}" for t in self.survivors) + ")")
        bits.append(
            f"positions requested={self.positions_requested} "
            f"confirmed_closed={self.positions_confirmed_closed}."
        )
        if self.residual:
            bits.append(
                "partial fill left residual volume on "
                + ", ".join(f"#{t}" for t in self.residual)
                + "."
            )
        if self.order_survivors:
            bits.append(
                f"{len(self.order_survivors)} working order(s) not cancelled: "
                + ", ".join(f"#{t}" for t in self.order_survivors)
                + "."
            )
        if not self.measured:
            bits.append("COULD NOT MEASURE the post-sweep state; treated as incomplete.")
        bits.append("HALTED; no new entries. Check the terminal.")
        return " ".join(bits)


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
    #: Opening sends and advice turns already spent inside `day_key`. Durable
    #: for the same reason the loss budget is: a cap a restart clears is not a
    #: cap, and a crash loop would hand out a fresh allowance every time.
    trades_today: int = 0
    advice_turns_today: int = 0
