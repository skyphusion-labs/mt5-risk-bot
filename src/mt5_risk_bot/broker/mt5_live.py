"""Adapter over the official MetaTrader5 Python package (Windows) or mt5-mac.

The official package is a Windows-only IPC client against a running terminal.
On macOS, mt5-mac speaks the same function names through Wine inside
MetaTrader 5.app. This adapter accepts either.
"""

from __future__ import annotations

from typing import Any

from mt5_risk_bot.constants import (
    FILLING_RETRY_ORDER,
    ORDER_TIME_GTC,
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_BUY_STOP_LIMIT,
    ORDER_TYPE_SELL_LIMIT,
    ORDER_TYPE_SELL_STOP,
    ORDER_TYPE_SELL_STOP_LIMIT,
    RETCODE_OK,
    TRADE_ACTION_CLOSE_BY,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
    TRADE_RETCODE_INVALID_FILL,
    choose_filling,
    timeframe_code,
)
from mt5_risk_bot.models import (
    Account,
    Bar,
    MarketOrder,
    OrderResult,
    PendingOrder,
    Position,
    Side,
    SymbolSpec,
    Tick,
    WorkingOrder,
)


def load_mt5_module() -> Any:
    try:
        import MetaTrader5 as mt5  # type: ignore

        return mt5
    except ImportError:
        pass
    try:
        import mt5_mac as mt5  # type: ignore

        return mt5
    except ImportError as exc:
        raise RuntimeError(
            "No MT5 Python binding. On Windows: pip install MetaTrader5. "
            "On macOS: brew-installed Python plus `pip install mt5-mac`, and "
            "MetaTrader 5.app from metatrader5.com (not Homebrew; MetaQuotes "
            "does not ship a cask). Paper mode needs neither."
        ) from exc


def _asdict(obj: Any) -> dict:
    if obj is None:
        return {}
    if hasattr(obj, "_asdict"):
        return obj._asdict()
    return dict(getattr(obj, "__dict__", {}) or {})


class Mt5Broker:
    def __init__(
        self,
        *,
        login: int = 0,
        password: str = "",
        server: str = "",
        path: str = "",
        timeout_ms: int = 60_000,
        mt5: Any | None = None,
    ) -> None:
        self._login = login
        self._password = password
        self._server = server
        self._path = path
        self._timeout = timeout_ms
        self._mt5 = mt5

    def connect(self) -> None:
        mt5 = self._mt5 or load_mt5_module()
        self._mt5 = mt5
        # initialize is not reentrant; drop the old IPC handle first
        if hasattr(mt5, "shutdown"):
            try:
                mt5.shutdown()
            except (RuntimeError, OSError, AttributeError, ValueError):
                pass
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._path:
            # initialize(path, login=..., ...) path is the unnamed first arg
            ok = mt5.initialize(
                self._path,
                login=self._login or None,
                password=self._password or None,
                server=self._server or None,
                timeout=self._timeout,
            )
        else:
            if self._login:
                kwargs["login"] = self._login
            if self._password:
                kwargs["password"] = self._password
            if self._server:
                kwargs["server"] = self._server
            ok = mt5.initialize(**{k: v for k, v in kwargs.items() if v is not None})
        if not ok:
            err = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
            raise RuntimeError(f"mt5.initialize failed: {err}")
        if self._login and hasattr(mt5, "login"):
            if not mt5.login(self._login, password=self._password, server=self._server):
                err = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
                raise RuntimeError(f"mt5.login failed: {err}")
        info = mt5.terminal_info()
        if info is not None:
            d = _asdict(info)
            if d.get("trade_allowed") is False:
                raise RuntimeError("terminal trade_allowed is False; enable AutoTrading")

    def disconnect(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()

    def ensure_connected(self) -> None:
        mt5 = self._mt5
        if mt5 is not None:
            try:
                info = mt5.account_info() if hasattr(mt5, "account_info") else None
            except (RuntimeError, OSError, AttributeError, ValueError):
                info = None
            if info is not None and not self._ipc_error():
                return
        self.connect()

    def _last_error(self) -> Any:
        mt5 = self._mt5
        if mt5 is None or not hasattr(mt5, "last_error"):
            return "unknown"
        try:
            return mt5.last_error()
        except (RuntimeError, OSError, AttributeError, ValueError):
            return "unknown"

    def _ipc_error(self) -> bool:
        err = self._last_error()
        if err is None or err == "unknown":
            return False
        code: Any = err
        desc = ""
        if isinstance(err, (tuple, list)):
            if not err:
                return False
            code = err[0]
            if len(err) > 1 and err[1] is not None:
                desc = str(err[1])
        elif isinstance(err, str):
            desc = err
            code = 0
        try:
            code_i = int(code)
        except (TypeError, ValueError):
            code_i = 0
        # MetaTrader5 RES_E_INTERNAL_FAIL* family (-10000..) is IPC death
        if code_i <= -10000:
            return True
        return "ipc" in desc.lower()

    def _with_reconnect(self, call: Any, what: str) -> Any:
        result = call()
        if result is not None and not self._ipc_error():
            return result
        self.connect()
        result = call()
        if result is None or self._ipc_error():
            raise RuntimeError(f"{what} failed: {self._last_error()}")
        return result

    def select_symbol(self, name: str) -> bool:
        return bool(self._mt5.symbol_select(name, True))

    def account(self) -> Account:
        info = self._with_reconnect(lambda: self._mt5.account_info(), "account_info")
        d = _asdict(info)
        return Account(
            login=int(d.get("login", 0)),
            balance=float(d.get("balance", 0)),
            equity=float(d.get("equity", 0)),
            margin=float(d.get("margin", 0)),
            margin_free=float(d.get("margin_free", 0)),
            profit=float(d.get("profit", 0)),
            leverage=int(d.get("leverage", 0)),
            currency=str(d.get("currency", "")),
            trade_allowed=bool(d.get("trade_allowed", False)),
            trade_expert=bool(d.get("trade_expert", False)),
            server=str(d.get("server", "")),
            name=str(d.get("name", "")),
            fifo_close=bool(d.get("fifo_close", False)),
            credit=float(d.get("credit", 0)),
            margin_level=float(d.get("margin_level", 0)),
            trade_mode=int(d.get("trade_mode", 0)),
        )

    def symbol(self, name: str) -> SymbolSpec:
        info = self._mt5.symbol_info(name)
        if info is None:
            raise RuntimeError(f"symbol_info({name}) failed: {self._mt5.last_error()}")
        d = _asdict(info)
        if not d.get("visible", True):
            self.select_symbol(name)
            info = self._mt5.symbol_info(name)
            d = _asdict(info)
        return SymbolSpec(
            name=str(d.get("name", name)),
            digits=int(d.get("digits", 5)),
            point=float(d.get("point", 0.00001)),
            trade_tick_size=float(d.get("trade_tick_size") or d.get("point") or 0.00001),
            trade_tick_value=float(d.get("trade_tick_value") or 1.0),
            trade_contract_size=float(d.get("trade_contract_size") or 100_000),
            volume_min=float(d.get("volume_min") or 0.01),
            volume_max=float(d.get("volume_max") or 100),
            volume_step=float(d.get("volume_step") or 0.01),
            trade_stops_level=int(d.get("trade_stops_level") or 0),
            trade_freeze_level=int(d.get("trade_freeze_level") or 0),
            filling_mode=int(d.get("filling_mode") or 0),
            currency_base=str(d.get("currency_base", "")),
            currency_profit=str(d.get("currency_profit", "")),
            currency_margin=str(d.get("currency_margin", "")),
            trade_mode=int(d.get("trade_mode", 4)),
            visible=bool(d.get("visible", True)),
            spread=int(d.get("spread") or 0),
        )

    def tick(self, name: str) -> Tick:
        t = self._with_reconnect(
            lambda: self._mt5.symbol_info_tick(name),
            f"symbol_info_tick({name})",
        )
        d = _asdict(t)
        return Tick(
            time=int(d.get("time", 0)),
            bid=float(d.get("bid", 0)),
            ask=float(d.get("ask", 0)),
            last=float(d.get("last", 0)),
            volume=int(d.get("volume", 0) or 0),
        )

    def rates(self, name: str, timeframe: str | int, count: int) -> list[Bar]:
        raw = self._mt5.copy_rates_from_pos(name, timeframe_code(timeframe), 0, count)
        if raw is None:
            return []
        out: list[Bar] = []
        for row in raw:
            d = _asdict(row) if not isinstance(row, dict) else row
            # numpy void / named tuple: also support index access
            if not d and hasattr(row, "dtype"):
                d = {name: row[name] for name in row.dtype.names}
            out.append(
                Bar(
                    time=int(d.get("time", 0)),
                    open=float(d.get("open", 0)),
                    high=float(d.get("high", 0)),
                    low=float(d.get("low", 0)),
                    close=float(d.get("close", 0)),
                    tick_volume=int(d.get("tick_volume", 0) or 0),
                    spread=int(d.get("spread", 0) or 0),
                    real_volume=int(d.get("real_volume", 0) or 0),
                )
            )
        return out

    def positions(self, magic: int | None = None) -> list[Position]:
        raw = self._with_reconnect(lambda: self._mt5.positions_get(), "positions_get")
        if not raw:
            return []
        out: list[Position] = []
        for row in raw:
            d = _asdict(row)
            mag = int(d.get("magic", 0) or 0)
            if magic is not None and mag != magic:
                continue
            ptype = int(d.get("type", 0))
            out.append(
                Position(
                    ticket=int(d.get("ticket", 0)),
                    symbol=str(d.get("symbol", "")),
                    side=Side.BUY if ptype == 0 else Side.SELL,
                    volume=float(d.get("volume", 0)),
                    price_open=float(d.get("price_open", 0)),
                    sl=float(d.get("sl", 0) or 0),
                    tp=float(d.get("tp", 0) or 0),
                    price_current=float(d.get("price_current", 0)),
                    profit=float(d.get("profit", 0)),
                    swap=float(d.get("swap", 0) or 0),
                    magic=mag,
                    comment=str(d.get("comment", "") or ""),
                    time=int(d.get("time", 0) or 0),
                    identifier=int(d.get("identifier", 0) or d.get("ticket", 0)),
                )
            )
        return out

    def orders(self, magic: int | None = None) -> list[PendingOrder]:
        mt5 = self._mt5
        if mt5 is None or not hasattr(mt5, "orders_get"):
            return []
        raw = self._with_reconnect(lambda: self._mt5.orders_get(), "orders_get")
        if not raw:
            return []
        buy_types = {
            ORDER_TYPE_BUY,
            ORDER_TYPE_BUY_LIMIT,
            ORDER_TYPE_BUY_STOP,
            ORDER_TYPE_BUY_STOP_LIMIT,
        }
        stop_types = {
            ORDER_TYPE_BUY_STOP,
            ORDER_TYPE_SELL_STOP,
            ORDER_TYPE_BUY_STOP_LIMIT,
            ORDER_TYPE_SELL_STOP_LIMIT,
        }
        out: list[PendingOrder] = []
        for row in raw:
            d = _asdict(row)
            mag = int(d.get("magic", 0) or 0)
            if magic is not None and mag != magic:
                continue
            ptype = int(d.get("type", 0))
            volume = float(d.get("volume_current", d.get("volume_initial", d.get("volume", 0))) or 0)
            price = float(d.get("price_open", d.get("price_current", d.get("price", 0))) or 0)
            out.append(
                PendingOrder(
                    ticket=int(d.get("ticket", 0)),
                    symbol=str(d.get("symbol", "")),
                    side=Side.BUY if ptype in buy_types else Side.SELL,
                    volume=volume,
                    price=price,
                    sl=float(d.get("sl", 0) or 0),
                    tp=float(d.get("tp", 0) or 0),
                    magic=mag,
                    comment=str(d.get("comment", "") or ""),
                    kind="stop" if ptype in stop_types else "limit",
                    time=int(d.get("time_setup") or d.get("time") or 0),
                )
            )
        return out

    def _result(self, raw: Any, request: dict) -> OrderResult:
        if raw is None:
            err = self._mt5.last_error() if self._mt5 else "none"
            return OrderResult(retcode=0, comment=f"no result: {err}", request=request)
        d = _asdict(raw)
        return OrderResult(
            retcode=int(d.get("retcode", 0)),
            comment=str(d.get("comment", "") or ""),
            deal=int(d.get("deal", 0) or 0),
            order=int(d.get("order", 0) or 0),
            volume=float(d.get("volume", 0) or 0),
            price=float(d.get("price", 0) or 0),
            bid=float(d.get("bid", 0) or 0),
            ask=float(d.get("ask", 0) or 0),
            request=request,
        )

    def order_check(self, request: dict) -> OrderResult:
        return self._result(self._mt5.order_check(request), request)

    def order_send(self, request: dict) -> OrderResult:
        raw = self._with_reconnect(lambda: self._mt5.order_send(request), "order_send")
        result = self._result(raw, request)
        if result.retcode != TRADE_RETCODE_INVALID_FILL:
            return result
        tried = {request.get("type_filling")}
        for filling in FILLING_RETRY_ORDER:
            if filling in tried:
                continue
            retry = dict(request)
            retry["type_filling"] = filling
            result = self._result(self._mt5.order_send(retry), retry)
            if result.retcode in RETCODE_OK or result.retcode != TRADE_RETCODE_INVALID_FILL:
                return result
            tried.add(filling)
        return result

    def check_market(self, order: MarketOrder) -> OrderResult:
        return self.order_check(self._market_req(order))

    def market(self, order: MarketOrder) -> OrderResult:
        return self.order_send(self._market_req(order))

    def check_working(self, order: WorkingOrder) -> OrderResult:
        return self.order_check(self._working_req(order))

    def working(self, order: WorkingOrder) -> OrderResult:
        return self.order_send(self._working_req(order))

    def modify_position(self, ticket: int, sl: float, tp: float, symbol: str = "") -> OrderResult:
        req = {"action": TRADE_ACTION_SLTP, "position": ticket, "sl": sl, "tp": tp}
        if symbol:
            req["symbol"] = symbol
        return self.order_send(req)

    def modify_working(
        self,
        ticket: int,
        *,
        price: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
        symbol: str = "",
        volume: float = 0.0,
        side: str = "",
        kind: str = "",
    ) -> OrderResult:
        req: dict = {"action": TRADE_ACTION_MODIFY, "order": ticket, "type_time": ORDER_TIME_GTC}
        if price is not None:
            req["price"] = price
        if sl is not None:
            req["sl"] = sl
        if tp is not None:
            req["tp"] = tp
        if symbol:
            req["symbol"] = symbol
        if volume:
            req["volume"] = volume
        if kind == "limit":
            req["type"] = ORDER_TYPE_BUY_LIMIT if side == "buy" else ORDER_TYPE_SELL_LIMIT
        elif kind == "stop":
            req["type"] = ORDER_TYPE_BUY_STOP if side == "buy" else ORDER_TYPE_SELL_STOP
        return self.order_send(req)

    def cancel(self, ticket: int) -> OrderResult:
        return self.order_send({"action": TRADE_ACTION_REMOVE, "order": ticket})

    def close_position(
        self,
        ticket: int,
        *,
        symbol: str,
        side: str,
        volume: float,
        price: float,
        comment: str = "",
        magic: int = 0,
        deviation: int = 20,
    ) -> OrderResult:
        spec = self.symbol(symbol)
        close_side = Side.SELL if side == "buy" else Side.BUY
        return self.order_send(
            {
                "action": TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": close_side.order_type,
                "position": ticket,
                "price": price,
                "deviation": deviation,
                "magic": magic,
                "comment": comment[:31],
                "type_time": ORDER_TIME_GTC,
                "type_filling": choose_filling(spec.filling_mode),
            }
        )

    def close_by(self, ticket: int, other: int, symbol: str = "") -> OrderResult:
        req = {"action": TRADE_ACTION_CLOSE_BY, "position": ticket, "position_by": other}
        if symbol:
            req["symbol"] = symbol
        return self.order_send(req)

    def _market_req(self, order: MarketOrder) -> dict:
        spec = self.symbol(order.symbol)
        if order.ticket is not None:
            close_side = Side.SELL if order.side is Side.BUY else Side.BUY
            return {
                "action": TRADE_ACTION_DEAL,
                "symbol": order.symbol,
                "volume": order.volume,
                "type": close_side.order_type,
                "position": order.ticket,
                "price": self.tick(order.symbol).bid if order.side is Side.BUY else self.tick(order.symbol).ask,
                "sl": order.sl,
                "tp": order.tp,
                "deviation": order.deviation,
                "magic": order.magic,
                "comment": order.comment[:31],
                "type_time": ORDER_TIME_GTC,
                "type_filling": choose_filling(spec.filling_mode),
            }
        tick = self.tick(order.symbol)
        price = tick.ask if order.side is Side.BUY else tick.bid
        return {
            "action": TRADE_ACTION_DEAL,
            "symbol": order.symbol,
            "volume": order.volume,
            "type": order.side.order_type,
            "price": price,
            "sl": order.sl,
            "tp": order.tp,
            "deviation": order.deviation,
            "magic": order.magic,
            "comment": order.comment[:31],
            "type_time": ORDER_TIME_GTC,
            "type_filling": choose_filling(spec.filling_mode),
        }

    def _working_req(self, order: WorkingOrder) -> dict:
        spec = self.symbol(order.symbol)
        if order.kind == "limit":
            typ = ORDER_TYPE_BUY_LIMIT if order.side is Side.BUY else ORDER_TYPE_SELL_LIMIT
        else:
            typ = ORDER_TYPE_BUY_STOP if order.side is Side.BUY else ORDER_TYPE_SELL_STOP
        return {
            "action": TRADE_ACTION_PENDING,
            "symbol": order.symbol,
            "volume": order.volume,
            "type": typ,
            "price": order.price,
            "sl": order.sl,
            "tp": order.tp,
            "magic": order.magic,
            "comment": order.comment[:31],
            "type_time": ORDER_TIME_GTC,
            "type_filling": choose_filling(spec.filling_mode),
        }
