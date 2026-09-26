"""The desk reads the Expert's worst case off the LIVE Expert, and judges it.

`constants.py` derives the send budget from the Expert's source as it stands in
this repo. That is a DOCUMENT, not a mechanism: the Expert's retry ladders reach it
as `input` parameters, so an operator can change them in the terminal's dialog on a
box nobody is watching, and the attached Expert can differ from the shipped one
with nothing saying so.

So the Expert declares `ladder_ms`, `broker_calls` and `fence` on every ping reply
and the desk checks them. Both directions are driven here, because a check that has
only ever been seen to pass is not a check:

* a budget that clears the declaration reports `ok`;
* a budget that does not reports `TOO SHORT` and warns at connect time, and
  `doctor --connect` exits non-zero on it;
* an Expert that does not declare at all reports `NOT MEASURED`, never `ok`. An
  Expert too old to answer renders identically to one that answered well unless
  that partition is explicit, which is the same rule `_survivor_ticket` follows.
"""

from __future__ import annotations

from typing import Any

import pytest

from straightedge.broker.mt4_live import Mt4Broker
from straightedge.constants import (
    EA_CLAIM_RETRY_MS,
    EA_LADDER_SLEEP_MS,
    MAILBOX_ROUND_TRIP_CEILING_MS,
)

FLOOR = MAILBOX_ROUND_TRIP_CEILING_MS + EA_LADDER_SLEEP_MS + EA_CLAIM_RETRY_MS


def _broker(reply: dict[str, Any], *, send_sec: float) -> tuple[Mt4Broker, list[str]]:
    logged: list[str] = []

    def call(op: str, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        assert op == "ping"
        return {"ok": 1, **reply}

    return (
        Mt4Broker(call, send_timeout_sec=send_sec, log=logged.append),
        logged,
    )


def _declaring(**over: Any) -> dict[str, Any]:
    base = {"ladder_ms": EA_LADDER_SLEEP_MS, "broker_calls": 19, "fence": 1}
    base.update(over)
    return base


class TestABudgetThatClears:
    def test_it_reports_ok_and_warns_about_nothing(self) -> None:
        broker, logged = _broker(_declaring(), send_sec=(FLOOR + 1000) / 1000.0)
        broker.connect()
        assert broker.ea_ladder_ms == EA_LADDER_SLEEP_MS
        assert broker.ea_broker_calls == 19
        assert broker.ea_fence is True
        assert "send fence: ok" in broker.send_fence_report()
        assert logged == [], logged

    def test_it_reports_the_stale_request_refusal_the_expert_claims(self) -> None:
        broker, _ = _broker(_declaring(fence=0), send_sec=(FLOOR + 1000) / 1000.0)
        broker.connect()
        assert broker.ea_fence is False
        assert "stale-request refusal no" in broker.send_fence_report()


class TestABudgetThatDoesNot:
    def test_it_warns_loudly_at_connect(self) -> None:
        broker, logged = _broker(_declaring(), send_sec=(FLOOR - 500) / 1000.0)
        broker.connect()
        assert len(logged) == 1
        assert "WARNING send budget" in logged[0]
        assert "does NOT clear" in logged[0]

    def test_it_reports_too_short_so_doctor_can_go_red(self) -> None:
        broker, _ = _broker(_declaring(), send_sec=(FLOOR - 500) / 1000.0)
        broker.connect()
        assert "TOO SHORT" in broker.send_fence_report()

    def test_a_budget_exactly_on_the_floor_is_too_short(self) -> None:
        """Strictly longer, not "at least". Equal means the desk gives up in the
        same millisecond the Expert could still answer."""
        broker, _ = _broker(_declaring(), send_sec=FLOOR / 1000.0)
        broker.connect()
        assert "TOO SHORT" in broker.send_fence_report()

    def test_a_longer_ladder_than_the_shipped_one_moves_the_verdict(self) -> None:
        """The whole point: the verdict follows the ATTACHED Expert.

        An operator who raised the retry inputs in the terminal's dialog gets a red
        verdict against a budget that was correct for the shipped Expert.
        """
        shipped_ok = (FLOOR + 1000) / 1000.0
        broker, logged = _broker(
            _declaring(ladder_ms=EA_LADDER_SLEEP_MS * 4), send_sec=shipped_ok
        )
        broker.connect()
        assert "TOO SHORT" in broker.send_fence_report()
        assert len(logged) == 1
        assert broker.declaration_matches_shipped() is False


class TestAnExpertThatDoesNotDeclare:
    def test_it_is_not_measured_rather_than_ok(self) -> None:
        broker, logged = _broker({"time": 1}, send_sec=(FLOOR + 1000) / 1000.0)
        broker.connect()
        assert broker.ea_ladder_ms is None
        assert broker.ea_broker_calls is None
        assert broker.ea_fence is None
        report = broker.send_fence_report()
        assert "NOT MEASURED" in report
        assert "ok" not in report.split("(")[0]
        assert len(logged) == 1
        assert "CANNOT be checked" in logged[0]

    def test_it_does_not_claim_the_shipped_expert_either_way(self) -> None:
        broker, _ = _broker({"time": 1}, send_sec=(FLOOR + 1000) / 1000.0)
        broker.connect()
        assert broker.declaration_matches_shipped() is None

    def test_a_non_numeric_declaration_reads_as_absent(self) -> None:
        """Garbage is UNMEASURED, not zero. Zero would clear every budget."""
        broker, _ = _broker(_declaring(ladder_ms="soon"), send_sec=1.0)
        broker.connect()
        assert broker.ea_ladder_ms is None
        assert "NOT MEASURED" in broker.send_fence_report()


class TestADeskThatDidNotStateItsBudget:
    def test_it_records_the_declaration_and_judges_nothing(self) -> None:
        """`send_timeout_sec` defaults to 0, which means "the caller did not say".

        A hand-built `Mt4Broker` in a test must not start emitting warnings about a
        budget it never declared, and must not report a verdict it cannot reach.
        """
        broker, logged = _broker(_declaring(), send_sec=0.0)
        broker.connect()
        assert broker.ea_ladder_ms == EA_LADDER_SLEEP_MS
        assert logged == []
        assert "TOO SHORT" in broker.send_fence_report()

    def test_a_failed_ping_never_reaches_the_declaration(self) -> None:
        def call(op: str, payload: dict[str, Any]) -> dict[str, Any]:
            del op, payload
            return {"ok": 0, "error": "no_terminal", "ladder_ms": 10}

        broker = Mt4Broker(call, send_timeout_sec=10.0)
        with pytest.raises(RuntimeError, match="no_terminal"):
            broker.connect()
        assert broker.ea_ladder_ms is None, (
            "a refusing Expert's declaration was recorded, so a dead terminal "
            "could report a healthy send fence"
        )
