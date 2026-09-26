"""The Expert must refuse a request the desk has already given up on.

`FileBridge` now withdraws an abandoned request, but that layer cannot run if the
desk PROCESS died between writing the request and noticing the timeout, and that
case has no bound on it at all: the request sits on the shared name and the Expert
executes it on its next 100ms poll, minutes or hours later. Observed on the live
box as a silent restart at 2026-09-26T01:56:49Z with no traceback in `desk.err`
and no Application event.

So the request carries `ttl_ms` and the Expert refuses one that is older. This is
the layer that survives the desk not being there any more, and it is the only one
that does.

MQL4 DOES NOT EXECUTE IN THIS SUITE. These are source guards over the shipped
Expert, exactly the kind of evidence `tests/test_mt4_claim_open_retry.py` and
`tests/test_mt4_unmanaged.py` carry: they go red if the .mq4 is mutated, and they
are NOT a behavioural test of a running terminal. The Expert half of this change
has not been compiled anywhere -- there is no MQL4 compiler in CI or on the
developer seat -- so it must be compiled and attached on the terminal host before
it is trusted. That is called out in the PR rather than implied by a green suite
here.

What must stay true, and why each guard exists:

* the age is MEASURED, not guessed. MQL4 does not state whether FILE_MODIFY_DATE
  comes back in local time or UTC, and the two wrong answers fail in opposite
  directions: one stops the desk working, the other silently removes the fence.
* the stamp is read BEFORE the claim rename, because a rename's effect on a
  modification time is undocumented and a refreshed one would make every request
  look new -- a fence that passes everything while appearing to work.
* an UNMEASURABLE age refuses a send and allows a read, which is the same read
  versus send asymmetry the budgets carry.
* the refusal is LOUD and states the age, because a silently dropped request is
  indistinguishable from one that was never written.
* nothing is sent on the refusal path, and the reply says so: `survivor_ticket=0`
  here is a real measurement, because the refusal happens before `OrderSend`.
"""

from __future__ import annotations

import re
from pathlib import Path

from straightedge.constants import MAILBOX_SEND_OPS

EA_PATH = Path(__file__).resolve().parents[1] / "mt4" / "Experts" / "Mt4RiskBot.mq4"


def _ea() -> str:
    return EA_PATH.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    src = _ea()
    start = src.index(name)
    return src[start : src.index("\n}", start)]


def _process_fence() -> str:
    src = _ea()
    start = src.index("LAYER 1d, the stale fence")
    return src[start : src.index("else\n      reply = Handle(body);", start)]


class TestTheAgeIsMeasuredNotGuessed:
    def test_the_expert_calibrates_the_file_time_offset_at_init(self) -> None:
        assert "gFileTimeKnown = CalibrateFileTime();" in _function_body("int OnInit()"), (
            "OnInit no longer calibrates, so every age comparison rests on an "
            "assumption about whether FILE_MODIFY_DATE is local time or UTC"
        )

    def test_the_calibration_writes_its_own_probe_and_reads_it_back(self) -> None:
        body = _function_body("bool CalibrateFileTime()")
        assert "FileOpen(probe" in body
        assert "FileGetInteger(probe, FILE_MODIFY_DATE, true)" in body
        assert "gFileTimeOffset = now - stamp;" in body
        assert "FileDelete(probe, FILE_COMMON);" in body, (
            "the probe file is left in the mailbox directory"
        )

    def test_a_failed_calibration_returns_false_rather_than_assuming_zero(self) -> None:
        body = _function_body("bool CalibrateFileTime()")
        assert body.count("return false;") == 2, (
            "one of the two calibration failure arms no longer returns false, so "
            "an unmeasured offset would be used as if it were zero"
        )
        assert "REFUSED" in body, "a failed calibration does not say what it costs"

    def test_an_unmeasurable_age_is_minus_one_and_never_zero(self) -> None:
        body = _function_body("int RequestAgeSec(int stamp)")
        assert "if(!gFileTimeKnown || stamp <= 0)\n      return -1;" in body
        assert "if(age < 0)\n      return -1;" in body, (
            "a negative age is not rejected, so a clock or timezone disagreement "
            "would make an old request look like it arrived in the future, which "
            "reads as FRESH"
        )


class TestTheStampIsTakenBeforeTheClaim:
    def test_the_shared_name_is_stamped_before_the_rename(self) -> None:
        src = _ea()
        stamp = src.index('FileGetInteger("mt4_risk_bot.req", FILE_MODIFY_DATE, true)')
        rename = src.index('FileMove("mt4_risk_bot.req", FILE_COMMON, gClaimPath')
        assert stamp < rename, (
            "the stamp is read after the claim rename; a rename that refreshed "
            "the modification time would make every request look new and the "
            "fence would pass everything while appearing to work"
        )

    def test_the_older_of_the_two_stamps_wins(self) -> None:
        src = _ea()
        assert "if(claimStamp > 0 && (reqStamp <= 0 || claimStamp < reqStamp))" in src, (
            "the second stamp no longer takes the older value, so a file swapped "
            "between the two reads could make an old request look young"
        )


class TestTheFenceRefusesAndSaysSo:
    def test_the_fence_runs_before_the_handler(self) -> None:
        src = _ea()
        fence = src.index("if(RequestExpired(body, reqStamp, fenceAge, fenceTtl))")
        handle = src.index("reply = Handle(body);")
        assert fence < handle

    def test_an_absent_ttl_is_not_a_fence_of_zero(self) -> None:
        body = _function_body("bool RequestExpired(")
        assert "if(ttlOut <= 0)\n      return false;" in body, (
            "a request with no ttl_ms is treated as already expired, so an older "
            "desk would have every request refused"
        )

    def test_an_unmeasurable_age_refuses_a_send_and_allows_a_read(self) -> None:
        body = _function_body("bool RequestExpired(")
        assert 'if(ageOut < 0)\n      return IsSendOp(KV(body, "op"));' in body, (
            "an unmeasurable age no longer partitions sends from reads; refusing "
            "reads would take the desk down over a permissions problem and "
            "allowing sends would remove the fence in the one case it matters"
        )

    def test_the_send_op_set_agrees_with_the_desks(self) -> None:
        """One partition, two languages. A disagreement here is a silent hole.

        The Expert cannot import `constants.MAILBOX_SEND_OPS`, so this is the only
        place the two lists are ever compared.
        """
        body = _function_body("bool IsSendOp(string op)")
        declared = set(re.findall(r'op == "([a-z_]+)"', body))
        assert declared == set(MAILBOX_SEND_OPS), (
            f"the Expert calls {sorted(declared)} sends and the desk calls "
            f"{sorted(MAILBOX_SEND_OPS)} sends"
        )

    def test_the_grace_absorbs_the_one_second_timestamp_resolution(self) -> None:
        body = _function_body("bool RequestExpired(")
        assert "int ttlSec = (ttlOut + 999) / 1000 + FenceGraceSec;" in body, (
            "the ttl is not rounded up and graced, so a fresh request read a "
            "moment after it was written can compute an age one second too high "
            "and be refused"
        )
        grace = int(re.search(r"^input int FenceGraceSec = (\d+);", _ea(), re.M).group(1))
        assert grace >= 1

    def test_the_refusal_is_printed_with_the_age_and_the_ttl(self) -> None:
        fence = _process_fence()
        assert "REFUSED a stale request" in fence
        assert '" age=", fenceAge' in fence
        assert '"s ttl=", fenceTtl' in fence
        assert "Nothing was sent." in fence

    def test_the_refusal_states_that_nothing_survived_and_means_it(self) -> None:
        """`survivor_ticket=0` is a real measurement here, not a default.

        The refusal happens before `OrderSend` is reached at all, so the Expert
        genuinely knows the book is untouched. That is the one place in this file
        where a zero is an answer rather than an absence.
        """
        fence = _process_fence()
        assert 'Fail(KV(body, "id"), 4109, "request_expired")' in fence
        assert 'survivor_ticket=0' in fence
        assert "Handle(body)" not in fence, (
            "the refusal path still reaches the handler"
        )
