"""Proof that the duplicate-order guards are the things doing the work.

Every test in `tests/test_send_idempotency.py` passes. That is not evidence on its
own: the FIRST DRAFT of that file passed against the UNPATCHED code, because the
fake broker filled the order and the `already_in_symbol` exposure rule refused the
second send by coincidence. A guard that has never been seen to fail is not a
guard, so this file removes the guards and asserts the duplicate comes back.

There are two, and they are independent:

* `Engine._unresolved` -- the control for EVERY caller, including the auto leg and
  anything added later. This is the one that must never be removed.
* `Desk._already_attempted` -- the control on the `/confirm` path, which also owns
  the WORDING, because the engine's refusal arrives as an `OrderResult` that is not
  ok and `_confirm` would otherwise report "send failed": telling the operator the
  venue rejected the order when nothing was transmitted at all.

Writing this file is what established that the desk check is load-bearing rather
than cosmetic: removing only the engine guard still produced one send, which the
comment in `desk.py` originally denied. Each is asserted sufficient on its own
below, and the defect is asserted to return only when both are gone.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from straightedge.desk import Desk
from straightedge.engine import Engine
from straightedge.models import OrderResult
from straightedge.telegram import TgCommand
from test_send_idempotency import InFlightBroker, _engine, _sent


def _drop_engine_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_guard(self: Engine, client_id: str, symbol: str) -> OrderResult | None:
        del self, client_id, symbol
        return None

    monkeypatch.setattr(Engine, "_unresolved", no_guard)


def _drop_desk_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Desk, "_already_attempted", lambda self, key: False)


@pytest.fixture
def both_removed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _drop_engine_guard(monkeypatch)
    _drop_desk_guard(monkeypatch)
    yield


def _two_confirms(tmp_path) -> tuple[InFlightBroker, str]:
    broker = InFlightBroker(balance=10_000)
    engine = _engine(tmp_path, broker)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    second = engine.handle_command(TgCommand("1", 1, "/confirm", 3))
    engine.stop()
    return broker, second


def test_with_both_guards_removed_the_same_order_goes_out_twice(
    tmp_path, both_removed: None
) -> None:
    """The defect, reproduced on demand. This is what `/confirm` twice used to do.

    Measured on `main` at f22f228 before the fix, with this same fake: two
    identical 0.55 lot EURUSD market orders, the second reporting success, the
    first reply the bare string `mt4 bridge timeout`.
    """
    broker, second = _two_confirms(tmp_path)
    assert len(broker.sends) == 2, "both guards were removed and the duplicate did NOT happen"
    assert _sent(broker) == [("EURUSD", "buy", 0.55), ("EURUSD", "buy", 0.55)]
    assert second.startswith("sent buy"), second


def test_the_engine_guard_alone_is_sufficient(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With only the engine guard, the duplicate is still refused.

    This is the one that matters most, because it is the only guard the auto leg
    and any future caller pass through.
    """
    _drop_desk_guard(monkeypatch)
    broker, second = _two_confirms(tmp_path)
    assert len(broker.sends) == 1, _sent(broker)
    assert "unresolved send" in second


def test_the_desk_guard_alone_is_sufficient(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _drop_engine_guard(monkeypatch)
    broker, second = _two_confirms(tmp_path)
    assert len(broker.sends) == 1, _sent(broker)
    assert "unresolved send" in second


def test_removing_the_guards_does_not_remove_the_ledger(
    tmp_path, both_removed: None
) -> None:
    """The record is written either way, so the journal still shows what happened.

    Worth pinning: if removing the guard also removed the durable trace, this file
    would be proving something about the fake rather than about the guard.
    """
    broker = InFlightBroker(balance=10_000)
    engine = _engine(tmp_path, broker)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    key = engine.desk.pending.client_id
    engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert key in engine.inflight.open_entries()
    engine.stop()
