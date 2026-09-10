"""One path for live, paper, and backtest: strategy proposes, risk sizes, broker sends."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mt5_risk_bot.broker.base import Broker
from mt5_risk_bot.config import BotConfig
from mt5_risk_bot.constants import (
    ORDER_TIME_GTC,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_INVALID_STOPS,
    choose_filling,
)
from mt5_risk_bot.desk import Desk
from mt5_risk_bot.indicators import adx, ema, last_closed
from mt5_risk_bot.indicators import atr as atr_bars
from mt5_risk_bot.journal import Journal
from mt5_risk_bot.llm import Advisor
from mt5_risk_bot.models import Bar, OrderResult, PendingOrder, Position, Signal, SignalKind
from mt5_risk_bot.risk import RiskDecision, RiskManager
from mt5_risk_bot.strategy import TrendStrategy
from mt5_risk_bot.telegram import TelegramClient, TgCommand

_ORDER_TYPE_NAME = {
    ORDER_TYPE_BUY_LIMIT: "BUY_LIMIT",
    ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
    ORDER_TYPE_BUY_STOP: "BUY_STOP",
    ORDER_TYPE_SELL_STOP: "SELL_STOP",
}


class Engine:
    def __init__(
        self,
        cfg: BotConfig,
        broker: Broker,
        *,
        journal: Journal | None = None,
        halt_dir: str = ".",
        now_fn: Any = None,
        telegram: TelegramClient | None = None,
        advisor: Advisor | None = None,
    ) -> None:
        self.cfg = cfg
        self.broker = broker
        self.journal = journal or Journal(cfg.journal_path)
        self.risk = RiskManager(cfg, halt_dir=halt_dir)
        self.strategy = TrendStrategy(cfg.strategy)
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.last_bar_time: dict[str, int] = {}
        self.halted = False
        self.telegram = telegram
        self.advisor = advisor if advisor is not None else Advisor(cfg.advice)
        self.desk = Desk(self, self.advisor)
        self._seen_pos: set[int] | None = None
        self._opened_this_step: set[int] = set()
        self._closed_this_step: set[int] = set()

    def _emit(self, event: str, **fields: Any) -> None:
        self.journal.write(event, **fields)
        if self.telegram is None:
            return
        text = _format_event(event, fields)
        if text:
            self.telegram.notify(event, text)

    def start(self) -> None:
        self.broker.connect()
        for name in self.cfg.symbols:
            self.broker.select_symbol(name)
        acct = self.broker.account()
        self.risk.observe(acct, self.now_fn())
        self._emit(
            "start",
            mode=self.cfg.mode,
            login=acct.login,
            equity=acct.equity,
            server=acct.server,
            symbols=self.cfg.symbols,
        )
        self._seen_pos = {
            p.ticket for p in self.broker.positions(magic=self.cfg.risk.magic)
        }

    def stop(self) -> None:
        self._emit("stop")
        self.broker.disconnect()

    def flatten(self, reason: str) -> None:
        if getattr(self, "desk", None) is not None:
            self.desk.pending = None
        for order in list(self.broker.orders(magic=self.cfg.risk.magic)):
            self.broker.order_send({"action": TRADE_ACTION_REMOVE, "order": order.ticket})
        for pos in list(self.broker.positions(magic=self.cfg.risk.magic)):
            self._close(pos, reason)
        self.halted = True
        self._seen_pos = {
            p.ticket for p in self.broker.positions(magic=self.cfg.risk.magic)
        }

    def _apply_circuit(self, acct, now) -> bool:
        trip = self.risk.circuit(acct, now)
        if not trip.halt:
            return False
        self._emit("halt", reason=trip.reason, equity=acct.equity)
        if trip.flatten:
            self.flatten(trip.reason)
        return True

    def _close(self, pos: Position, reason: str, volume: float | None = None) -> OrderResult:
        tick = self.broker.tick(pos.symbol)
        spec = self.broker.symbol(pos.symbol)
        vol = pos.volume if volume is None else volume
        request = {
            "action": TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": vol,
            "type": pos.side.close_type,
            "position": pos.ticket,
            "price": tick.bid if pos.side.value == "buy" else tick.ask,
            "deviation": self.cfg.risk.deviation_points,
            "magic": self.cfg.risk.magic,
            "comment": reason[:31],
            "type_time": ORDER_TIME_GTC,
            "type_filling": choose_filling(spec.filling_mode),
        }
        result = self.broker.order_send(request)
        self._closed_this_step.add(pos.ticket)
        self._emit(
            "close",
            reason=reason,
            ticket=pos.ticket,
            symbol=pos.symbol,
            ok=result.ok,
            retcode=result.retcode,
            comment=result.comment,
            price=result.price,
            volume=vol,
            side=pos.side.value,
        )
        return result

    def _modify(self, pos: Position, sl: float, tp: float) -> OrderResult:
        if abs(sl - pos.sl) < 1e-12 and abs((tp or 0) - (pos.tp or 0)) < 1e-12:
            return OrderResult(retcode=TRADE_RETCODE_DONE, comment="unchanged")
        result = self.broker.order_send(
            {
                "action": TRADE_ACTION_SLTP,
                "symbol": pos.symbol,
                "position": pos.ticket,
                "sl": sl,
                "tp": tp,
            }
        )
        self.journal.write(
            "modify",
            ticket=pos.ticket,
            symbol=pos.symbol,
            sl=sl,
            tp=tp,
            ok=result.ok,
            retcode=result.retcode,
        )
        return result

    def _open(self, signal: Signal, volume: float) -> OrderResult:
        spec = self.broker.symbol(signal.symbol)
        tick = self.broker.tick(signal.symbol)
        side = signal.side
        assert side is not None
        price = tick.ask if side.value == "buy" else tick.bid
        request = {
            "action": TRADE_ACTION_DEAL,
            "symbol": signal.symbol,
            "volume": volume,
            "type": side.order_type,
            "price": price,
            "sl": signal.sl,
            "tp": signal.tp,
            "deviation": self.cfg.risk.deviation_points,
            "magic": self.cfg.risk.magic,
            "comment": self.cfg.comment[:31],
            "type_time": ORDER_TIME_GTC,
            "type_filling": choose_filling(spec.filling_mode),
        }
        check = self.broker.order_check(request)
        # Live order_check success is retcode 0; paper returns DONE (10009).
        if check.retcode not in (0,) and not check.ok:
            self._emit(
                "order_check_fail",
                symbol=signal.symbol,
                retcode=check.retcode,
                comment=check.comment,
            )
            return check
        result = self.broker.order_send(request)
        if result.ok:
            ticket = int(result.order or result.deal or 0)
            if ticket:
                self._opened_this_step.add(ticket)
        self._emit(
            "open",
            symbol=signal.symbol,
            side=side.value,
            volume=volume,
            sl=signal.sl,
            tp=signal.tp,
            ok=result.ok,
            retcode=result.retcode,
            order=result.order,
            price=result.price or price,
            reason=signal.reason,
            adx=signal.adx,
            atr=signal.atr,
        )
        return result

    def step_symbol(self, symbol: str) -> None:
        if self.halted:
            return
        need = self.strategy.needed_bars() + 2
        bars = self.broker.rates(symbol, self.cfg.strategy.timeframe_id, need)
        if not bars:
            return
        last_t = bars[-1].time
        prev = self.last_bar_time.get(symbol)
        if prev is None:
            # First poll: pin the last bar so we do not dump-trade history.
            self.last_bar_time[symbol] = last_t
            return
        if last_t <= prev:
            return
        self.last_bar_time[symbol] = last_t
        self._act(symbol, bars)

    def replay_symbol(self, symbol: str, bars: list[Bar]) -> None:
        if self.halted:
            return
        self._act(symbol, bars)

    def _act(self, symbol: str, bars: list[Bar]) -> None:
        now = datetime.fromtimestamp(bars[-1].time, tz=timezone.utc) if bars else self.now_fn()
        acct = self.broker.account()
        if self._apply_circuit(acct, now):
            return
        spec = self.broker.symbol(symbol)
        tick = self.broker.tick(symbol)
        positions = self.broker.positions(magic=self.cfg.risk.magic)

        for pos in positions:
            if pos.symbol != symbol:
                continue
            new_sl, new_tp = self.strategy.manage(pos, bars, spec)
            self._modify(pos, new_sl, new_tp)

        positions = self.broker.positions(magic=self.cfg.risk.magic)
        if any(p.symbol == symbol for p in positions):
            return

        sig = self.strategy.signal(symbol, bars, spec)
        if sig.kind is SignalKind.FLAT or sig.side is None:
            return
        entry = tick.ask if sig.kind is SignalKind.BUY else tick.bid
        sig = sig.reprice(entry, spec)
        decision = self.risk.evaluate(
            account=acct,
            signal=sig,
            spec=spec,
            tick=tick,
            positions=positions,
            now=now,
        )
        if not decision.allowed:
            self.journal.write(
                "reject",
                symbol=symbol,
                reason=decision.reason,
                kind=sig.kind.value,
                rr=sig.rr,
            )
            return
        self._open(sig, decision.volume)

    def market_signal(
        self,
        kind: SignalKind,
        symbol: str,
        sl: float | None = None,
        tp: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
    ) -> Signal:
        symbol = symbol.upper()
        spec = self.broker.symbol(symbol)
        self.broker.select_symbol(symbol)
        tick = self.broker.tick(symbol)
        if tick.bid <= 0 or tick.ask <= 0:
            raise RuntimeError(f"no tick for {symbol}")
        if limit is not None and stop is not None:
            raise RuntimeError("use limit= or stop=, not both")
        bars = self.broker.rates(
            symbol, self.cfg.strategy.timeframe_id, self.strategy.needed_bars()
        )
        a0 = 0.0
        if bars:
            vals = [v for v in atr_bars(bars, self.cfg.strategy.atr_period) if v == v]
            if vals:
                a0 = vals[-1]
        pending_kind = ""
        if limit is not None:
            pending_kind = "limit"
            entry = limit
            if kind is SignalKind.BUY and not (limit < tick.ask):
                raise RuntimeError("buy limit must be below ask")
            if kind is SignalKind.SELL and not (limit > tick.bid):
                raise RuntimeError("sell limit must be above bid")
        elif stop is not None:
            pending_kind = "stop"
            entry = stop
            if kind is SignalKind.BUY and not (stop > tick.ask):
                raise RuntimeError("buy stop must be above ask")
            if kind is SignalKind.SELL and not (stop < tick.bid):
                raise RuntimeError("sell stop must be below bid")
        else:
            entry = tick.ask if kind is SignalKind.BUY else tick.bid
        if sl is None:
            if a0 <= 0:
                raise RuntimeError("no ATR and no sl; pass sl=")
            dist = self.cfg.strategy.atr_stop_mult * a0
            sl = entry - dist if kind is SignalKind.BUY else entry + dist
        if tp is None:
            if a0 <= 0:
                raise RuntimeError("no ATR and no tp; pass tp=")
            dist = self.cfg.strategy.atr_tp_mult * a0
            tp = entry + dist if kind is SignalKind.BUY else entry - dist
        entry = spec.normalize_price(entry)
        sl = spec.normalize_price(sl)
        tp = spec.normalize_price(tp)
        if kind is SignalKind.BUY and not (sl < entry < tp):
            raise RuntimeError("buy needs sl < entry < tp")
        if kind is SignalKind.SELL and not (tp < entry < sl):
            raise RuntimeError("sell needs tp < entry < sl")
        return Signal(
            kind=kind,
            symbol=symbol,
            entry=entry,
            sl=sl,
            tp=tp,
            atr=a0,
            reason="manual",
            pending_kind=pending_kind,
        )

    def preview(self, signal: Signal, *, manual: bool = True) -> RiskDecision:
        return self.risk.evaluate(
            account=self.broker.account(),
            signal=signal,
            spec=self.broker.symbol(signal.symbol),
            tick=self.broker.tick(signal.symbol),
            positions=self.broker.positions(magic=self.cfg.risk.magic),
            now=self.now_fn(),
            manual=manual,
        )

    def submit(self, signal: Signal, volume: float) -> OrderResult:
        if signal.pending_kind:
            return self._place_pending(signal, volume)
        return self._open(signal, volume)

    def _place_pending(self, signal: Signal, volume: float) -> OrderResult:
        side = signal.side
        assert side is not None
        if signal.pending_kind == "limit":
            type_code = ORDER_TYPE_BUY_LIMIT if side.value == "buy" else ORDER_TYPE_SELL_LIMIT
        else:
            type_code = ORDER_TYPE_BUY_STOP if side.value == "buy" else ORDER_TYPE_SELL_STOP
        spec = self.broker.symbol(signal.symbol)
        request = {
            "action": TRADE_ACTION_PENDING,
            "symbol": signal.symbol,
            "volume": volume,
            "type": type_code,
            "price": signal.entry,
            "sl": signal.sl,
            "tp": signal.tp,
            "deviation": self.cfg.risk.deviation_points,
            "magic": self.cfg.risk.magic,
            "comment": self.cfg.comment[:31],
            "type_time": ORDER_TIME_GTC,
            "type_filling": choose_filling(spec.filling_mode),
        }
        check = self.broker.order_check(request)
        if check.retcode not in (0,) and not check.ok:
            self._emit(
                "order_check_fail",
                symbol=signal.symbol,
                retcode=check.retcode,
                comment=check.comment,
            )
            return check
        result = self.broker.order_send(request)
        self._emit(
            "pending",
            symbol=signal.symbol,
            side=side.value,
            kind=signal.pending_kind,
            volume=volume,
            price=signal.entry,
            sl=signal.sl,
            tp=signal.tp,
            ok=result.ok,
            retcode=result.retcode,
            order=result.order,
        )
        return result

    def orders_text(self) -> str:
        rows = self.broker.orders(magic=self.cfg.risk.magic)
        if not rows:
            return "no pending orders"
        return "\n".join(
            f"#{o.ticket} {o.symbol} {_ORDER_TYPE_NAME.get(o.type_code, o.side.value)} "
            f"{o.volume} @ {o.price} sl={o.sl} tp={o.tp}"
            for o in rows
        )

    def cancel_order(self, ticket: int) -> str:
        for order in self.broker.orders(magic=self.cfg.risk.magic):
            if order.ticket == ticket:
                result = self.broker.order_send(
                    {"action": TRADE_ACTION_REMOVE, "order": ticket}
                )
                if not result.ok:
                    return f"cancel failed retcode={result.retcode} {result.comment}".strip()
                return f"cancelled #{ticket}"
        return "not found"

    def _resolve_pending(self) -> None:
        fn = getattr(self.broker, "resolve_pending", None)
        if not callable(fn):
            fn = getattr(self.broker, "resolve_pending_tick", None)
        if not callable(fn):
            return
        fn()

    def _manage_open(self) -> None:
        for pos in list(self.broker.positions(magic=self.cfg.risk.magic)):
            bars = self.broker.rates(
                pos.symbol, self.cfg.strategy.timeframe_id, self.strategy.needed_bars()
            )
            spec = self.broker.symbol(pos.symbol)
            new_sl, new_tp = self.strategy.manage(pos, bars, spec)
            self._modify(pos, new_sl, new_tp)

    def _check_stops(self) -> None:
        for pos in list(self.broker.positions(magic=self.cfg.risk.magic)):
            tick = self.broker.tick(pos.symbol)
            sl_hit = False
            tp_hit = False
            if pos.side.value == "buy":
                sl_hit = pos.sl > 0 and tick.bid <= pos.sl
                tp_hit = pos.tp > 0 and tick.bid >= pos.tp
            else:
                sl_hit = pos.sl > 0 and tick.ask >= pos.sl
                tp_hit = pos.tp > 0 and tick.ask <= pos.tp
            if sl_hit:
                self._close(pos, "sl")
            elif tp_hit:
                self._close(pos, "tp")

    def _detect_fills(self) -> None:
        now = {p.ticket for p in self.broker.positions(magic=self.cfg.risk.magic)}
        if self._seen_pos is None:
            self._seen_pos = now
            return
        appeared = now - self._seen_pos
        vanished = self._seen_pos - now
        for ticket in sorted(appeared):
            if ticket in self._opened_this_step:
                continue
            pos = self._pos(ticket)
            self._emit(
                "open",
                fill=True,
                ticket=ticket,
                symbol=pos.symbol if pos else "",
                side=pos.side.value if pos else "",
                volume=pos.volume if pos else 0,
                price=pos.price_open if pos else 0,
                sl=pos.sl if pos else 0,
                tp=pos.tp if pos else 0,
                ok=True,
                reason="fill",
            )
        for ticket in sorted(vanished):
            if ticket in self._closed_this_step:
                continue
            self._emit("close", fill=True, ticket=ticket, reason="fill", ok=True)
        self._seen_pos = now

    def symbols_text(self) -> str:
        return " ".join(self.cfg.symbols) if self.cfg.symbols else "no symbols"

    def add_symbol(self, name: str) -> str:
        name = name.upper()
        if name in self.cfg.symbols:
            return f"already in book: {name}"
        if not self.broker.select_symbol(name):
            return f"broker rejected {name}"
        self.cfg.symbols.append(name)
        return f"added {name}\n{self.symbols_text()}"

    def remove_symbol(self, name: str) -> str:
        name = name.upper()
        if name not in self.cfg.symbols:
            return f"not in book: {name}"
        if len(self.cfg.symbols) <= 1:
            return "cannot remove the last symbol"
        magic = self.cfg.risk.magic
        if any(p.symbol == name for p in self.broker.positions(magic=magic)):
            return f"{name} has open positions; /close first"
        if any(o.symbol == name for o in self.broker.orders(magic=magic)):
            return f"{name} has working orders; /cancel first"
        self.cfg.symbols = [s for s in self.cfg.symbols if s != name]
        return f"removed {name}\n{self.symbols_text()}"

    def quote_text(self, symbol: str) -> str:
        symbol = symbol.upper()
        tick = self.broker.tick(symbol)
        parts = [f"{symbol} bid={tick.bid} ask={tick.ask} spread={tick.spread:.6f}"]
        bars = self.broker.rates(symbol, self.cfg.strategy.timeframe_id, self.strategy.needed_bars())
        if bars:
            closes = [b.close for b in bars]
            f0 = last_closed(ema(closes, self.cfg.strategy.fast_ema))
            s0 = last_closed(ema(closes, self.cfg.strategy.slow_ema))
            a0 = last_closed(atr_bars(bars, self.cfg.strategy.atr_period))
            x0 = last_closed(adx(bars, self.cfg.strategy.adx_period)[0])
            bits = []
            if a0 == a0:
                bits.append(f"atr={a0:.6f}")
            if x0 == x0:
                bits.append(f"adx={x0:.1f}")
            if f0 == f0:
                bits.append(f"fast={f0:.5f}")
            if s0 == s0:
                bits.append(f"slow={s0:.5f}")
            if bits:
                parts.append(" ".join(bits))
        return " ".join(parts)

    def close_ticket(self, ticket: int, reason: str, volume: float | None = None) -> str:
        pos = self._pos(ticket)
        if pos is None:
            return "no such ticket"
        if volume is not None and volume <= 0:
            raise ValueError("volume must be > 0")
        result = self._close(pos, reason, volume)
        if not result.ok:
            return f"close failed retcode={result.retcode} {result.comment}"
        return "closed"

    def close_symbol(self, symbol: str, reason: str) -> int:
        n = 0
        for pos in list(self.broker.positions(magic=self.cfg.risk.magic)):
            if pos.symbol == symbol:
                result = self._close(pos, reason)
                if result.ok:
                    n += 1
        return n

    def close_all(self, reason: str) -> int:
        n = 0
        for pos in list(self.broker.positions(magic=self.cfg.risk.magic)):
            result = self._close(pos, reason)
            if result.ok:
                n += 1
        return n

    def set_sl(self, ticket: int, price: float) -> str:
        pos = self._pos(ticket)
        if pos is not None:
            result = self._modify(pos, price, pos.tp)
            if not result.ok:
                return f"sl failed retcode={result.retcode} {result.comment}"
            return f"sl #{ticket} -> {price}"
        order = self._order(ticket)
        if order is None:
            return "no such ticket"
        result = self._modify_pending(order, sl=price, tp=order.tp)
        if not result.ok:
            return f"sl failed retcode={result.retcode} {result.comment}"
        return f"sl #{ticket} -> {price}"

    def set_tp(self, ticket: int, price: float) -> str:
        pos = self._pos(ticket)
        if pos is not None:
            result = self._modify(pos, pos.sl, price)
            if not result.ok:
                return f"tp failed retcode={result.retcode} {result.comment}"
            return f"tp #{ticket} -> {price}"
        order = self._order(ticket)
        if order is None:
            return "no such ticket"
        result = self._modify_pending(order, sl=order.sl, tp=price)
        if not result.ok:
            return f"tp failed retcode={result.retcode} {result.comment}"
        return f"tp #{ticket} -> {price}"

    def _order(self, ticket: int) -> PendingOrder | None:
        for order in self.broker.orders(magic=self.cfg.risk.magic):
            if order.ticket == ticket:
                return order
        return None

    def _modify_pending(self, order: PendingOrder, sl: float, tp: float) -> OrderResult:
        spec = self.broker.symbol(order.symbol)
        sl_n = spec.normalize_price(sl) if sl else 0.0
        tp_n = spec.normalize_price(tp) if tp else 0.0
        if sl_n <= 0:
            return OrderResult(retcode=TRADE_RETCODE_INVALID_STOPS, comment="sl required")
        entry = order.price
        if order.side.value == "buy" and not (sl_n < entry and (tp_n <= 0 or entry < tp_n)):
            return OrderResult(retcode=TRADE_RETCODE_INVALID_STOPS, comment="buy needs sl < entry < tp")
        if order.side.value == "sell" and not (sl_n > entry and (tp_n <= 0 or tp_n < entry)):
            return OrderResult(retcode=TRADE_RETCODE_INVALID_STOPS, comment="sell needs tp < entry < sl")
        request = {
            "action": TRADE_ACTION_MODIFY,
            "order": order.ticket,
            "symbol": order.symbol,
            "volume": order.volume,
            "type": order.type_code,
            "price": order.price,
            "sl": sl_n,
            "tp": tp_n,
            "type_time": ORDER_TIME_GTC,
            "magic": order.magic,
        }
        result = self.broker.order_send(request)
        self.journal.write(
            "modify",
            ticket=order.ticket,
            symbol=order.symbol,
            sl=sl_n,
            tp=tp_n,
            pending=True,
            ok=result.ok,
            retcode=result.retcode,
        )
        return result

    def breakeven(self, ticket: int) -> str:
        pos = self._pos(ticket)
        if pos is None:
            return "no such ticket"
        entry = pos.price_open
        tick = self.broker.tick(pos.symbol)
        if pos.side.value == "buy":
            if pos.sl > 0 and pos.sl >= entry - 1e-12:
                return "be would loosen sl"
            if tick.bid < entry:
                return "not in profit"
        else:
            if pos.sl > 0 and pos.sl <= entry + 1e-12:
                return "be would loosen sl"
            if tick.ask > entry:
                return "not in profit"
        result = self._modify(pos, entry, pos.tp)
        if not result.ok:
            return f"be failed retcode={result.retcode} {result.comment}"
        return f"be #{ticket} sl -> {entry}"

    def trail(self, ticket: int) -> str:
        pos = self._pos(ticket)
        if pos is None:
            return "no such ticket"
        bars = self.broker.rates(
            pos.symbol, self.cfg.strategy.timeframe_id, self.strategy.needed_bars()
        )
        spec = self.broker.symbol(pos.symbol)
        new_sl, new_tp = self.strategy.manage(pos, bars, spec)
        if abs(new_sl - pos.sl) < 1e-12 and abs((new_tp or 0) - (pos.tp or 0)) < 1e-12:
            return f"trail #{ticket} unchanged"
        result = self._modify(pos, new_sl, new_tp)
        if not result.ok:
            return f"trail failed retcode={result.retcode} {result.comment}"
        return f"trail #{ticket} sl -> {new_sl}"

    def risk_text(self) -> str:
        acct = self.broker.account()
        self.risk.observe(acct, self.now_fn())
        snap = self.risk.snapshot
        r = self.cfg.risk
        daily_loss = snap.day_start_equity - acct.equity
        daily_cap = snap.day_start_equity * r.daily_loss_pct
        dd = snap.peak_equity - acct.equity
        dd_cap = snap.peak_equity * r.max_drawdown_pct if snap.peak_equity else 0.0
        n = len(self.broker.positions(magic=r.magic))
        return (
            f"risk_pct={r.risk_pct:.2%}  positions={n}/{r.max_positions}\n"
            f"daily_loss={daily_loss:.2f}/{daily_cap:.2f}  "
            f"drawdown={dd:.2f}/{dd_cap:.2f}\n"
            f"equity={acct.equity:.2f} peak={snap.peak_equity:.2f} "
            f"day_start={snap.day_start_equity:.2f}"
        )

    def history_text(self, n: int = 15) -> str:
        rows = self.journal.tail(n)
        if not rows:
            return "no history"
        lines = []
        for rec in rows:
            ev = rec.get("event", "")
            ts = str(rec.get("ts", ""))[:19]
            extra = " ".join(
                f"{k}={v}"
                for k, v in rec.items()
                if k not in {"ts", "event"} and v not in (None, "")
            )
            lines.append(f"{ts} {ev} {extra}".strip())
        return "\n".join(lines)

    def _pos(self, ticket: int) -> Position | None:
        for pos in self.broker.positions(magic=self.cfg.risk.magic):
            if pos.ticket == ticket:
                return pos
        return None

    def advice_context(self) -> str:
        lines = [
            self.status_text(),
            self.risk_text(),
            self.positions_text(),
            self.orders_text(),
            f"symbols={','.join(self.cfg.symbols)} risk_pct={self.cfg.risk.risk_pct}",
            f"auto={self.cfg.strategy.auto} trail={self.cfg.strategy.trail} "
            f"provider={self.cfg.advice.provider}",
            "Advice may stage a trade. It never sends. /confirm is the only send.",
            "If daily_loss or drawdown room is gone, action must be hold or close.",
        ]
        for name in self.cfg.symbols:
            try:
                lines.append(self.quote_text(name))
            except RuntimeError:
                continue
        return "\n".join(lines)

    def status_text(self) -> str:
        acct = self.broker.account()
        reason = self.risk.halt_reason or ("halt_file" if self.risk.halt_path().exists() else "")
        halt = "HALTED " + reason if (self.halted or reason) else "running"
        return (
            f"mt5-risk-bot {halt}\n"
            f"mode={self.cfg.mode} server={acct.server}\n"
            f"equity={acct.equity:.2f} {acct.currency}  "
            f"balance={acct.balance:.2f}  peak={self.risk.snapshot.peak_equity:.2f}\n"
            f"positions={len(self.broker.positions(magic=self.cfg.risk.magic))}  "
            f"risk={self.cfg.risk.risk_pct:.2%}"
        )

    def positions_text(self) -> str:
        rows = self.broker.positions(magic=self.cfg.risk.magic)
        if not rows:
            return "no open positions"
        return "\n".join(
            f"#{p.ticket} {p.symbol} {p.side.value} {p.volume} "
            f"@ {p.price_open} sl={p.sl} tp={p.tp} pnl={p.profit:.2f}"
            for p in rows
        )

    def handle_command(self, cmd: TgCommand) -> str:
        return self.desk.handle(cmd)

    def poll_telegram(self) -> None:
        if self.telegram is None:
            return
        timeout = max(0, int(self.cfg.poll_seconds))
        try:
            cmds = self.telegram.poll_commands(timeout=timeout)
        except (ValueError, RuntimeError, OSError):
            return
        for cmd in cmds:
            try:
                self.telegram.send(self.desk.handle(cmd))
            except (ValueError, RuntimeError, OSError) as exc:
                try:
                    self.telegram.send(f"error: {exc}")
                except (ValueError, RuntimeError, OSError):
                    continue

    def step_all(self) -> None:
        self.poll_telegram()
        if self.halted:
            return
        if self._apply_circuit(self.broker.account(), self.now_fn()):
            return
        self._opened_this_step.clear()
        self._closed_this_step.clear()
        self._resolve_pending()
        self._check_stops()
        if self.cfg.strategy.trail:
            self._manage_open()
        if self.cfg.strategy.auto:
            for symbol in self.cfg.symbols:
                if self.halted:
                    break
                self.step_symbol(symbol)
        self._detect_fills()


def _format_event(event: str, fields: dict[str, Any]) -> str:
    if event == "start":
        return (
            f"start mode={fields.get('mode')} equity={fields.get('equity')} "
            f"symbols={fields.get('symbols')}"
        )
    if event == "stop":
        return "stop"
    if event == "open":
        tag = "FILL/OPEN" if fields.get("fill") or fields.get("reason") == "fill" else "OPEN"
        return (
            f"{tag} {fields.get('side')} {fields.get('symbol')} "
            f"vol={fields.get('volume')} @ {fields.get('price')} "
            f"sl={fields.get('sl')} tp={fields.get('tp')} ok={fields.get('ok')}"
        )
    if event == "close":
        tag = "FILL/CLOSE" if fields.get("fill") or fields.get("reason") == "fill" else "CLOSE"
        return (
            f"{tag} {fields.get('symbol')} #{fields.get('ticket')} "
            f"reason={fields.get('reason')} ok={fields.get('ok')}"
        )
    if event == "halt":
        return f"HALT {fields.get('reason')} equity={fields.get('equity')}"
    if event == "order_check_fail":
        return f"order_check_fail {fields.get('symbol')} retcode={fields.get('retcode')}"
    if event == "pending":
        return (
            f"PENDING {fields.get('kind')} {fields.get('side')} {fields.get('symbol')} "
            f"vol={fields.get('volume')} @ {fields.get('price')} "
            f"ok={fields.get('ok')} order={fields.get('order')}"
        )
    return ""


def run_backtest(cfg: BotConfig, series: dict[str, list[Bar]], *, journal_path: str) -> dict:
    from mt5_risk_bot.broker.paper import PaperBroker

    broker = PaperBroker(balance=cfg.initial_balance)
    for name, bars in series.items():
        if bars:
            broker.seed_bars(name, bars[:1])
    engine = Engine(cfg, broker, journal=Journal(journal_path))
    engine.start()
    maxlen = max((len(v) for v in series.values()), default=0)
    names = list(series.keys())
    for i in range(1, maxlen):
        if engine.halted:
            break
        for name in names:
            bars = series[name]
            if i >= len(bars):
                continue
            broker.on_bar(name, bars[i])
            engine.replay_symbol(name, bars[: i + 1])
    engine.stop()
    acct = broker.account()
    return {
        "balance": acct.balance,
        "equity": acct.equity,
        "peak": engine.risk.snapshot.peak_equity,
        "halted": engine.halted,
        "open_positions": len(broker.positions()),
    }
