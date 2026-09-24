"""The desk vs risk mode-gate mismatch (#15 item 2).

Denominator, measured in src/ against this commit (line numbers drift;
re-derive rather than trust a citation): 9 places compare `cfg.mode`
against the venue set.

  wrong (1):    desk.py Desk._live_needs_flag -- `!= "mt5"` (excludes mt4)
  set form (3): risk.py RiskManager, two sites -- `in {"mt5", "mt4"}` (right)
                config.py BotConfig.validate -- `not in {"paper","mt5","mt4"}`
                (a different question: is this a legal value at all, not
                 "is this a live venue"; correct for its own purpose)
  single-venue dispatch (5), each intentionally `==` one specific mode:
                broker/__init__.py broker_for, x2 (mt5, mt4)
                __main__.py cmd_doctor, x2 (mt4-only printing/connect branch)
                __main__.py _cmd_run_locked (paper-only synthetic/seed branch)

risk.py is the correct side: it is what actually gates a send. desk.py's
`_live_needs_flag` only gates the WARNING that tells an operator to arm
live before `/approve always` -- so on an MT4 real account, the warning
never fires and `/approve always` silently arms with no live-arm
requirement. The send itself still gets refused downstream by risk.py's
correct set-form gate (`live_not_accepted`), so this was never a path to
an unarmed real-money send -- but the desk lied about the precondition
until that refusal, and CONTRACT.md/RUNBOOK.md both promise refusal to
arm, not refusal to send, the way MT5 already worked.
"""

from __future__ import annotations


class _Account:
    def __init__(self, trade_mode: int) -> None:
        self.trade_mode = trade_mode


class _Broker:
    def __init__(self, trade_mode: int) -> None:
        self._account = _Account(trade_mode)

    def account(self) -> _Account:
        return self._account


class _Journal:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    def write(self, event: str, **fields: object) -> None:
        self.records.append((event, fields))


class _Cfg:
    def __init__(self, mode: str, live_accepted: bool = False) -> None:
        self.mode = mode
        self.live_accepted = live_accepted


class _Engine:
    def __init__(self, mode: str, trade_mode: int, live_accepted: bool = False) -> None:
        self.cfg = _Cfg(mode, live_accepted)
        self.broker = _Broker(trade_mode)
        self.journal = _Journal()


def _desk(mode: str, trade_mode: int, live_accepted: bool = False):
    from mt5_risk_bot.desk import Desk

    return Desk(_Engine(mode, trade_mode, live_accepted))


# --- the bug, on the desk side ----------------------------------------------


def test_live_needs_flag_true_for_mt4_real_account() -> None:
    """The defect: this returned False before the fix, the same wrong
    answer as if the account were paper."""
    desk = _desk(mode="mt4", trade_mode=2)
    assert desk._live_needs_flag() is True


def test_approve_always_refused_on_mt4_real_account_without_live() -> None:
    desk = _desk(mode="mt4", trade_mode=2)
    reply = desk._approve("always")
    assert "I-ACCEPT-RISK" in reply
    assert desk.approve_always is False


# --- regression controls: existing correct behaviour must not move ---------


def test_live_needs_flag_true_for_mt5_real_account_unchanged() -> None:
    desk = _desk(mode="mt5", trade_mode=2)
    assert desk._live_needs_flag() is True


def test_live_needs_flag_false_for_paper_unchanged() -> None:
    desk = _desk(mode="paper", trade_mode=2)
    assert desk._live_needs_flag() is False


def test_live_needs_flag_false_once_live_accepted_on_mt4() -> None:
    desk = _desk(mode="mt4", trade_mode=2, live_accepted=True)
    assert desk._live_needs_flag() is False


def test_approve_always_succeeds_on_mt4_real_account_once_armed() -> None:
    desk = _desk(mode="mt4", trade_mode=2, live_accepted=True)
    reply = desk._approve("always")
    assert "approve always" in reply
    assert desk.approve_always is True


def test_live_needs_flag_false_for_mt4_demo_account() -> None:
    """trade_mode 0 (demo): never needed the flag on either venue."""
    desk = _desk(mode="mt4", trade_mode=0)
    assert desk._live_needs_flag() is False
