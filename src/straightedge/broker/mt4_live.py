"""MT4 venue adapter.

MetaTrader 4 has no official Python package. This adapter speaks a
key=value line protocol to an Expert Advisor over a file mailbox in
Terminal Common Files. Engine still sees MarketOrder / WorkingOrder only.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from straightedge.constants import (
    RETCODE_UNKNOWN,
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
from straightedge.history import HistoryProbe
from straightedge.models import (
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

#: How long `Mt4Broker.startup_connect()` waits for the Expert to start
#: answering. This is a COLD BOOT budget, not a latency budget.
#:
#: A Windows host that starts the desk from a boot-triggered scheduled task
#: races MetaTrader 4's own launch: the terminal has to start, load, log in to
#: the broker and reach the Expert's first timer tick. That is tens of seconds
#: on a quiet box and longer while every other service on the machine is
#: competing for the same disk. Before this budget existed, `connect()` sent
#: exactly one ping on the 5 second steady-state timeout, so after every
#: reboot MT4 came up healthy and the desk was already dead, with nothing
#: retrying and nothing saying why.
#:
#: 180 seconds is about twice the slowest cold start observed by hand. The two
#: failure directions do not cost the same, which is why the number is biased
#: long: too short kills the desk on every reboot, and too long only delays
#: the report of a genuine misconfiguration (wrong `files_dir`, Expert not
#: attached, MT4 not installed) by up to a couple of minutes. That report is
#: never silent -- every attempt is logged with its elapsed time -- and the
#: wait is always bounded, because an unbounded wait would turn the same
#: misconfiguration into a process that hangs forever looking busy.
DEFAULT_STARTUP_WAIT_SEC = 180.0

#: Gap between startup pings: `_STARTUP_GAP_MIN`, then doubling to
#: `_STARTUP_GAP_MAX`. Deliberately small next to `FileBridge.timeout`, because
#: a ping that times out has already spent one full bridge timeout and the
#: timeout IS most of the backoff. The gap exists for the failures that return
#: IMMEDIATELY -- an unwritable mailbox directory raises `OSError` with no
#: delay at all -- which would otherwise spin the budget away in a tight loop.
_STARTUP_GAP_MIN = 1.0
_STARTUP_GAP_MAX = 5.0

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


def _survivor_ticket(d: dict[str, Any], ok: bool) -> int | None:
    """What the Expert said about exposure left behind by a failed send.

    The Expert states this on every reply from a trade handler, including
    the ordinary rejections where the answer is zero. Absence therefore
    means one thing only: the Expert predates this contract and cannot
    answer. That is COULD NOT MEASURE, and it is reported as None.
    Returning 0 there would turn an unanswered question into a clean bill
    of health, which is the defect this field exists to close.
    """
    if "survivor_ticket" in d:
        return int(d.get("survivor_ticket") or 0)
    return 0 if ok else None


def _reported_int(d: dict[str, Any], key: str) -> int | None:
    """An int the Expert may or may not have sent. Absence reads as None.

    Same partition as `_survivor_ticket` above and for the same reason: a
    `history_error` of 0 is the Expert saying it looked and found no error,
    while a missing `history_error` is an Expert that predates the field and
    cannot answer. Collapsing the second into 0 would turn "nobody asked" into
    "no problem", which is what made a cold symbol read as a healthy one.
    """
    if key not in d:
        return None
    try:
        return int(d[key])
    except (TypeError, ValueError):
        return None


def _reported_bool(d: dict[str, Any], key: str) -> bool | None:
    if key not in d:
        return None
    return _truthy(d[key])


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


def _log_line(msg: str) -> None:
    """Default progress sink for the startup wait: stdout, flushed per line.

    `flush=True` is load-bearing rather than tidiness. Under Windows Task
    Scheduler stdout is a redirected file, so Python block-buffers it; a three
    minute wait would then reach the log as one burst AFTER the wait ended,
    which is exactly as useful to the operator as no logging at all. Silence
    for three minutes is indistinguishable from a hang, and the whole point of
    these lines is that the operator can tell the two apart while it happens.

    stdout rather than stderr because the deployed desk redirects stdout to its
    log file, and because `__main__.py` already prints operator-facing progress
    there.
    """
    print(msg, flush=True)


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


class BridgeTimeout(RuntimeError):
    """The mailbox produced no matching reply inside `FileBridge.timeout`.

    A distinct TYPE rather than a message to match on, because the startup wait
    in `Mt4Broker.startup_connect()` must retry exactly this and must NOT retry
    an Expert that answered `ok=0`. Those are different facts: the first is
    "nothing is answering yet", which a cold boot fixes by waiting, and the
    second is a measured refusal from a live Expert, which waiting cannot fix
    and which must be reported at once. Telling them apart by reading
    `str(exc)` would make the retry policy depend on error wording, so the
    partition is a type.

    It subclasses `RuntimeError` so that every existing caller -- the
    `except (RuntimeError, OSError, ValueError)` handlers in `engine.py` and
    `__main__.py`, and the tests matching on "timeout" -- behaves exactly as
    before.
    """


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
        raise BridgeTimeout("mt4 bridge timeout")


class Mt4Broker:
    #: This venue can answer "no bars" now and serve them a moment later,
    #: because the terminal fetches history from the broker in the background.
    #: `history.preflight` reads this to decide whether a bounded wait could
    #: change the answer. A venue that answers from memory must NOT set it: a
    #: wait there is pure latency, and `run_backtest` seeds one bar on purpose
    #: and streams the rest.
    history_async = True

    def __init__(
        self,
        call: Call,
        *,
        magic: int = 0,
        startup_wait_sec: float = DEFAULT_STARTUP_WAIT_SEC,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._call = call
        self._magic = magic
        #: Zero or less means one ping and no wait, which is the pre-1.4.2
        #: behaviour and a legitimate operator choice on a host where MT4 is
        #: already up before the desk starts.
        self._startup_wait = max(0.0, float(startup_wait_sec))
        self._log = log if log is not None else _log_line

    def connect(self) -> None:
        """One ping, on the bridge's own steady-state timeout.

        This budget stays SHORT on purpose, and the reason is the call graph
        rather than taste. `ensure_connected()` below delegates here, and
        `Engine.step_all()` calls `ensure_connected()` on every single step;
        `Engine._reconnect_broker()` also lands here, on the trading path,
        after a mid-session blip. Giving this method a cold-boot-sized budget
        would convert a transient blip into a multi-minute stall while the desk
        is holding live positions, which is a worse defect than the startup
        race it would be fixing.

        Startup tolerance and steady-state tolerance are different numbers, so
        they are different methods: the wait lives in `startup_connect()`.
        """
        got = self._call("ping", {})
        if not _truthy(got.get("ok")):
            raise RuntimeError(str(got.get("error") or "mt4 ping failed"))

    def startup_connect(self) -> None:
        """`connect()`, retried until the Expert answers or the budget expires.

        Called once, by `Engine.start()`, and by nothing else. See
        `DEFAULT_STARTUP_WAIT_SEC` for why the budget is what it is.

        What is retried and what is not. A `BridgeTimeout` means no reply
        arrived, and an `OSError` means the mailbox itself could not be
        written; at boot both are ordinary, because MT4 may not have created
        or released the Common Files directory yet. Anything else propagates
        immediately, and the case that matters is an Expert that replied
        `ok=0`: that is a live Expert stating a diagnosis, waiting cannot
        change it, and retrying would bury the operator's actual error under
        three minutes of silence.

        The wall-clock bound is the budget PLUS one bridge timeout. A new
        attempt is only started while time remains, but an attempt already
        started is allowed to finish, and a single ping can cost a full
        `FileBridge.timeout`. Stating that is better than pretending the
        deadline is exact.
        """
        budget = self._startup_wait
        if budget <= 0:
            self.connect()
            return
        started = time.monotonic()
        deadline = started + budget
        self._log(
            f"mt4: waiting up to {budget:.0f}s for the Expert to answer on the mailbox"
        )
        attempt = 0
        gap = _STARTUP_GAP_MIN
        while True:
            attempt += 1
            try:
                self.connect()
            except (BridgeTimeout, OSError) as exc:
                elapsed = time.monotonic() - started
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"mt4 bridge never answered: {attempt} ping(s) over "
                        f"{elapsed:.1f}s, budget {budget:.0f}s, last error: {exc}. "
                        "Check that MetaTrader 4 is running, that "
                        "mt4/Experts/Mt4RiskBot.mq4 is attached to exactly one "
                        "chart with AutoTrading enabled, and that mt4.files_dir "
                        "is the Terminal Common Files folder."
                    ) from exc
                pause = min(gap, remaining)
                self._log(
                    f"mt4: no reply yet, attempt {attempt} at {elapsed:.1f}s "
                    f"of {budget:.0f}s ({exc}); retrying in {pause:.1f}s"
                )
                time.sleep(pause)
                gap = min(_STARTUP_GAP_MAX, gap * 2)
                continue
            self._log(
                f"mt4: Expert answered on attempt {attempt} after "
                f"{time.monotonic() - started:.1f}s"
            )
            return

    def ensure_connected(self) -> None:
        """Steady-state liveness check. Never the startup budget: see `connect`."""
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
        """Read a symbol spec, recording every field that was not measured.

        `d.get(key, DEFAULT) or DEFAULT` was used for all 15 reads here. `or`
        fires on a legitimate ZERO as well as on absence, so a broker-reported
        zero became a EURUSD-shaped default that no later check could tell from
        a real measurement. The last-line guard could not catch it either:
        `risk.py` recomputes `money_per_lot_at_stop` from this same spec, so a
        wrong number was compared against a wrong number and passed.

        MQL4 has exactly one tick-value identifier, `MODE_TICKVALUE`. There is
        no loss-leg variant, so the MT5 remedy of preferring a better field does
        not transfer. On MT4 the only honest answer is to refuse.
        """
        d = self._require("symbol", {"symbol": name.upper()})
        n = name.upper()
        unmeasured: set[str] = set()

        def measure(key: str, *, positive: bool) -> float:
            """The measured value, or 0.0 with `key` recorded as unmeasured.

            `positive` marks a field where zero is not a possible measurement,
            only a failed one: `MarketInfo` answers 0 for a symbol that is not
            in Market Watch. Fields where zero IS a real measurement, such as
            `digits` on an instrument quoted in whole points, pass
            `positive=False` so the reading survives.
            """
            if key not in d:
                unmeasured.add(key)
                return 0.0
            try:
                value = float(d[key])
            except (TypeError, ValueError):
                unmeasured.add(key)
                return 0.0
            if positive and value <= 0:
                unmeasured.add(key)
                return 0.0
            return value

        def derived(key: str, convention: str) -> str:
            """A field the Expert cannot send. The value is a naming convention.

            MQL4's `MarketInfo` has no per-symbol currency identifier, so this
            is a limit of the platform rather than a gap in the Expert. The
            convention is kept because it is useful and usually right, and the
            field is recorded as unmeasured so no caller mistakes it for a
            measurement.
            """
            if key in d:
                return str(d[key])
            unmeasured.add(key)
            return convention

        # Evaluated before the constructor call on purpose: `unmeasured` is
        # filled in by these, and relying on argument evaluation order to have
        # happened first would be a trap for the next reader.
        point = measure("point", positive=True)
        digits = measure("digits", positive=False)
        tick_size = measure("tick_size", positive=True)
        tick_value = measure("tick_value", positive=True)
        contract_size = measure("contract_size", positive=True)
        volume_min = measure("volume_min", positive=True)
        volume_max = measure("volume_max", positive=True)
        volume_step = measure("volume_step", positive=True)
        stops_level = measure("stops_level", positive=False)
        freeze_level = measure("freeze_level", positive=False)
        spread = measure("spread", positive=False)
        currency_base = derived("currency_base", n[:3] if len(n) >= 6 else "")
        currency_profit = derived("currency_profit", n[3:6] if len(n) >= 6 else "")
        currency_margin = derived("currency_margin", "")

        if "trade_mode" in d:
            trade_mode = int(d["trade_mode"])
        else:
            # MQL4 has no trade-mode identifier, so this can never be measured
            # over this wire. 0 is MQL5's DISABLED: the fail-closed direction.
            # It used to default to 4, full trading, which meant a close-only
            # symbol presented as fully tradable. Nothing in src/ reads this
            # field today, so that was latent rather than live; it is closed
            # here so it cannot become live later.
            unmeasured.add("trade_mode")
            trade_mode = 0

        return SymbolSpec(
            name=n,
            digits=int(digits),
            point=point,
            trade_tick_size=tick_size,
            trade_tick_value=tick_value,
            trade_contract_size=contract_size,
            volume_min=volume_min,
            volume_max=volume_max,
            volume_step=volume_step,
            trade_stops_level=int(stops_level),
            trade_freeze_level=int(freeze_level),
            filling_mode=1,
            currency_base=currency_base,
            currency_profit=currency_profit,
            currency_margin=currency_margin,
            trade_mode=trade_mode,
            spread=int(spread),
            unmeasured=frozenset(unmeasured),
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
        return self.history_probe(name, timeframe, count).bars

    def history_probe(self, name: str, timeframe: str | int, count: int) -> HistoryProbe:
        """`rates`, plus what the Expert says about the state of the series.

        Why a separate method rather than a wider `rates()`: the `Broker`
        Protocol (`broker/base.py`) is venue-neutral, every venue returns bars,
        and only MT4 has a history-download state to report. `history.preflight`
        duck-types on this method and reads any other venue through `rates()`,
        whose optional fields then stay None -- the honest reading, rather than a
        zero that would look like a measurement.

        `rates()` is implemented in terms of this so the wire is parsed in
        exactly one place. There is no second path that could drift.
        """
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
        return HistoryProbe(
            bars=out,
            bars_total=_reported_int(d, "bars_total"),
            selected=_reported_bool(d, "selected"),
            history_error=_reported_int(d, "history_error"),
        )

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
        return self._result(self._call("market", self._market_payload(order)), sends=True)

    def check_working(self, order: WorkingOrder) -> OrderResult:
        return self._result(self._call("check_working", self._working_payload(order)), placed=True)

    def working(self, order: WorkingOrder) -> OrderResult:
        return self._result(
            self._call("working", self._working_payload(order)), placed=True, sends=True
        )

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

    def _result(
        self, d: dict[str, Any], *, placed: bool = False, sends: bool = False
    ) -> OrderResult:
        if not isinstance(d, dict) or ("ok" not in d and "retcode" not in d):
            # The EA answered with no verdict at all, so nothing was measured.
            # This used to fall through to REJECT, which told the operator the
            # broker said no when the broker had in fact said nothing.
            err = d.get("error") if isinstance(d, dict) else None
            unknown = OrderResult.unknown(f"no result: {err or 'empty mt4 response'}")
            if not sends:
                return unknown
            # A send with no verdict cannot tell us whether a position was
            # opened, so survivorship is unknown too. Zero here would be the
            # same lie one layer down.
            return replace(unknown, survivor_ticket=None)
        ok = _truthy(d.get("ok"))
        raw = int(d.get("retcode", 0) or 0)
        if ok:
            if raw in {TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED}:
                code = raw
            else:
                code = TRADE_RETCODE_PLACED if placed else TRADE_RETCODE_DONE
        elif raw == 0:
            # A failure carrying no MT4 error. The Expert destroyed the reason:
            # GetLastError() clears the register on read, so a second read for
            # the reply returns 0. Calling that REJECT asserts the broker
            # refused the order, and nothing measured that. It is COULD NOT
            # MEASURE, and it reuses #19's vocabulary rather than a second one.
            code = RETCODE_UNKNOWN
        else:
            code = _MT4_RET.get(raw, raw if raw >= 10004 else TRADE_RETCODE_REJECT)
            if code == 0:
                code = TRADE_RETCODE_REJECT
        detail = str(d.get("error") or d.get("comment") or "")
        if code == RETCODE_UNKNOWN and not ok:
            detail = (detail + " (reason not reported by the Expert)").strip()
        return OrderResult(
            retcode=code,
            comment=detail,
            order=int(d.get("ticket", 0) or 0),
            deal=int(d.get("ticket", 0) or 0),
            volume=float(d.get("volume", 0) or 0),
            price=float(d.get("price", 0) or 0),
            # Only a send can strand a position. Every other op is asked
            # nothing, so it answers 0 rather than an alarming None.
            survivor_ticket=_survivor_ticket(d, ok) if sends else 0,
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
