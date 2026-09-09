"""Adapter over the official MetaTrader5 Python package (Windows) or mt5-mac.

The official package is a Windows-only IPC client against a running terminal.
On macOS, mt5-mac speaks the same function names through Wine inside
MetaTrader 5.app. This adapter accepts either.
"""

from __future__ import annotations

from typing import Any

from mt5_risk_bot.constants import (
    FILLING_RETRY_ORDER,
    RETCODE_OK,
    TRADE_RETCODE_INVALID_FILL,
)
from mt5_risk_bot.models import Account, Bar, OrderResult, Position, Side, SymbolSpec, Tick


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

    def select_symbol(self, name: str) -> bool:
        return bool(self._mt5.symbol_select(name, True))

    def account(self) -> Account:
        info = self._mt5.account_info()
        if info is None:
            raise RuntimeError(f"account_info failed: {self._mt5.last_error()}")
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
        t = self._mt5.symbol_info_tick(name)
        if t is None:
            raise RuntimeError(f"symbol_info_tick({name}) failed: {self._mt5.last_error()}")
        d = _asdict(t)
        return Tick(
            time=int(d.get("time", 0)),
            bid=float(d.get("bid", 0)),
            ask=float(d.get("ask", 0)),
            last=float(d.get("last", 0)),
            volume=int(d.get("volume", 0) or 0),
        )

    def rates(self, name: str, timeframe: int, count: int) -> list[Bar]:
        raw = self._mt5.copy_rates_from_pos(name, timeframe, 0, count)
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
        raw = self._mt5.positions_get()
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
        result = self._result(self._mt5.order_send(request), request)
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
