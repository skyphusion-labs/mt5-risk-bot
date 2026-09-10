"""MT4 venue adapter.

MetaTrader 4 has no official Python package. This adapter speaks a
key=value line protocol to an Expert Advisor over a file mailbox in
Terminal Common Files. Engine still sees MarketOrder / WorkingOrder only.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from mt5_risk_bot.constants import (
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_INVALID_PRICE,
    TRADE_RETCODE_INVALID_STOPS,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_LOCKED,
    TRADE_RETCODE_NO_MONEY,
    TRADE_RETCODE_PLACED,
    TRADE_RETCODE_POSITION_CLOSED,
    TRADE_RETCODE_REJECT,
    TRADE_RETCODE_TRADE_DISABLED,
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

Call = Callable[[str, dict[str, Any]], dict[str, Any]]

REQ_NAME = "mt4_risk_bot.req"
RES_NAME = "mt4_risk_bot.res"

BAR_FIELDS = ("time", "open", "high", "low", "close", "volume")
POS_FIELDS = (
    "ticket",
    "symbol",
    "side",
    "volume",
    "price_open",
    "sl",
    "tp",
    "price_current",
    "profit",
    "magic",
    "comment",
    "swap",
    "time",
)
ORD_FIELDS = (
    "ticket",
    "symbol",
    "side",
    "kind",
    "volume",
    "price",
    "sl",
    "tp",
    "magic",
    "comment",
    "time",
)

_MT4_RET = {
    129: TRADE_RETCODE_INVALID_PRICE,
    130: TRADE_RETCODE_INVALID_STOPS,
    131: TRADE_RETCODE_INVALID_VOLUME,
    132: TRADE_RETCODE_TRADE_DISABLED,
    133: TRADE_RETCODE_TRADE_DISABLED,
    134: TRADE_RETCODE_NO_MONEY,
    136: TRADE_RETCODE_TRADE_DISABLED,
    138: TRADE_RETCODE_INVALID_PRICE,
    146: TRADE_RETCODE_LOCKED,
    4108: TRADE_RETCODE_POSITION_CLOSED,
    4109: TRADE_RETCODE_TRADE_DISABLED,
}

_TEXT_FIELDS = {"symbol", "side", "kind", "comment", "name", "server", "currency", "error", "op"}


def _wire(v: Any) -> str:
    s = str(v).replace("\r", " ").replace("\n", " ").replace("|", "/")
    return s.encode("ascii", "replace").decode("ascii")


def encode(op: str, payload: dict[str, Any], req_id: int) -> str:
    lines = [f"id={req_id}", f"op={op}"]
    for k, v in payload.items():
        if v is None:
            continue
        lines.append(f"{k}={_wire(v)}")
    return "\n".join(lines) + "\n"


def decode(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k in _TEXT_FIELDS or k.startswith("row"):
            out[k] = v
        else:
            out[k] = _coerce(v)
    return out


def _coerce(v: str) -> Any:
    if v in {"true", "True"}:
        return True
    if v in {"false", "False"}:
        return False
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return v


def _truthy(v: Any) -> bool:
    return v in {True, 1, "1", "true", "True", "yes"}


def parse_rows(data: dict[str, Any], fields: tuple[str, ...], *, nested: str = "") -> list[dict[str, Any]]:
    raw = data.get(nested) if nested else None
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    n = int(data.get("n", 0) or 0)
    out: list[dict[str, Any]] = []
    for i in range(n):
        item = data.get(f"row{i}")
        if isinstance(item, str):
            out.append(_split_row(item, fields))
        elif isinstance(item, dict):
            out.append(item)
    return out


def _split_row(text: str, fields: tuple[str, ...]) -> dict[str, Any]:
    parts = text.split("|")
    out: dict[str, Any] = {}
    for i, name in enumerate(fields):
        v = parts[i] if i < len(parts) else ""
        if name in _TEXT_FIELDS:
            out[name] = v
        else:
            out[name] = _coerce(v) if v != "" else 0
    return out


def _mailbox_encoding() -> str:
    # FILE_ANSI on Windows is the ANSI code page. latin-1 is 8-bit clean.
    if sys.platform == "win32":
        return "mbcs"
    return "utf-8"


def _retry_unlink(path: Path, deadline: float) -> None:
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def _atomic_write(path: Path, text: str, deadline: float) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=_mailbox_encoding(), errors="replace", newline="\n")
    while True:
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


class FileBridge:
    """Mailbox in MT4 FILE_COMMON. EA reads .req and writes .res.

    Windows NTFS will refuse unlink/replace while the terminal holds the
    handle. Retry until timeout. Protocol lines are LF even on Windows.
    """

    def __init__(self, directory: str | Path, timeout_sec: float = 5.0) -> None:
        self.dir = Path(directory)
        self.timeout = timeout_sec
        self._n = 0
        self._lock = threading.Lock()

    def call(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._call(op, payload)

    def _call(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._n += 1
        req_id = self._n
        req = self.dir / REQ_NAME
        res = self.dir / RES_NAME
        deadline = time.monotonic() + self.timeout
        _retry_unlink(res, deadline)
        _retry_unlink(req, deadline)
        _atomic_write(req, encode(op, payload, req_id), deadline)
        enc = _mailbox_encoding()
        while time.monotonic() < deadline:
            if res.exists():
                try:
                    text = res.read_text(encoding=enc, errors="replace")
                except OSError:
                    time.sleep(0.02)
                    continue
                data = decode(text)
                if int(data.get("id", 0) or 0) == req_id:
                    _retry_unlink(res, deadline)
                    _retry_unlink(req, deadline)
                    return data
            time.sleep(0.02)
        raise RuntimeError("mt4 bridge timeout")


class Mt4Broker:
    def __init__(self, call: Call, *, magic: int = 0) -> None:
        self._call = call
        self._magic = magic

    def connect(self) -> None:
        got = self._call("ping", {})
        if not _truthy(got.get("ok")):
            raise RuntimeError(str(got.get("error") or "mt4 ping failed"))

    def ensure_connected(self) -> None:
        self.connect()

    def disconnect(self) -> None:
        try:
            self._call("ping", {})
        except (RuntimeError, OSError):
            pass

    def account(self) -> Account:
        d = self._require("account", {})
        return Account(
            login=int(d.get("login", 0) or 0),
            balance=float(d.get("balance", 0) or 0),
            equity=float(d.get("equity", 0) or 0),
            margin=float(d.get("margin", 0) or 0),
            margin_free=float(d.get("margin_free", d.get("free_margin", 0)) or 0),
            profit=float(d.get("profit", 0) or 0),
            leverage=int(d.get("leverage", 0) or 0),
            currency=str(d.get("currency", "USD") or "USD"),
            trade_allowed=_truthy(d.get("trade_allowed", True)),
            trade_expert=_truthy(d.get("trade_expert", True)),
            server=str(d.get("server", "") or ""),
            name=str(d.get("name", "mt4") or "mt4"),
            trade_mode=int(d.get("trade_mode", 0) or 0),
        )

    def symbol(self, name: str) -> SymbolSpec:
        d = self._require("symbol", {"symbol": name.upper()})
        n = name.upper()
        point = float(d.get("point", 0.00001) or 0.00001)
        return SymbolSpec(
            name=n,
            digits=int(d.get("digits", 5) or 5),
            point=point,
            trade_tick_size=float(d.get("tick_size", point) or point),
            trade_tick_value=float(d.get("tick_value", 1.0) or 1.0),
            trade_contract_size=float(d.get("contract_size", 100_000) or 100_000),
            volume_min=float(d.get("volume_min", 0.01) or 0.01),
            volume_max=float(d.get("volume_max", 100.0) or 100.0),
            volume_step=float(d.get("volume_step", 0.01) or 0.01),
            trade_stops_level=int(d.get("stops_level", 0) or 0),
            trade_freeze_level=int(d.get("freeze_level", 0) or 0),
            filling_mode=1,
            currency_base=str(d.get("currency_base", n[:3] if len(n) >= 6 else "") or ""),
            currency_profit=str(d.get("currency_profit", n[3:6] if len(n) >= 6 else "") or ""),
            currency_margin=str(d.get("currency_margin", "") or ""),
            trade_mode=int(d.get("trade_mode", 4) or 4),
            spread=int(d.get("spread", 0) or 0),
        )

    def tick(self, name: str) -> Tick:
        d = self._require("tick", {"symbol": name.upper()})
        bid = float(d.get("bid", 0) or 0)
        ask = float(d.get("ask", 0) or 0)
        return Tick(
            time=int(d.get("time", 0) or 0),
            bid=bid,
            ask=ask,
            last=float(d.get("last", bid) or bid),
            volume=int(d.get("volume", 0) or 0),
        )

    def rates(self, name: str, timeframe: str | int, count: int) -> list[Bar]:
        tf = str(timeframe).upper() if not isinstance(timeframe, str) else timeframe
        d = self._require("rates", {"symbol": name.upper(), "timeframe": tf, "count": int(count)})
        out: list[Bar] = []
        for row in parse_rows(d, BAR_FIELDS, nested="bars"):
            out.append(
                Bar(
                    time=int(row.get("time", 0) or 0),
                    open=float(row.get("open", 0) or 0),
                    high=float(row.get("high", 0) or 0),
                    low=float(row.get("low", 0) or 0),
                    close=float(row.get("close", 0) or 0),
                    tick_volume=int(row.get("volume", row.get("tick_volume", 0)) or 0),
                )
            )
        return out

    def positions(self, magic: int | None = None) -> list[Position]:
        want = magic if magic is not None else 0
        d = self._require("positions", {"magic": want})
        return [_pos(x) for x in parse_rows(d, POS_FIELDS, nested="rows")]

    def orders(self, magic: int | None = None) -> list[PendingOrder]:
        want = magic if magic is not None else 0
        d = self._require("orders", {"magic": want})
        return [_ord(x) for x in parse_rows(d, ORD_FIELDS, nested="rows")]

    def select_symbol(self, name: str) -> bool:
        d = self._call("select", {"symbol": name.upper()})
        return _truthy(d.get("ok", True))

    def check_market(self, order: MarketOrder) -> OrderResult:
        return self._result(self._call("check_market", self._market_payload(order)))

    def market(self, order: MarketOrder) -> OrderResult:
        return self._result(self._call("market", self._market_payload(order)))

    def check_working(self, order: WorkingOrder) -> OrderResult:
        return self._result(self._call("check_working", self._working_payload(order)), placed=True)

    def working(self, order: WorkingOrder) -> OrderResult:
        return self._result(self._call("working", self._working_payload(order)), placed=True)

    def modify_position(self, ticket: int, sl: float, tp: float, symbol: str = "") -> OrderResult:
        return self._result(
            self._call("modify_position", {"ticket": ticket, "sl": sl, "tp": tp, "symbol": symbol})
        )

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
        payload: dict[str, Any] = {
            "ticket": ticket,
            "symbol": symbol,
            "volume": volume,
            "side": side,
            "kind": kind,
        }
        if price is not None:
            payload["price"] = price
        if sl is not None:
            payload["sl"] = sl
        if tp is not None:
            payload["tp"] = tp
        return self._result(self._call("modify_working", payload))

    def cancel(self, ticket: int) -> OrderResult:
        return self._result(self._call("cancel", {"ticket": ticket}))

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
        return self._result(
            self._call(
                "close",
                {
                    "ticket": ticket,
                    "symbol": symbol,
                    "side": side,
                    "volume": volume,
                    "price": price,
                    "comment": comment,
                    "magic": magic or self._magic,
                    "deviation": deviation,
                },
            )
        )

    def close_by(self, ticket: int, other: int, symbol: str = "") -> OrderResult:
        return self._result(self._call("close_by", {"ticket": ticket, "other": other, "symbol": symbol}))

    def _market_payload(self, order: MarketOrder) -> dict[str, Any]:
        return {
            "symbol": order.symbol,
            "side": order.side.value,
            "volume": order.volume,
            "sl": order.sl,
            "tp": order.tp,
            "comment": (order.comment or "")[:31],
            "magic": order.magic or self._magic,
            "deviation": order.deviation,
            "ticket": order.ticket or 0,
        }

    def _working_payload(self, order: WorkingOrder) -> dict[str, Any]:
        return {
            "symbol": order.symbol,
            "side": order.side.value,
            "kind": order.kind,
            "volume": order.volume,
            "price": order.price,
            "sl": order.sl,
            "tp": order.tp,
            "comment": (order.comment or "")[:31],
            "magic": order.magic or self._magic,
        }

    def _require(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        d = self._call(op, payload)
        if not _truthy(d.get("ok", True)) and d.get("error"):
            raise RuntimeError(str(d.get("error")))
        return d

    def _result(self, d: dict[str, Any], *, placed: bool = False) -> OrderResult:
        ok = _truthy(d.get("ok"))
        raw = int(d.get("retcode", 0) or 0)
        if ok:
            if raw in {TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED}:
                code = raw
            else:
                code = TRADE_RETCODE_PLACED if placed else TRADE_RETCODE_DONE
        else:
            code = _MT4_RET.get(raw, raw if raw >= 10004 else TRADE_RETCODE_REJECT)
            if code == 0:
                code = TRADE_RETCODE_REJECT
        return OrderResult(
            retcode=code,
            comment=str(d.get("error") or d.get("comment") or ""),
            order=int(d.get("ticket", 0) or 0),
            deal=int(d.get("ticket", 0) or 0),
            volume=float(d.get("volume", 0) or 0),
            price=float(d.get("price", 0) or 0),
        )


def _pos(d: dict[str, Any]) -> Position:
    side = Side.BUY if str(d.get("side", "buy")).lower() == "buy" else Side.SELL
    return Position(
        ticket=int(d.get("ticket", 0) or 0),
        symbol=str(d.get("symbol", "")),
        side=side,
        volume=float(d.get("volume", 0) or 0),
        price_open=float(d.get("price_open", 0) or 0),
        sl=float(d.get("sl", 0) or 0),
        tp=float(d.get("tp", 0) or 0),
        price_current=float(d.get("price_current", 0) or 0),
        profit=float(d.get("profit", 0) or 0),
        swap=float(d.get("swap", 0) or 0),
        magic=int(d.get("magic", 0) or 0),
        comment=str(d.get("comment", "") or ""),
        time=int(d.get("time", 0) or 0),
    )


def _ord(d: dict[str, Any]) -> PendingOrder:
    side = Side.BUY if str(d.get("side", "buy")).lower() == "buy" else Side.SELL
    kind = str(d.get("kind", "") or "")
    return PendingOrder(
        ticket=int(d.get("ticket", 0) or 0),
        symbol=str(d.get("symbol", "")),
        side=side,
        volume=float(d.get("volume", 0) or 0),
        price=float(d.get("price", 0) or 0),
        sl=float(d.get("sl", 0) or 0),
        tp=float(d.get("tp", 0) or 0),
        magic=int(d.get("magic", 0) or 0),
        comment=str(d.get("comment", "") or ""),
        kind=kind,
        time=int(d.get("time", 0) or 0),
    )
