"""Reads and sends get different budgets, and the send budget outlasts the EA.

One `mt4.timeout_ms` covered both. A read timing out is cheap; a send timing out
is the ambiguous-money case, and the desk giving up while the Expert is still
inside its own retry ladders is exactly how that case is reached. Six of the 88
desk timeout events on 2026-09-25 had no Expert-side drop behind them, which is
the shape of this.

Every number here is DERIVED from the Expert's own source or from the live
measurements, never chosen. `constants.py` owns the terms; this file recomputes
them independently from the .mq4, and cross-checks that the Expert's retry loops
are really driven by the constants it declares to the desk. A literal in a loop
plus a number in a ping reply is two copies that drift in silence, and the drift
is invisible until the desk gives up on a live order.

MQL4 does not execute in this suite, so the Expert half of this is SOURCE GUARDS
over the shipped artifact, the same kind of evidence as
`tests/test_mt4_claim_open_retry.py`: they go red if the .mq4 is mutated, and they
are not a behavioural test of a running terminal. The behavioural proof is a
market-hours window on the real box.
"""

from __future__ import annotations

import re
from pathlib import Path

from live_measurements import BRIDGE_ROUND_TRIP_P50_MS
from straightedge.config import BotConfig, Mt4Config
from straightedge.constants import (
    BROKER_CALL_ALLOWANCE_MS,
    EA_BROKER_CALLS_WORST_CASE,
    EA_CLAIM_RETRY_MS,
    EA_LADDER_SLEEP_MS,
    MAILBOX_ROUND_TRIP_CEILING_MS,
    MAILBOX_SEND_OPS,
    derive_send_timeout_ms,
)

EA_PATH = Path(__file__).resolve().parents[1] / "mt4" / "Experts" / "Mt4RiskBot.mq4"

#: The number of bounded retry ladders in the Expert that sit between the desk's
#: request and the desk's reply, and therefore spend the desk's send budget: the
#: claim read (#82) and the reply write (#83). Both are on the same `tries` knob on
#: purpose. A THIRD would silently under-budget every send.
EA_RETRY_LADDERS_ON_THE_CLAIM_KNOB = 2


def _ea() -> str:
    return EA_PATH.read_text(encoding="utf-8")


def _define(name: str) -> int:
    m = re.search(r"^#define\s+%s\s+(\d+)\s*$" % name, _ea(), re.M)
    assert m is not None, f"the Expert no longer declares `#define {name}`"
    return int(m.group(1))


def _loop_uses(constant: str) -> int:
    """How many `for` loops in the Expert are bounded by this constant."""
    return len(re.findall(r"for\(int i=0; i<%s; i\+\+\)" % constant, _ea()))


class TestTheBudgetsAreSeparate:
    def test_config_carries_a_send_budget_of_its_own(self) -> None:
        cfg = BotConfig()
        assert cfg.mt4.send_timeout_ms > cfg.mt4.timeout_ms, (
            f"send budget {cfg.mt4.send_timeout_ms}ms is not longer than the read "
            f"budget {cfg.mt4.timeout_ms}ms; a send is the expensive one to lose"
        )

    def test_the_read_budget_is_still_the_measured_one(self) -> None:
        """The read budget is deliberately UNCHANGED by this split.

        It was never the defect, and two other numbers derive from it:
        `watchdog.venue_timeout_seconds` and `test_mt4_claim_open_retry`'s
        `ADAPTER_BUDGET_MS`. Moving it would move the alarm latency an operator
        was promised, for no measured reason.
        """
        assert BotConfig().mt4.timeout_ms == 5000

    def test_the_transport_ceiling_clears_the_one_trustworthy_measurement(self) -> None:
        """Cited from `live_measurements.py`, which is where a measured number lives.

        The median is the only statistic from that window that can be stood behind:
        the pairing used to compute latency shifts by one after every unanswered
        request and three went unanswered, so p90 and above are unreliable. The
        ceiling is therefore justified as a multiple of the median rather than as a
        tail, and this asserts that relationship instead of restating a number.
        """
        assert MAILBOX_ROUND_TRIP_CEILING_MS >= 4 * BRIDGE_ROUND_TRIP_P50_MS

    def test_a_dry_run_is_a_read_and_a_send_is_not(self) -> None:
        """`check_market` returns before `SendRetry`, so it cannot move money.

        Getting this partition wrong in the other direction is the expensive
        mistake, which is why it is one frozenset rather than a condition repeated
        at each call site.
        """
        assert "check_market" not in MAILBOX_SEND_OPS
        assert "check_working" not in MAILBOX_SEND_OPS
        assert "tick" not in MAILBOX_SEND_OPS
        assert {"market", "working", "close", "close_by"} <= MAILBOX_SEND_OPS

    def test_a_send_budget_at_or_below_the_read_budget_is_refused(self) -> None:
        cfg = BotConfig()
        cfg.mt4 = Mt4Config(files_dir="x", timeout_ms=5000, send_timeout_ms=5000)
        try:
            cfg.validate()
        except ValueError as exc:
            assert "send_timeout_ms" in str(exc)
        else:  # pragma: no cover - the assertion above is the point
            raise AssertionError("a send budget equal to the read budget was accepted")

    def test_a_send_budget_inside_the_experts_sleep_total_is_refused(self) -> None:
        """The floor gate has to be able to go RED, and with the shipped 5000ms
        read budget it never can: anything above 5000 already clears the Expert's
        2310ms of unconditional waiting. So the reachable misconfiguration is an
        operator who lowered BOTH -- a plausible move on a box they think is slow,
        and one where the ordering check alone would pass it.

        Driving this arm was worth the trouble: the first version of this test
        asserted against 6000ms, which the ordering check accepts and the floor
        check never sees, so it was a guard that could not fire.
        """
        cfg = BotConfig()
        cfg.mt4 = Mt4Config(files_dir="x", timeout_ms=1000, send_timeout_ms=2000)
        try:
            cfg.validate()
        except ValueError as exc:
            assert "Sleep()" in str(exc), exc
        else:  # pragma: no cover
            raise AssertionError("a send budget inside the Expert's ladder was accepted")

    def test_the_two_refusals_are_different_gates(self) -> None:
        """The ordering check and the floor check catch different mistakes, and a
        config can fail one while satisfying the other in both directions."""
        ordering = BotConfig()
        ordering.mt4 = Mt4Config(files_dir="x", timeout_ms=9000, send_timeout_ms=9000)
        floor = BotConfig()
        floor.mt4 = Mt4Config(files_dir="x", timeout_ms=100, send_timeout_ms=2000)
        messages = []
        for cfg in (ordering, floor):
            try:
                cfg.validate()
            except ValueError as exc:
                messages.append(str(exc))
        assert len(messages) == 2
        assert "greater than mt4.timeout_ms" in messages[0]
        assert "Sleep()" in messages[1]


class TestTheSendBudgetOutlastsTheExpert:
    def test_the_expert_declares_the_constants_its_loops_actually_use(self) -> None:
        """No literal loop bounds. The declaration and the behaviour are one thing."""
        assert _loop_uses("SE_SEND_TRIES") == 1
        assert _loop_uses("SE_MODIFY_TRIES") == 1
        # RollbackPosition and RollbackPending, one each: a market send and a
        # working send both roll back over the same bound.
        assert _loop_uses("SE_ROLLBACK_TRIES") == 2
        assert _ea().count("Sleep(SE_RETRY_SLEEP_MS);") == 4
        assert "Sleep(50);" not in _ea(), (
            "a retry ladder sleeps on a literal again, so the ping reply and the "
            "behaviour can now disagree"
        )

    def test_the_ladder_sleep_total_matches_the_shipped_expert(self) -> None:
        measured = (
            _define("SE_SEND_TRIES")
            + _define("SE_MODIFY_TRIES")
            + _define("SE_ROLLBACK_TRIES")
        ) * _define("SE_RETRY_SLEEP_MS")
        assert measured == EA_LADDER_SLEEP_MS, (
            f"the Expert's worst-case sleep total is now {measured}ms but "
            f"constants.EA_LADDER_SLEEP_MS still says {EA_LADDER_SLEEP_MS}ms"
        )

    def test_the_broker_call_count_matches_the_shipped_expert(self) -> None:
        counted = (
            _define("SE_SEND_TRIES")
            + _define("SE_MODIFY_TRIES")
            + _define("SE_ROLLBACK_TRIES")
        )
        assert counted == EA_BROKER_CALLS_WORST_CASE, (
            f"the Expert can now make {counted} broker round trips in one "
            f"Process(), not {EA_BROKER_CALLS_WORST_CASE}"
        )

    def test_the_claim_retry_term_counts_every_ladder_on_that_knob(self) -> None:
        """Two ladders, not one, and the count comes from the source.

        This is the term that moved while the change was in review: #83 added the
        reply-write retry on the same `tries` bound as #82's claim-read retry. Both
        spend the desk's send budget, so both are in the derivation. Counting the
        loops rather than trusting the number is what makes a third one go red.
        """
        src = _ea()
        tries = int(
            re.search(r"^input int ClaimOpenRetries = (\d+);", src, re.M).group(1)
        )
        gap = int(re.search(r"^input int ClaimOpenRetryMs = (\d+);", src, re.M).group(1))
        ladders = src.count("Sleep(ClaimOpenRetryMs);")
        assert ladders == EA_RETRY_LADDERS_ON_THE_CLAIM_KNOB, (
            f"the Expert now has {ladders} retry ladders on the ClaimOpenRetryMs "
            f"knob, not {EA_RETRY_LADDERS_ON_THE_CLAIM_KNOB}; the send budget "
            "derivation counts them and has not been updated"
        )
        assert ladders * (tries - 1) * gap == EA_CLAIM_RETRY_MS

    def test_the_shipped_send_budget_is_strictly_longer_than_the_ladder(self) -> None:
        cfg = BotConfig()
        floor = MAILBOX_ROUND_TRIP_CEILING_MS + EA_LADDER_SLEEP_MS + EA_CLAIM_RETRY_MS
        assert cfg.mt4.send_timeout_ms > floor, (
            f"send budget {cfg.mt4.send_timeout_ms}ms does not clear the "
            f"Expert's unconditional waiting ({floor}ms); the desk would give "
            "up while the Expert was still working"
        )

    def test_the_default_is_the_derivation_and_not_a_round_number(self) -> None:
        derived = derive_send_timeout_ms()
        assert BotConfig().mt4.send_timeout_ms == derived
        assert derived == (
            MAILBOX_ROUND_TRIP_CEILING_MS
            + EA_LADDER_SLEEP_MS
            + EA_CLAIM_RETRY_MS
            + EA_BROKER_CALLS_WORST_CASE * BROKER_CALL_ALLOWANCE_MS
        )
        assert derived % 1000 != 0, (
            "the send budget is a round number, which means it was chosen and "
            "then justified rather than derived from its terms"
        )


class TestTheExpertDeclaresItselfToTheDesk:
    def test_the_ping_reply_states_the_ladder_and_the_fence(self) -> None:
        """A number in a runbook cannot go red. This can.

        The ladder bounds reach the Expert as `input` parameters, so an operator
        can change them in the terminal's dialog on a box nobody is watching. The
        desk therefore reads them off the live Expert rather than trusting the copy
        in this repo.
        """
        src = _ea()
        ping = src[src.index('if(op == "ping")') :]
        ping = ping[: ping.index('if(op == "account")')]
        for field in ("ladder_ms=", "broker_calls=", "fence="):
            assert field in ping, f"the ping reply no longer declares {field}"
        assert "SE_LADDER_SLEEP_MS" in ping
        assert "SE_BROKER_CALLS" in ping

    def test_the_fence_flag_reports_the_measurement_and_not_a_hope(self) -> None:
        src = _ea()
        assert 'fence=" + (gFileTimeKnown ? "1" : "0")' in src, (
            "the fence flag is no longer wired to the calibration result, so an "
            "Expert that could not measure file ages would report that it can"
        )
