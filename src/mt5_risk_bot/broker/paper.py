"""In-process broker. Used for paper trading and backtests.

Fills at the current bid/ask. Stop and take-profit are evaluated against
each new bar using the conservative rule: if both SL and TP are touched
in the same bar, SL wins.

Pending limit/stop orders fill on tick (bid/ask vs price) or on bar
(high/low vs price). Each pending fills at most once per resolve.
"""

from __future__ import annotations

from mt5_risk_bot.constants import (
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_INVALID,
    TRADE_RETCODE_INVALID_PRICE,
    TRADE_RETCODE_INVALID_STOPS,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_NO_MONEY,
    TRADE_RETCODE_PLACED,
    TRADE_RETCODE_POSITION_CLOSED,
    TRADE_RETCODE_TRADE_DISABLED,
)
from mt5_risk_bot.models import Account, Bar, OrderResult, PendingOrder, Position, Side, SymbolSpec, Tick
from mt5_risk_bot.sizing import ticks_between

_PENDING_SIDE = {
    ORDER_TYPE_BUY_LIMIT: Side.BUY,
    ORDER_TYPE_SELL_LIMIT: Side.SELL,
    ORDER_TYPE_BUY_STOP: Side.BUY,
    ORDER_TYPE_SELL_STOP: Side.SELL,
}

# Filling IOC allowed (bit 2) so live-style filling selection works in paper.
_IOC = 2


def default_spec(name: str) -> SymbolSpec:
    n = name.upper()
    if n.endswith("JPY"):
        return SymbolSpec(
            name=n,
            digits=3,
            point=0.001,
            trade_tick_size=0.001,
            trade_tick_value=0.667,
            trade_contract_size=100_000,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_stops_level=10,
            trade_freeze_level=0,
            filling_mode=_IOC,
            currency_base=n[:3],
            currency_profit="JPY",
            currency_margin="USD",
            spread=20,
        )
    return SymbolSpec(
        name=n,
        digits=5,
        point=0.00001,
        trade_tick_size=0.00001,
        trade_tick_value=1.0,
        trade_contract_size=100_000,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_stops_level=10,
        trade_freeze_level=0,
        filling_mode=_IOC,
        currency_base=n[:3] if len(n) >= 6 else "EUR",
        currency_profit=n[3:6] if len(n) >= 6 else "USD",
        currency_margin="USD",
        spread=10,
    )


class PaperBroker:
    def __init__(
        self,
        *,
        balance: float = 10_000.0,
        leverage: int = 100,
        currency: str = "USD",
        specs: dict[str, SymbolSpec] | None = None,
        bars: dict[str, list[Bar]] | None = None,
        trade_allowed: bool = True,
    ) -> None:
        self._balance = float(balance)
        self._leverage = leverage
        self._currency = currency
        self._specs = specs or {}
        self._bars: dict[str, list[Bar]] = bars or {}
        self._positions: dict[int, Position] = {}
        self._orders: dict[int, PendingOrder] = {}
        self._next_ticket = 1
        self._trade_allowed = trade_allowed
        self._connected = False
        self._clock = 0

    def seed_bars(self, symbol: str, bars: list[Bar]) -> None:
        self._bars[symbol.upper()] = list(bars)
        if bars:
            self._clock = max(self._clock, bars[-1].time)

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def select_symbol(self, name: str) -> bool:
        spec = self.symbol(name)
        return spec is not None

    def symbol(self, name: str) -> SymbolSpec:
        key = name.upper()
        if key not in self._specs:
            self._specs[key] = default_spec(key)
        return self._specs[key]

    def _last_bar(self, name: str) -> Bar | None:
        bars = self._bars.get(name.upper(), [])
        return bars[-1] if bars else None

    def tick(self, name: str) -> Tick:
        spec = self.symbol(name)
        bar = self._last_bar(name)
        mid = bar.close if bar else 1.0
        half = (spec.spread * spec.point) / 2.0
        t = bar.time if bar else self._clock
        return Tick(time=t, bid=mid - half, ask=mid + half, last=mid)

    def rates(self, name: str, timeframe: int, count: int) -> list[Bar]:
        del timeframe
        bars = self._bars.get(name.upper(), [])
        if count <= 0:
            return []
        return bars[-count:]

    def _mark_to_market(self) -> None:
        for pos in self._positions.values():
            tick = self.tick(pos.symbol)
            spec = self.symbol(pos.symbol)
            px = tick.bid if pos.side is Side.BUY else tick.ask
            pos.price_current = px
            pos.profit = self._pnl(pos, px, spec)

    def _pnl(self, pos: Position, price: float, spec: SymbolSpec) -> float:
        ticks = ticks_between(pos.price_open, price, spec)
        if pos.side is Side.BUY:
            signed = ticks if price >= pos.price_open else -ticks
        else:
            signed = ticks if price <= pos.price_open else -ticks
        return signed * spec.trade_tick_value * pos.volume

    def account(self) -> Account:
        self._mark_to_market()
        profit = sum(p.profit for p in self._positions.values())
        equity = self._balance + profit
        margin = 0.0
        for p in self._positions.values():
            spec = self.symbol(p.symbol)
            notional = spec.trade_contract_size * p.volume * p.price_open
            margin += notional / max(self._leverage, 1)
        return Account(
            login=1,
            balance=self._balance,
            equity=equity,
            margin=margin,
            margin_free=equity - margin,
            profit=profit,
            leverage=self._leverage,
            currency=self._currency,
            trade_allowed=self._trade_allowed,
            trade_expert=True,
            server="paper",
            name="paper",
            trade_mode=0,
        )

    def positions(self, magic: int | None = None) -> list[Position]:
        self._mark_to_market()
        out = list(self._positions.values())
        if magic is not None:
            out = [p for p in out if p.magic == magic]
        return out

    def orders(self, magic: int | None = None) -> list[PendingOrder]:
        out = list(self._orders.values())
        if magic is not None:
            out = [o for o in out if o.magic == magic]
        return out

    def order_check(self, request: dict) -> OrderResult:
        return self._apply(request, commit=False)

    def order_send(self, request: dict) -> OrderResult:
        return self._apply(request, commit=True)

    def _apply(self, request: dict, *, commit: bool) -> OrderResult:
        if not self._trade_allowed:
            return OrderResult(retcode=TRADE_RETCODE_TRADE_DISABLED, comment="trade disabled", request=request)
        action = int(request.get("action", 0))
        if action == TRADE_ACTION_SLTP:
            return self._modify_sltp(request, commit=commit)
        if action == TRADE_ACTION_PENDING:
            return self._place_pending(request, commit=commit)
        if action == TRADE_ACTION_REMOVE:
            return self._remove_pending(request, commit=commit)
        if action != TRADE_ACTION_DEAL:
            return OrderResult(retcode=TRADE_RETCODE_INVALID, comment="unsupported action", request=request)
        if request.get("position"):
            return self._close(request, commit=commit)
        return self._open(request, commit=commit)

    def _open(self, request: dict, *, commit: bool) -> OrderResult:
        symbol = str(request.get("symbol", ""))
        volume = float(request.get("volume", 0))
        order_type = int(request.get("type", -1))
        spec = self.symbol(symbol)
        if volume < spec.volume_min or volume > spec.volume_max:
            return OrderResult(retcode=TRADE_RETCODE_INVALID_VOLUME, comment="volume", request=request)
        tick = self.tick(symbol)
        side = Side.BUY if order_type == ORDER_TYPE_BUY else Side.SELL
        price = tick.ask if side is Side.BUY else tick.bid
        sl = float(request.get("sl", 0) or 0)
        tp = float(request.get("tp", 0) or 0)
        if sl <= 0:
            return OrderResult(retcode=TRADE_RETCODE_INVALID_STOPS, comment="sl required", request=request)
        min_dist = spec.min_stop_distance()
        if abs(price - sl) < min_dist - spec.point / 2:
            return OrderResult(retcode=TRADE_RETCODE_INVALID_STOPS, comment="stops_level", request=request)
        acct = self.account()
        notional = spec.trade_contract_size * volume * price
        need = notional / max(self._leverage, 1)
        if need > acct.margin_free:
            return OrderResult(retcode=TRADE_RETCODE_NO_MONEY, comment="margin", request=request)
        ticket = self._next_ticket
        if commit:
            self._next_ticket += 1
            pos = Position(
                ticket=ticket,
                symbol=symbol.upper(),
                side=side,
                volume=volume,
                price_open=spec.normalize_price(price),
                sl=spec.normalize_price(sl),
                tp=spec.normalize_price(tp) if tp else 0.0,
                price_current=spec.normalize_price(price),
                profit=0.0,
                magic=int(request.get("magic", 0) or 0),
                comment=str(request.get("comment", "")),
                time=tick.time,
                identifier=ticket,
            )
            self._positions[ticket] = pos
        return OrderResult(
            retcode=TRADE_RETCODE_DONE,
            comment="Done",
            deal=ticket if commit else 0,
            order=ticket if commit else 0,
            volume=volume,
            price=spec.normalize_price(price),
            bid=tick.bid,
            ask=tick.ask,
            request=request,
        )

    def _close(self, request: dict, *, commit: bool) -> OrderResult:
        ticket = int(request["position"])
        pos = self._positions.get(ticket)
        if pos is None:
            return OrderResult(retcode=TRADE_RETCODE_POSITION_CLOSED, comment="gone", request=request)
        volume = float(request.get("volume", pos.volume))
        if volume > pos.volume + 1e-12:
            return OrderResult(retcode=TRADE_RETCODE_INVALID_VOLUME, comment="close volume", request=request)
        spec = self.symbol(pos.symbol)
        tick = self.tick(pos.symbol)
        px = tick.bid if pos.side is Side.BUY else tick.ask
        pnl = self._pnl(pos, px, spec) * (volume / pos.volume)
        if commit:
            self._balance += pnl
            if abs(volume - pos.volume) < 1e-12:
                del self._positions[ticket]
            else:
                pos.volume = round(pos.volume - volume, 8)
        return OrderResult(
            retcode=TRADE_RETCODE_DONE,
            comment="Done",
            deal=ticket,
            order=ticket,
            volume=volume,
            price=spec.normalize_price(px),
            bid=tick.bid,
            ask=tick.ask,
            request=request,
        )

    def _modify_sltp(self, request: dict, *, commit: bool) -> OrderResult:
        ticket = int(request.get("position") or 0)
        pos = self._positions.get(ticket)
        if pos is None:
            return OrderResult(retcode=TRADE_RETCODE_POSITION_CLOSED, comment="gone", request=request)
        spec = self.symbol(pos.symbol)
        sl = float(request.get("sl", pos.sl) or 0)
        tp = float(request.get("tp", pos.tp) or 0)
        if commit:
            pos.sl = spec.normalize_price(sl)
            pos.tp = spec.normalize_price(tp) if tp else 0.0
        return OrderResult(retcode=TRADE_RETCODE_DONE, comment="Done", order=ticket, request=request)

    def _place_pending(self, request: dict, *, commit: bool) -> OrderResult:
        symbol = str(request.get("symbol", ""))
        volume = float(request.get("volume", 0))
        type_code = int(request.get("type", -1))
        side = _PENDING_SIDE.get(type_code)
        if side is None:
            return OrderResult(retcode=TRADE_RETCODE_INVALID, comment="pending type", request=request)
        spec = self.symbol(symbol)
        if volume < spec.volume_min or volume > spec.volume_max:
            return OrderResult(retcode=TRADE_RETCODE_INVALID_VOLUME, comment="volume", request=request)
        price = float(request.get("price", 0) or 0)
        if price <= 0:
            return OrderResult(retcode=TRADE_RETCODE_INVALID_PRICE, comment="price", request=request)
        sl = float(request.get("sl", 0) or 0)
        tp = float(request.get("tp", 0) or 0)
        tick = self.tick(symbol)
        ticket = self._next_ticket
        if commit:
            self._next_ticket += 1
            self._orders[ticket] = PendingOrder(
                ticket=ticket,
                symbol=symbol.upper(),
                side=side,
                volume=volume,
                price=spec.normalize_price(price),
                sl=spec.normalize_price(sl) if sl else 0.0,
                tp=spec.normalize_price(tp) if tp else 0.0,
                magic=int(request.get("magic", 0) or 0),
                comment=str(request.get("comment", "")),
                type_code=type_code,
                time=tick.time,
            )
        return OrderResult(
            retcode=TRADE_RETCODE_PLACED,
            comment="placed",
            order=ticket if commit else 0,
            volume=volume,
            price=spec.normalize_price(price),
            bid=tick.bid,
            ask=tick.ask,
            request=request,
        )

    def _remove_pending(self, request: dict, *, commit: bool) -> OrderResult:
        ticket = int(request.get("order") or 0)
        order = self._orders.get(ticket)
        if order is None:
            return OrderResult(retcode=TRADE_RETCODE_POSITION_CLOSED, comment="gone", request=request)
        if commit:
            del self._orders[ticket]
        return OrderResult(
            retcode=TRADE_RETCODE_DONE,
            comment="Done",
            order=ticket,
            volume=order.volume,
            price=order.price,
            request=request,
        )

    def _pending_hit_tick(self, order: PendingOrder, tick: Tick) -> bool:
        t = order.type_code
        p = order.price
        if t == ORDER_TYPE_BUY_LIMIT:
            return tick.ask <= p
        if t == ORDER_TYPE_SELL_LIMIT:
            return tick.bid >= p
        if t == ORDER_TYPE_BUY_STOP:
            return tick.ask >= p
        if t == ORDER_TYPE_SELL_STOP:
            return tick.bid <= p
        return False

    def _pending_hit_bar(self, order: PendingOrder, bar: Bar) -> bool:
        t = order.type_code
        p = order.price
        if t == ORDER_TYPE_BUY_LIMIT:
            return bar.low <= p
        if t == ORDER_TYPE_SELL_LIMIT:
            return bar.high >= p
        if t == ORDER_TYPE_BUY_STOP:
            return bar.high >= p
        if t == ORDER_TYPE_SELL_STOP:
            return bar.low <= p
        return False

    def _fill_pending(self, order: PendingOrder, when: int) -> bool:
        spec = self.symbol(order.symbol)
        price = spec.normalize_price(order.price)
        acct = self.account()
        notional = spec.trade_contract_size * order.volume * price
        need = notional / max(self._leverage, 1)
        if need > acct.margin_free:
            return False
        del self._orders[order.ticket]
        self._positions[order.ticket] = Position(
            ticket=order.ticket,
            symbol=order.symbol,
            side=order.side,
            volume=order.volume,
            price_open=price,
            sl=spec.normalize_price(order.sl) if order.sl else 0.0,
            tp=spec.normalize_price(order.tp) if order.tp else 0.0,
            price_current=price,
            profit=0.0,
            magic=order.magic,
            comment=order.comment,
            time=when,
            identifier=order.ticket,
        )
        return True

    def resolve_pending_tick(self, symbol: str | None = None) -> list[int]:
        """Fill pending against the current bid/ask. Returns filled tickets."""
        return self.resolve_pending(symbol)

    def resolve_pending(self, symbol: str | None = None, bar: Bar | None = None) -> list[int]:
        """Fill pending against a bar (high/low) or the current tick.

        Each pending fills at most once even if the bar could trigger both
        a limit and a stop.
        """
        filled: list[int] = []
        key = symbol.upper() if symbol else None
        for ticket, order in list(self._orders.items()):
            if key is not None and order.symbol != key:
                continue
            if bar is not None:
                hit = self._pending_hit_bar(order, bar)
                when = bar.time
            else:
                tick = self.tick(order.symbol)
                hit = self._pending_hit_tick(order, tick)
                when = tick.time
            if not hit:
                continue
            if self._fill_pending(order, when):
                filled.append(ticket)
        return filled

    def on_bar(self, symbol: str, bar: Bar) -> list[int]:
        """Append a closed bar, fill pending, then resolve SL/TP.

        Returns closed position tickets. Pending that fill and then hit SL
        on the same bar close in this call.
        """
        key = symbol.upper()
        self._bars.setdefault(key, []).append(bar)
        self._clock = bar.time
        self.resolve_pending(key, bar)
        closed: list[int] = []
        for ticket, pos in list(self._positions.items()):
            if pos.symbol != key:
                continue
            hit = self._hit_level(pos, bar)
            if hit is None:
                continue
            spec = self.symbol(pos.symbol)
            pnl = self._pnl(pos, hit, spec)
            self._balance += pnl
            del self._positions[ticket]
            closed.append(ticket)
        return closed

    def _hit_level(self, pos: Position, bar: Bar) -> float | None:
        sl, tp = pos.sl, pos.tp
        if pos.side is Side.BUY:
            sl_hit = sl > 0 and bar.low <= sl
            tp_hit = tp > 0 and bar.high >= tp
            if sl_hit and tp_hit:
                return sl  # conservative: stop first
            if sl_hit:
                return sl
            if tp_hit:
                return tp
            return None
        sl_hit = sl > 0 and bar.high >= sl
        tp_hit = tp > 0 and bar.low <= tp
        if sl_hit and tp_hit:
            return sl
        if sl_hit:
            return sl
        if tp_hit:
            return tp
        return None
