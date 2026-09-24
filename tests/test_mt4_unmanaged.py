"""Issue #23: an MT4 market order must never be left open with no stop while
the desk is told the send failed.

Two surfaces are covered here, and they are not the same kind of evidence.

1. Source guards over the shipped Expert. MQL4 does not execute in this suite,
   so these read `mt4/Experts/Mt4RiskBot.mq4` and assert the structural
   invariants that make the leak impossible. They are a real gate on the
   shipped artifact (mutate the .mq4 and they go red), but they are not a
   behavioural test of a running terminal.
2. Adapter tests over the real mailbox. These drive encode -> file -> decode ->
   OrderResult through FileBridge on a real directory, so the wire contract is
   exercised end to end rather than through a hand-made dict.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from mt5_risk_bot.broker.mt4_live import FileBridge, Mt4Broker, decode
from mt5_risk_bot.models import MarketOrder, Side, WorkingOrder

EA_PATH = Path(__file__).resolve().parents[1] / "mt4" / "Experts" / "Mt4RiskBot.mq4"

# Every MQL4 call that can change the state of the book. If one of these is
# written as a bare statement its return value is discarded, and the Expert
# then cannot know whether the position it just tried to close is still open.
ORDER_MUTATORS = (
    "OrderSend(",
    "OrderClose(",
    "OrderCloseBy(",
    "OrderDelete(",
    "OrderModify(",
)


def _ea_source() -> str:
    return EA_PATH.read_text(encoding="utf-8")


def _function_body(src: str, signature: str) -> str:
    """The brace-matched body of one MQL4 function, signature included."""
    start = src.index(signature)
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _after_send(body: str) -> str:
    """The region of a send handler in which a position may already exist.

    Everything after the `ticket < 0` branch. Before that point a failure is a
    clean failure: nothing was opened, so a bare Fail is honest there.
    """
    marker = '"OrderSend"'
    return body[body.index(marker) + len(marker) :]


def test_no_order_mutating_call_discards_its_result() -> None:
    offenders = []
    for n, line in enumerate(_ea_source().splitlines(), 1):
        stripped = line.strip()
        for fn in ORDER_MUTATORS:
            if stripped.startswith(fn):
                offenders.append(f"{EA_PATH.name}:{n}: {stripped}")
    assert offenders == [], (
        "an order-mutating call whose return value is discarded cannot tell the "
        "Expert whether the position is still open, so the Expert reports a "
        "failure it has not verified:\n" + "\n".join(offenders)
    )


def test_market_send_never_returns_a_bare_failure_while_a_position_may_be_open() -> None:
    body = _function_body(_ea_source(), "string CheckMarket(string id, string body, bool send)")
    bare = [
        line.strip() for line in _after_send(body).splitlines() if "return Fail(" in line
    ]
    assert bare == [], (
        "CheckMarket returns a plain failure after the position has been opened. "
        "Fail() carries no ticket, so the desk is told the send failed while the "
        "position is live with no stop:\n" + "\n".join(bare)
    )


def test_working_send_never_returns_a_bare_failure_while_an_order_may_be_live() -> None:
    body = _function_body(_ea_source(), "string CheckWorking(string id, string body, bool send)")
    bare = [
        line.strip() for line in _after_send(body).splitlines() if "return Fail(" in line
    ]
    assert bare == [], (
        "CheckWorking returns a plain failure after the order has been placed:\n"
        + "\n".join(bare)
    )


def test_oninit_reports_unmanaged_positions_instead_of_adopting_them() -> None:
    src = _ea_source()
    init = _function_body(src, "int OnInit()")
    assert "ReportUnmanaged(" in init, (
        "OnInit does not scan the book, so a position left open with no stop by a "
        "previous session is adopted in silence and never reconciled"
    )
    body = _function_body(src, "void ReportUnmanaged(")
    assert "OrdersTotal(" in body, "ReportUnmanaged does not enumerate the book"
    assert "OrderStopLoss(" in body, "ReportUnmanaged does not look at the stop"


class _Mailbox:
    """A stand-in Expert that writes one canned reply body per request id.

    The reply is the literal bytes the Expert emits, so decode() and _result()
    are exercised as shipped rather than handed a Python dict.
    """

    def __init__(self, directory: Path, reply: str) -> None:
        self.dir = directory
        self.reply = reply
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        req = self.dir / "mt4_risk_bot.req"
        res = self.dir / "mt4_risk_bot.res"
        while not self.stop.is_set():
            if req.exists():
                try:
                    text = req.read_text(encoding="utf-8")
                    req.unlink()
                except OSError:
                    time.sleep(0.01)
                    continue
                data = decode(text)
                res.write_text(
                    self.reply.format(id=data.get("id", 0)), encoding="utf-8"
                )
            time.sleep(0.01)

    def __enter__(self) -> "_Mailbox":
        self.dir.mkdir(parents=True, exist_ok=True)
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self.thread.join(timeout=1.0)


def _market() -> MarketOrder:
    return MarketOrder(
        symbol="EURUSD", side=Side.BUY, volume=0.10, sl=1.0950, tp=1.1100, magic=7
    )


def _working() -> WorkingOrder:
    return WorkingOrder(
        symbol="EURUSD",
        side=Side.BUY,
        kind="limit",
        volume=0.10,
        price=1.0980,
        sl=1.0950,
        magic=7,
    )


def test_failed_market_send_surfaces_the_surviving_ticket(tmp_path: Path) -> None:
    # The Expert could not attach the stop and could not verify the rollback,
    # so ticket 777 is live with no stop.
    reply = (
        "id={id}\nok=0\nretcode=130\nerror=sl_modify_failed_position_live\n"
        "survivor_ticket=777\n"
    )
    with _Mailbox(tmp_path, reply):
        res = Mt4Broker(FileBridge(tmp_path, timeout_sec=5.0).call, magic=7).market(_market())
    assert not res.ok, "the send did fail; that part is honest today"
    assert res.survivor_ticket == 777, (
        "the desk is told the send failed and is given no way to learn that "
        "ticket 777 is still open with no stop"
    )


def test_failed_working_send_surfaces_the_surviving_ticket(tmp_path: Path) -> None:
    reply = (
        "id={id}\nok=0\nretcode=130\nerror=sl_modify_failed_order_live\n"
        "survivor_ticket=901\n"
    )
    with _Mailbox(tmp_path, reply):
        res = Mt4Broker(FileBridge(tmp_path, timeout_sec=5.0).call, magic=7).working(_working())
    assert not res.ok
    assert res.survivor_ticket == 901


def test_a_verified_rollback_reports_no_survivor(tmp_path: Path) -> None:
    # The Expert closed the ticket and the book confirms it is gone. Zero here
    # is a measured zero, and it must not be confused with the case below.
    reply = "id={id}\nok=0\nretcode=130\nerror=sl_modify_failed\nsurvivor_ticket=0\n"
    with _Mailbox(tmp_path, reply):
        res = Mt4Broker(FileBridge(tmp_path, timeout_sec=5.0).call, magic=7).market(_market())
    assert not res.ok
    assert res.survivor_ticket == 0


def test_an_expert_that_never_answers_is_unknown_not_zero(tmp_path: Path) -> None:
    # An Expert older than this fix says nothing about survivorship. That is
    # COULD NOT MEASURE. Reporting it as zero would be the same defect in a
    # new place: an absent measurement wearing a measurement's clothes.
    reply = "id={id}\nok=0\nretcode=130\nerror=sl_modify_failed\n"
    with _Mailbox(tmp_path, reply):
        res = Mt4Broker(FileBridge(tmp_path, timeout_sec=5.0).call, magic=7).market(_market())
    assert not res.ok
    assert res.survivor_ticket is None


def test_a_successful_send_reports_no_survivor(tmp_path: Path) -> None:
    reply = "id={id}\nok=1\nticket=42\nvolume=0.10\nprice=1.10020\n"
    with _Mailbox(tmp_path, reply):
        res = Mt4Broker(FileBridge(tmp_path, timeout_sec=5.0).call, magic=7).market(_market())
    assert res.ok
    assert res.order == 42
    assert res.survivor_ticket == 0


# --- the condition has to be consumed, or naming it is decoration ----------


def _engine(tmp_path: Path):
    from mt5_risk_bot.broker.paper import PaperBroker
    from mt5_risk_bot.config import BotConfig, SessionConfig
    from mt5_risk_bot.engine import Engine
    from mt5_risk_bot.journal import Journal

    cfg = BotConfig()
    cfg.session = SessionConfig(enabled=False)
    cfg.symbols = ["EURUSD"]
    return Engine(cfg, PaperBroker(balance=10_000.0), journal=Journal(tmp_path / "j.jsonl"))


def _result(**kw):
    from mt5_risk_bot.constants import TRADE_RETCODE_INVALID_STOPS
    from mt5_risk_bot.models import OrderResult

    kw.setdefault("retcode", TRADE_RETCODE_INVALID_STOPS)
    kw.setdefault("comment", "sl_modify_failed_position_live")
    return OrderResult(**kw)


def test_engine_journals_an_unmanaged_position(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    eng._report_survivor("EURUSD", _result(survivor_ticket=777))
    rec = eng.journal.last_event("unmanaged_position")
    assert rec is not None, "a live position the desk thinks does not exist was not journalled"
    assert rec["ticket"] == 777
    assert rec["symbol"] == "EURUSD"


def test_engine_journals_a_survivor_it_could_not_measure(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    eng._report_survivor("EURUSD", _result(survivor_ticket=None))
    rec = eng.journal.last_event("survivor_unknown")
    assert rec is not None, "an unanswered question was recorded as an all clear"
    assert rec["symbol"] == "EURUSD"


def test_engine_stays_quiet_when_nothing_survived(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    eng._report_survivor("EURUSD", _result(survivor_ticket=0, comment="sl_modify_failed"))
    assert eng.journal.last_event("unmanaged_position") is None
    assert eng.journal.last_event("survivor_unknown") is None


def test_operator_is_told_in_words_not_only_in_the_journal() -> None:
    from mt5_risk_bot.engine import _format_event

    live = _format_event("unmanaged_position", {"ticket": 777, "symbol": "EURUSD", "retcode": 10016})
    assert "777" in live
    assert "NO STOP" in live

    unknown = _format_event("survivor_unknown", {"symbol": "EURUSD", "retcode": 10016})
    assert "COULD NOT MEASURE" in unknown


def test_a_send_with_no_verdict_at_all_leaves_survivorship_unknown(tmp_path: Path) -> None:
    """The seam between #19 and #23.

    #19 made a reply carrying neither `ok` nor `retcode` report COULD NOT
    MEASURE instead of REJECT. On a SEND that also means we cannot know whether
    a position was opened, so survivorship is unknown rather than zero. Neither
    change could see this on its own.
    """
    with _Mailbox(tmp_path, "id={id}\nerror=terminal_busy\n"):
        res = Mt4Broker(FileBridge(tmp_path, timeout_sec=5.0).call, magic=7).market(_market())
    assert not res.measured, "#19's guard still has to fire here"
    assert res.survivor_ticket is None, (
        "the Expert gave no verdict on a send, so whether a position was opened "
        "is unknown; reporting zero would be the same defect one layer down"
    )


def test_a_non_send_with_no_verdict_is_not_an_alarm(tmp_path: Path) -> None:
    # A cancel that came back empty cannot have stranded a position.
    with _Mailbox(tmp_path, "id={id}\nerror=terminal_busy\n"):
        res = Mt4Broker(FileBridge(tmp_path, timeout_sec=5.0).call, magic=7).cancel(55)
    assert not res.measured
    assert res.survivor_ticket == 0
