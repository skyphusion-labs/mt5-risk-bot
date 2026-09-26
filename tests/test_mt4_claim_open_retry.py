"""The EA must not destroy a request because ONE FileOpen failed.

Measured on the live desk (Vultr `straightedge-desk`) on 2026-09-25: the MQL4
Experts log carried 84 copies of

    mt4riskbot claimed request unreadable path=mt4_risk_bot.req.claim.-532103033
    err=5004. Dropped, not replayed.

across 08:19:27Z to 19:08:58Z, and each one lands 4 to 6 seconds before a
`loop_error` / `reconnect` in `journal.jsonl`. `5004` is MQL4's
ERR_CANNOT_OPEN_FILE. So the desk-side symptom "mt4 bridge timeout" was never a
latency problem: the Expert claimed the request by rename, failed to open the
file it had just claimed, and dropped it, leaving the adapter to wait out a
budget for a reply that was never going to be written.

MQL4 does not execute in this suite, so these are SOURCE GUARDS over the
shipped Expert, the same kind of evidence as `tests/test_mt4_unmanaged.py`:
they go red if the .mq4 is mutated, and they are not a behavioural test of a
running terminal. The behavioural proof is a market-hours window on the real
box, which is called out in the PR rather than implied by a green suite here.

What must stay true, and why each guard exists:

* the open is RETRIED, because the failure is transient;
* the retry is BOUNDED, and bounded against the adapter's own budget, so it can
  never turn one slow request into a stalled mailbox;
* a give-up still DROPS and still LOGS, because a replayed request is a
  duplicate order and a silent degrade is worse than a loud one;
* a RECOVERY is logged, so the retry cannot hide the rate of the underlying
  fault it is papering over.
"""

from __future__ import annotations

import re
from pathlib import Path

EA_PATH = Path(__file__).resolve().parents[1] / "mt4" / "Experts" / "Mt4RiskBot.mq4"

#: The adapter budget the retry must stay well inside. `config.py` ships
#: `timeout_ms = 5000` and `broker/__init__.py` converts it to seconds once.
ADAPTER_BUDGET_MS = 5000

#: Measured steady-state round trip on the live rig, 2026-09-26 01:48Z, taken
#: from a FileSystemWatcher on the mailbox directory: .req renamed in to .res
#: renamed in, about 205ms. The retry must not exceed one round trip.
MEASURED_ROUND_TRIP_MS = 205


def _ea_source() -> str:
    return EA_PATH.read_text(encoding="utf-8")


def _input_int(name: str) -> int:
    m = re.search(r"^input\s+int\s+%s\s*=\s*(-?\d+)\s*;" % name, _ea_source(), re.M)
    assert m is not None, f"the Expert no longer declares an `input int {name}`"
    return int(m.group(1))


def _claim_open_block() -> str:
    """The text from the share-flag declaration to the end of the give-up branch."""
    src = _ea_source()
    start = src.index("int share = FILE_READ|FILE_TXT")
    end = src.index("Dropped, not replayed.", start)
    return src[start:end]


def test_the_claim_open_is_retried_rather_than_dropped_on_the_first_failure() -> None:
    block = _claim_open_block()
    assert "while(attempts < tries)" in block, (
        "the FileOpen of the claim is not inside a bounded retry loop; a single "
        "transient ERR_CANNOT_OPEN_FILE would destroy the request again"
    )
    assert block.count("FileOpen(gClaimPath") == 1, (
        "the claim is opened in more than one place; the retry must be a loop "
        "around ONE open, not a duplicated open with its own error handling"
    )


def test_the_retry_is_bounded_and_stays_inside_the_adapter_budget() -> None:
    tries = _input_int("ClaimOpenRetries")
    gap = _input_int("ClaimOpenRetryMs")
    assert tries >= 2, "a retry count below 2 is not a retry"
    # The loop sleeps between attempts, never after the last one.
    worst_ms = (tries - 1) * gap
    assert worst_ms <= MEASURED_ROUND_TRIP_MS, (
        f"the retry can add {worst_ms}ms, more than one measured round trip "
        f"({MEASURED_ROUND_TRIP_MS}ms); it would stall the mailbox behind it"
    )
    assert worst_ms * 10 <= ADAPTER_BUDGET_MS, (
        f"the retry can add {worst_ms}ms, over 10% of the adapter's "
        f"{ADAPTER_BUDGET_MS}ms budget; the Expert must give the adapter room "
        "to still receive the reply"
    )


def test_it_does_not_sleep_after_the_final_attempt() -> None:
    block = _claim_open_block()
    assert "if(attempts < tries)\n         Sleep(ClaimOpenRetryMs);" in block, (
        "the retry sleeps unconditionally, so the last attempt pays a wait for "
        "a retry that never happens"
    )


def test_a_misconfigured_retry_count_cannot_drop_every_request() -> None:
    block = _claim_open_block()
    assert "int tries = (ClaimOpenRetries < 1) ? 1 : ClaimOpenRetries;" in block, (
        "ClaimOpenRetries is used unguarded; setting it to 0 in the Expert's "
        "inputs dialog would skip the loop entirely and drop EVERY request "
        "while the log still blamed an unreadable claim"
    )


def test_giving_up_still_drops_the_request_and_never_replays_it() -> None:
    src = _ea_source()
    block = src[src.index("if(h == INVALID_HANDLE)") :]
    give_up = block[: block.index("if(attempts > 1)")]
    assert "Dropped, not replayed." in give_up
    for stmt in (
        "FileDelete(gClaimPath, FILE_COMMON);",
        "LockRelease(SE_MAILBOX_LOCK);",
        "gHoldsMailbox = false;",
        "gBusy = false;",
        "return;",
    ):
        assert stmt in give_up, (
            f"the give-up path no longer runs `{stmt}`; a claimed request that "
            "is not cleaned up wedges the mailbox or leaks the lock"
        )


def test_the_give_up_reports_the_attempt_count_and_the_real_error() -> None:
    src = _ea_source()
    assert 'err=", lastErr, " attempts=", attempts' in src, (
        "the drop no longer reports how many attempts were made, or reports "
        "GetLastError() after the Sleep rather than the captured error; "
        "without the count the log cannot distinguish a one-off transient "
        "from a hard failure"
    )


def test_a_recovered_claim_is_logged_so_the_retry_cannot_hide_the_fault() -> None:
    src = _ea_source()
    assert "claim open recovered" in src, (
        "a claim that opened only after retrying is not logged, so the retry "
        "silently absorbs the underlying fault and the rate becomes invisible"
    )
    assert "if(attempts > 1)" in src, (
        "the recovery log is not gated on having actually retried, so it would "
        "fire on every healthy request and drown the log"
    )


def test_the_error_is_reset_before_each_attempt() -> None:
    block = _claim_open_block()
    reset = block.index("ResetLastError();")
    open_call = block.index("FileOpen(gClaimPath")
    assert reset < open_call, (
        "GetLastError() is not cleared before the open, so a stale error from "
        "an earlier MQL4 call can be reported as the reason this one failed"
    )
