"""Per-symbol history preflight: can this venue actually serve bars, and can
ATR be computed from them?

Why this module exists. MT4 keeps a price series per symbol AND timeframe, and
builds one only once something asks for it. A terminal with an H4 chart open and
a config set to H1 therefore has H1 history for nothing, and the desk's own
measurement on the live rig was:

    EURUSD  bars=0    ATR=nan
    USDJPY  bars=0    ATR=nan
    XAUUSD  bars=200  ATR=17.2188

Nothing crashed. `Engine.step_symbol` (`engine.py:466-468`) asks for bars, gets
an empty list, and returns without a journal record, so those two symbols were
silently untradeable and the operator-visible symptom was "the bot will not
trade EURUSD" with nothing anywhere saying why.

This module is the instrument that makes that condition a statement instead of
an inference. Three rules it exists to enforce:

- A missing series is never filled in. No synthetic bar, no default ATR, no ATR
  borrowed from another symbol. `broker/mt4_live.py:294-308` records what
  happened the last time a missing measurement got a plausible default: a broker
  zero became a EURUSD-shaped spec, and because `risk.py` recomputes from the
  same spec, a wrong number was compared against a wrong number and passed.
  Refusing is correct. The silence was the defect.

- Every symbol is NAMED. A report that says "2 symbols have no history" is not
  usable; the operator needs to know which ones.

- "Still downloading" and "will never arrive" are different worlds and get
  different verdicts. That distinction cannot be made from an empty bar list
  alone, which is why `HistoryProbe` carries the venue's own history error and
  why the MT4 Expert was changed to emit it. A probe that cannot tell those two
  apart reports the reassuring one.

The preflight also ASKS for the series, which on MT4 is what makes the terminal
request it from the server. So the common cold-start case is not reported at all:
it is fixed, silently and without the operator opening anything.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from straightedge.indicators import atr as atr_bars
from straightedge.indicators import last_closed
from straightedge.models import Bar

#: How hard the desk tries before it calls a series unavailable. Ten attempts
#: one second apart is nine seconds of waiting, chosen against the observed cost
#: of an MT4 history download for a few hundred bars (seconds, over the broker's
#: link) rather than against a round number. It is a startup cost paid once.
#: Raising it is cheaper than a false "unavailable"; lowering it is not.
ATTEMPTS = 10
DELAY_SEC = 1.0

#: MT4 error codes that mean something specific about history. Named because
#: `history_error=4066` in a journal line is not readable and the difference
#: between these two decides whether the operator waits or fixes a symbol name.
MT4_HISTORY_UPDATING = 4066  # ERR_HISTORY_WILL_UPDATED: the download is in flight
MT4_NO_HISTORY = 4073  # ERR_NO_HISTORY_DATA: the terminal has none and is not fetching

#: Every status `measure()` can produce. A closed roster, so a test can assert
#: the set is covered rather than assert one example of it (#61: a roster stops
#: being a denominator the moment a value is built by concatenation).
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_WARMING = "warming"
STATUS_NONE = "none"
STATUS_ERROR = "error"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_WARMING, STATUS_NONE, STATUS_ERROR)

#: Statuses worth asking again for. `error` is deliberately absent: a raising
#: `rates()` on MT4 is a five-second bridge timeout (`mt4_live.py:220`), so ten
#: retries of it is fifty seconds of a startup path, and a bridge that is not
#: answering is not a condition more asking repairs.
RETRYABLE = frozenset({STATUS_PARTIAL, STATUS_WARMING, STATUS_NONE})


@dataclass(frozen=True)
class HistoryProbe:
    """One venue answer to "give me `count` bars of `symbol` at `timeframe`".

    Everything past `bars` is OPTIONAL and defaults to None, which means NOT
    REPORTED, never a substituted value. MT4 is the only venue that has a
    history-download state to report; paper and MT5 answer from memory or from
    a synchronous API and have nothing to say here. A venue that stays silent
    must read as silent, so that an Expert too old to emit these fields is
    distinguishable from one that emitted them as zero -- the same partition
    `survivor_ticket` exists for (`Mt4RiskBot.mq4:142-147`).
    """

    bars: list[Bar]
    #: Bars the venue holds, which can exceed the bars it was asked for.
    bars_total: int | None = None
    #: Whether the venue could select the symbol at all (MT4 Market Watch).
    selected: bool | None = None
    #: The venue's own error code from the history access. 0 means the venue
    #: looked and reported no error, which is NOT the same as None.
    history_error: int | None = None


def waiting_helps(broker: object) -> bool:
    """Whether asking the same question again could produce a different answer.

    A venue whose terminal fetches history from the broker in the background
    (MT4, MT5) can answer 0 bars now and 200 a second later, so a bounded wait
    is the whole fix. A venue that answers from memory will answer identically
    every time, so a wait there is pure latency and nothing else:
    `engine.run_backtest` seeds ONE bar per symbol on purpose and streams the
    rest, and a nine-second wait on that would be nine seconds added to every
    backtest, in exchange for a verdict that was already correct at attempt one.

    Declared by the venue as `history_async`, not sniffed from its type.
    """
    return bool(getattr(broker, "history_async", False))


@dataclass(frozen=True)
class SymbolHistory:
    """What the venue served for one symbol, and what could be computed from it."""

    symbol: str
    bars: int
    needed: int
    #: The last finite ATR, or None when ATR could not be computed. None is not
    #: 0.0 and is not nan: it is the absence of a measurement, and it is printed
    #: as "ATR unavailable" so a reader never sees a number that was not read.
    atr: float | None
    status: str
    attempts: int
    error: str = ""
    bars_total: int | None = None
    selected: bool | None = None
    history_error: int | None = None

    @property
    def usable(self) -> bool:
        """True only when the strategy can actually produce a signal.

        `bars > 0` is not the bar. `Strategy.signal` returns FLAT `warmup` until
        it has `needed_bars()` (`strategy.py:25-27`), so a partially downloaded
        series is a permanent non-trade that looks exactly like a working one.
        """
        return self.status == STATUS_OK

    def line(self) -> str:
        """One operator-readable sentence. Always names the symbol first."""
        atr_text = "ATR unavailable" if self.atr is None else f"ATR={self.atr:.6f}"
        head = f"{self.symbol}: {self.bars} bars (need {self.needed}), {atr_text}"
        if self.status == STATUS_OK:
            return head
        if self.status == STATUS_ERROR:
            return (
                f"{self.symbol}: COULD NOT MEASURE (need {self.needed}) -- "
                f"the rates call failed: {self.error}"
            )
        return f"{head} -- {self._why()}"

    def _why(self) -> str:
        if self.status == STATUS_PARTIAL:
            return (
                f"NOT ENOUGH: the strategy needs {self.needed} and will report "
                f"warmup forever on {self.bars}"
            )
        if self.status == STATUS_WARMING:
            return (
                f"STILL DOWNLOADING after {self.attempts} attempts (venue error "
                f"{MT4_HISTORY_UPDATING} ERR_HISTORY_WILL_UPDATED); the wait was "
                f"too short, not the symbol wrong"
            )
        if self.selected is False:
            return "the venue could not select this symbol at all"
        if self.history_error == MT4_NO_HISTORY:
            return (
                f"the terminal reports NO HISTORY DATA for this symbol and "
                f"timeframe (venue error {MT4_NO_HISTORY})"
            )
        if self.history_error is None:
            return (
                f"the venue served nothing after {self.attempts} attempts and "
                f"reported no history error, so COULD NOT MEASURE whether the "
                f"download is in flight or the symbol is not served"
            )
        return (
            f"the venue served nothing after {self.attempts} attempts and was "
            f"not fetching any (venue error {self.history_error})"
        )


@dataclass(frozen=True)
class HistoryReport:
    """Every configured symbol, measured. `ok` is the run gate."""

    timeframe: str
    needed: int
    attempts: int
    symbols: tuple[SymbolHistory, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return all(s.usable for s in self.symbols)

    @property
    def unusable(self) -> tuple[SymbolHistory, ...]:
        return tuple(s for s in self.symbols if not s.usable)

    def names(self) -> list[str]:
        """The unusable symbols, by name. The point of the whole module."""
        return [s.symbol for s in self.unusable]

    def lines(self) -> list[str]:
        return [s.line() for s in self.symbols]

    def rows(self) -> list[dict[str, Any]]:
        """Journal payload: one dict per symbol, every field measured or None."""
        return [
            {
                "symbol": s.symbol,
                "bars": s.bars,
                "needed": s.needed,
                "atr": s.atr,
                "status": s.status,
                "attempts": s.attempts,
                "error": s.error,
                "bars_total": s.bars_total,
                "selected": s.selected,
                "history_error": s.history_error,
            }
            for s in self.symbols
        ]

    def text(self) -> str:
        """The loud block. Named symbols first, then what to do about it.

        The remedy is stated once rather than per symbol, and it is a remedy:
        nothing here tells the operator to keep a chart open, because a rule a
        human has to remember is not a control.
        """
        if self.ok:
            return ""
        head = (
            f"NO USABLE {self.timeframe} HISTORY: "
            + ", ".join(self.names())
            + f" ({len(self.unusable)} of {len(self.symbols)} configured symbols)"
        )
        body = [f"  {s.line()}" for s in self.unusable]
        tail = [
            "  These symbols cannot produce a signal and will never trade.",
            "  Check the symbol name matches the broker's own spelling (many use",
            f"  suffixes like EURUSD.m) and that the broker serves {self.timeframe}",
            "  for it. The desk already asked the terminal to download it "
            f"{self.attempts} times.",
        ]
        return "\n".join([head, *body, *tail])


def _probe(broker: object, symbol: str, timeframe: str, count: int) -> HistoryProbe:
    """Ask the venue, preferring a probe that reports its own history state.

    Duck-typed on purpose. The `Broker` Protocol (`broker/base.py`) is
    venue-neutral and `rates()` returns bars only, so widening it for one
    venue's download state would put an MT4 concept in every venue's signature.
    A venue with a richer answer offers `history_probe`; one without it is read
    through `rates()` and its optional fields stay None, which is the honest
    reading rather than a fabricated one.
    """
    probe_fn = getattr(broker, "history_probe", None)
    if callable(probe_fn):
        got = probe_fn(symbol, timeframe, count)
        if isinstance(got, HistoryProbe):
            return got
    rates = getattr(broker, "rates")
    return HistoryProbe(bars=list(rates(symbol, timeframe, count)))


def measure(
    broker: object,
    symbol: str,
    *,
    timeframe: str,
    needed: int,
    atr_period: int,
    attempt: int = 1,
) -> SymbolHistory:
    """One measurement of one symbol. Never raises; a failure is a status."""
    try:
        probe = _probe(broker, symbol, timeframe, needed + 2)
    except (RuntimeError, OSError, ValueError) as exc:
        return SymbolHistory(
            symbol=symbol,
            bars=0,
            needed=needed,
            atr=None,
            status=STATUS_ERROR,
            attempts=attempt,
            error=str(exc)[:200],
        )

    bars: list[Bar] = list(probe.bars)
    value = last_closed(atr_bars(bars, atr_period)) if bars else float("nan")
    atr = value if value == value else None

    if len(bars) >= needed and atr is not None:
        status = STATUS_OK
    elif bars:
        status = STATUS_PARTIAL
    elif probe.history_error == MT4_HISTORY_UPDATING:
        status = STATUS_WARMING
    else:
        status = STATUS_NONE

    return SymbolHistory(
        symbol=symbol,
        bars=len(bars),
        needed=needed,
        atr=atr,
        status=status,
        attempts=attempt,
        bars_total=probe.bars_total,
        selected=probe.selected,
        history_error=probe.history_error,
    )


def preflight(
    broker: object,
    symbols: Sequence[str],
    *,
    timeframe: str,
    needed: int,
    atr_period: int,
    attempts: int | None = None,
    delay: float = DELAY_SEC,
    sleep: Callable[[float], None] = time.sleep,
) -> HistoryReport:
    """Warm every symbol, then report each one by name.

    The loop is the warm-up. Asking for a series is what makes an MT4 terminal
    request it from the server, so the first round is both the trigger and the
    first measurement, and later rounds are the bounded wait. Only symbols that
    are still not usable are asked again, and the happy path sleeps zero times.

    The wait lives HERE and not in the Expert on purpose. `Process()`
    (`Mt4RiskBot.mq4:66-95`) is a single-threaded mailbox pumped from `OnTimer`
    behind a `gBusy` flag, and the adapter's bridge times out at 5 seconds
    (`mt4_live.py:220`), so a `Sleep()` inside the Expert's rates handler would
    stall every other op and blow that timeout. The Expert triggers and reports;
    the desk waits.
    """
    names = [str(s).upper() for s in symbols]
    budget = (ATTEMPTS if waiting_helps(broker) else 1) if attempts is None else attempts
    budget = max(1, budget)
    results: dict[str, SymbolHistory] = {}
    for attempt in range(1, budget + 1):
        pending = [n for n in names if n not in results or results[n].status in RETRYABLE]
        if not pending:
            break
        if attempt > 1:
            sleep(delay)
        for name in pending:
            results[name] = measure(
                broker,
                name,
                timeframe=timeframe,
                needed=needed,
                atr_period=atr_period,
                attempt=attempt,
            )
    return HistoryReport(
        timeframe=timeframe,
        needed=needed,
        attempts=budget,
        symbols=tuple(results[n] for n in names),
    )
