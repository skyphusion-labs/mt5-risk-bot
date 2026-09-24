"""Issue #31, two Expert-side reply defects. They are not the same defect.

HALF 1, the error half. `GetLastError()` CLEARS the error as a side effect of
reading it. `SendRetry` and `ModifyRetry` read it inside their retry loops and
then throw it away, so the caller's second read returns 0 and the reply carries
no reason. The desk cannot tell "not enough money" from "market closed".

HALF 2, the sanitation half. The Expert joins `OrderComment()` into a
pipe-separated row without stripping pipes, while the Python side strips them on
the way out (`_wire`). A broker comment containing a pipe shifts every field
after it, and `positions()` raises `ValueError` out of `float()`. The desk
cannot enumerate its own book, and the trigger is data the broker controls.

MQL4 does not execute in this suite. Half 1 is therefore covered by source
guards over the shipped `.mq4`: they are a real gate on the shipped artifact and
go red when it regresses, but they are not a behavioural test of a terminal.
Half 2 gets both: source guards for the Expert, and a genuine end-to-end run
where a raw broker comment goes through a faithful port of the Expert's
sanitiser, onto the real wire, and back through the real adapter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mt4_transcripts import EA_POS_EMIT, Transcript, ea_rows_reply, pos_row

from mt5_risk_bot.broker.mt4_live import Mt4Broker, _wire
from mt5_risk_bot.models import Side

EA_PATH = Path(__file__).resolve().parents[1] / "mt4" / "Experts" / "Mt4RiskBot.mq4"

# The string-returning terminal accessors whose result the Expert writes
# straight into a reply. Every one of these is broker-controlled text.
BROKER_STRINGS = (
    "OrderComment()",
    "OrderSymbol()",
    "AccountName()",
    "AccountServer()",
    "AccountCurrency()",
)

# Handlers whose error was already consumed by a retry helper, so none of them
# may call GetLastError() for the reply.
ERROR_LAUNDERING_HANDLERS = (
    "string CheckMarket(string id, string body, bool send)",
    "string CheckWorking(string id, string body, bool send)",
    "string ModifyPos(string id, string body)",
    "string ModifyPend(string id, string body)",
)


def _ea_source() -> str:
    return EA_PATH.read_text(encoding="utf-8")


def _function_body(src: str, signature: str) -> str:
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


def ea_sanitize(s: str) -> str:
    """Faithful port of the Expert's `Wire()`.

    Deliberately a port rather than a call to `mt5_risk_bot`'s `_wire`: the
    point is that the two ends agree on a rule, and asserting the port against
    `_wire` is what proves it. Testing `_wire` against itself would prove
    nothing.

    Framing characters only. `_wire` also forces ASCII, which MQL4 has no cheap
    equivalent for; that asymmetry is documented in docs/MT4.md rather than
    papered over here.
    """
    return s.replace("\r", " ").replace("\n", " ").replace("|", "/")


# --------------------------------------------------------------------------
# Half 1: the real broker error must survive the retry loop
# --------------------------------------------------------------------------


def test_send_retry_hands_back_the_error_it_consumed() -> None:
    src = _ea_source()
    sig = [line for line in src.splitlines() if line.startswith("int SendRetry(")]
    assert sig, "SendRetry not found"
    assert "int &err" in sig[0], (
        "SendRetry reads GetLastError() inside its loop, which CLEARS it, and then "
        "returns only -1. The caller's second read gets 0, so the reply carries no "
        "reason for the rejection. The captured error has to come back out:\n" + sig[0]
    )


def test_modify_retry_hands_back_the_error_it_consumed() -> None:
    src = _ea_source()
    sig = [line for line in src.splitlines() if line.startswith("bool ModifyRetry(")]
    assert sig, "ModifyRetry not found"
    assert "int &err" in sig[0], (
        "ModifyRetry consumes the error the same way, and its OrderSelect branch "
        "returns without reading it at all, so the caller reads a DIFFERENT call's "
        "error. Both paths have to report what they saw:\n" + sig[0]
    )


@pytest.mark.parametrize("signature", ERROR_LAUNDERING_HANDLERS)
def test_a_reply_never_reads_the_error_register_directly(signature: str) -> None:
    """The precise defect: the REPLY reads `GetLastError()`.

    Capturing the register into a variable right after the call that failed is
    correct and stays allowed; `Cancel` and `Close` do exactly that and are
    fine. What is never correct is reading it while BUILDING the reply, because
    by then a retry helper has already consumed it and the read returns 0.
    """
    body = _function_body(_ea_source(), signature)
    offenders = [
        line.strip()
        for line in body.splitlines()
        if "GetLastError()" in line and ("Fail(" in line or "FailTrade(" in line)
    ]
    assert offenders == [], (
        f"{signature.split('(')[0]} reads GetLastError() while building its reply, "
        "after a retry helper already consumed it, so the value it sends is 0 and "
        "the real reason is gone:\n" + "\n".join(offenders)
    )


def test_a_failure_with_no_reason_is_not_reported_as_a_broker_rejection(tmp_path: Path) -> None:
    """The adapter half of the same lie.

    `retcode=0` on a failure means the Expert did not report a reason. Mapping
    that to REJECT asserts the broker refused the order, which is a claim
    nothing measured. It has to read as COULD NOT MEASURE.
    """
    from mt4_transcripts import TranscriptExpert

    reason_destroyed = Transcript("market", "id={id}\nok=0\nretcode=0\nerror=OrderSend\n")
    with TranscriptExpert(tmp_path, {"market": reason_destroyed}):
        res = _broker(tmp_path).market(_order())
    assert not res.ok
    assert not res.measured, (
        "the Expert reported a failure with no reason, and the adapter dressed it as "
        "a broker rejection; nothing measured that the broker refused anything"
    )
    assert "reason" in res.comment.lower() or "not reported" in res.comment.lower(), (
        f"the comment should say the reason was not reported, got {res.comment!r}"
    )


def test_a_failure_that_does_carry_a_reason_still_maps_to_it(tmp_path: Path) -> None:
    """The positive control. If this also went unknown, the change above would
    have measured the instrument rather than the defect."""
    from mt4_transcripts import TranscriptExpert
    from mt5_risk_bot.constants import TRADE_RETCODE_NO_MONEY

    no_money = Transcript("market", "id={id}\nok=0\nretcode=134\nerror=OrderSend\n")
    with TranscriptExpert(tmp_path, {"market": no_money}):
        res = _broker(tmp_path).market(_order())
    assert not res.ok
    assert res.measured
    assert res.retcode == TRADE_RETCODE_NO_MONEY


# --------------------------------------------------------------------------
# Half 2: the Expert must sanitise what the broker gave it
# --------------------------------------------------------------------------


def test_the_expert_has_a_wire_sanitiser() -> None:
    src = _ea_source()
    assert "string Wire(string" in src, (
        "the Expert has no sanitiser, so broker text goes into a pipe-separated row "
        "exactly as the terminal handed it over"
    )
    body = _function_body(src, "string Wire(string")
    for ch in ('"|"', '"\\r"', '"\\n"'):
        assert ch in body, f"Wire() does not handle {ch}"


@pytest.mark.parametrize("accessor", BROKER_STRINGS)
def test_every_broker_string_is_sanitised_before_it_reaches_the_wire(accessor: str) -> None:
    src = _ea_source()
    offenders = []
    for signature in ("string BookReply(", "string AccountReply("):
        if signature not in src:
            continue
        for line in _function_body(src, signature).splitlines():
            if accessor not in line or f"Wire({accessor}" in line:
                continue
            # Only lines that EMIT. `MarketInfo(OrderSymbol(), MODE_DIGITS)`
            # passes the symbol to a lookup and must NOT be sanitised; wrapping
            # it would corrupt the lookup. An emitting line carries a field
            # separator or a `key=` prefix.
            if '"|"' in line or '="' in line:
                offenders.append(line.strip())
    assert offenders == [], (
        f"{accessor} is broker-controlled text written into a reply without "
        "sanitation. A pipe in it shifts every field after it:\n" + "\n".join(offenders)
    )


def test_the_two_ends_agree_on_the_framing_rule() -> None:
    """The Expert's rule and `_wire`'s rule have to be the same rule.

    Judged on the characters the wire uses as structure. Any disagreement here
    means one end strips something the other keeps, which is the defect wearing
    a fix.
    """
    for raw in (
        "rb-1|from #123",
        "a|b|c",
        "with\r\nnewlines",
        "trailing|",
        "|leading",
        "clean-comment",
        "",
        "[sl]|tp hit",
    ):
        assert ea_sanitize(raw) == _wire(raw), f"rules disagree on {raw!r}"


def test_a_pipe_in_a_broker_comment_does_not_shift_the_book(tmp_path: Path) -> None:
    """End to end on the real wire, and the reason half 2 is the sharper defect.

    A broker appends annotations to comments, so `rb-1|from #123` is ordinary
    text, not a crafted input. Unsanitised it truncates the comment, moves
    `swap` onto the comment tail and `time` onto `swap`, and `positions()`
    raises `ValueError` out of `float()`: the desk cannot read its own book.
    """
    from mt4_transcripts import TranscriptExpert

    broker_comment = "rb-1|from #123"
    row = pos_row(comment=ea_sanitize(broker_comment), swap=-0.42, time_=1758694000)
    shifted = Transcript("positions", ea_rows_reply(EA_POS_EMIT, [row]))
    with TranscriptExpert(tmp_path, {"positions": shifted}):
        got = _broker(tmp_path).positions()
    assert len(got) == 1
    pos = got[0]
    assert pos.comment == "rb-1/from #123", "the annotation must survive, just not as a pipe"
    assert pos.swap == -0.42, "swap landed in the wrong field"
    assert pos.time == 1758694000, "time landed in the wrong field"
    assert pos.ticket == 80051234
    assert pos.symbol == "EURUSD"
    assert pos.side is Side.BUY
    assert pos.volume == 0.17


def test_an_unsanitised_row_is_what_breaks_the_book(tmp_path: Path) -> None:
    """Characterisation, not a guard on my fix.

    This pins the consequence the Expert's sanitiser exists to prevent, so a
    future reader can see why the rule matters. It holds before and after the
    fix; it is evidence of the harm, never evidence that the fix works.
    """
    from mt4_transcripts import TranscriptExpert

    row = pos_row(comment="rb-1|from #123")
    unsanitised = Transcript("positions", ea_rows_reply(EA_POS_EMIT, [row]))
    with TranscriptExpert(tmp_path, {"positions": unsanitised}):
        with pytest.raises(ValueError):
            _broker(tmp_path).positions()


# --------------------------------------------------------------------------


def _broker(tmp_path: Path) -> Mt4Broker:
    from mt5_risk_bot.broker.mt4_live import FileBridge

    return Mt4Broker(FileBridge(tmp_path, timeout_sec=5.0).call, magic=770077)


def _order():
    from mt5_risk_bot.models import MarketOrder

    return MarketOrder(
        symbol="EURUSD", side=Side.BUY, volume=0.17, sl=1.09815, tp=1.10415, magic=770077
    )


def test_every_failing_path_out_of_modify_retry_records_what_it_saw() -> None:
    """Found while mutation-testing the guards above, which missed it.

    `ModifyRetry` has two distinct failure exits: `OrderSelect` refused, and
    `OrderModify` refused. An out-parameter on the signature does not prove both
    exits fill it in, and the `OrderSelect` one originally did not, so the
    caller read whatever the register happened to hold.
    """
    body = _function_body(_ea_source(), "bool ModifyRetry(")
    captures = body.count("err = GetLastError()")
    assert captures >= 2, (
        "ModifyRetry has two failing exits and only "
        f"{captures} of them records the error. The exit that records nothing "
        "hands the caller a stale register, which is a wrong answer rather than "
        "a missing one."
    )
