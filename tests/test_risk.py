from datetime import datetime, timezone
from pathlib import Path

from mt5_risk_bot.broker.paper import default_spec
from mt5_risk_bot.config import BotConfig
from mt5_risk_bot.models import Account, Position, Side, Signal, SignalKind, Tick
from mt5_risk_bot.risk import RiskManager, currency_exposure, in_session, parse_fx


def _acct(equity: float = 10_000, **kw) -> Account:
    return Account(
        login=1,
        balance=kw.get("balance", equity),
        equity=equity,
        margin=kw.get("margin", 0.0),
        margin_free=kw.get("margin_free", equity),
        profit=kw.get("profit", 0.0),
        leverage=100,
        currency="USD",
        trade_allowed=kw.get("trade_allowed", True),
        trade_expert=True,
        trade_mode=kw.get("trade_mode", 0),
    )


def _sig(kind=SignalKind.BUY, symbol="EURUSD", entry=1.10, sl=1.095, tp=1.1125, atr=0.003) -> Signal:
    return Signal(kind, symbol, entry, sl, tp, atr, reason="test")


def _tick(bid=1.0999, ask=1.1001) -> Tick:
    return Tick(time=0, bid=bid, ask=ask)


def _now() -> datetime:
    return datetime(2024, 1, 3, 12, 0, tzinfo=timezone.utc)  # Wednesday noon UTC


def test_parse_fx() -> None:
    assert parse_fx("EURUSD") == ("EUR", "USD")
    assert parse_fx("USDJPYm") == ("USD", "JPY")


def test_currency_exposure_blocks_third_usd() -> None:
    positions = [
        Position(1, "EURUSD", Side.BUY, 0.1, 1.1, 1.09, 1.12, 1.1, 0, magic=1),
        Position(2, "GBPUSD", Side.BUY, 0.1, 1.2, 1.19, 1.22, 1.2, 0, magic=1),
    ]
    exp = currency_exposure(positions, extra=("AUDUSD", Side.BUY))
    assert exp["USD"] == -3


def test_session_london_ny() -> None:
    from mt5_risk_bot.config import SessionConfig

    s = SessionConfig()
    assert in_session(datetime(2024, 1, 3, 12, 0, tzinfo=timezone.utc), s)
    assert not in_session(datetime(2024, 1, 3, 5, 0, tzinfo=timezone.utc), s)
    assert not in_session(datetime(2024, 1, 5, 17, 0, tzinfo=timezone.utc), s)  # Friday 17:00


def test_halt_file(tmp_path: Path) -> None:
    (tmp_path / "HALT").write_text("stop\n")
    rm = RiskManager(BotConfig(), halt_dir=tmp_path)
    d = rm.evaluate(
        account=_acct(),
        signal=_sig(),
        spec=default_spec("EURUSD"),
        tick=_tick(),
        positions=[],
        now=_now(),
    )
    assert not d.allowed and d.halt and d.flatten


def test_daily_loss_halt() -> None:
    cfg = BotConfig()
    rm = RiskManager(cfg)
    rm.observe(_acct(10_000), _now())
    d = rm.evaluate(
        account=_acct(9_700),  # -3% vs 2% daily cap
        signal=_sig(),
        spec=default_spec("EURUSD"),
        tick=_tick(),
        positions=[],
        now=_now(),
    )
    assert d.halt and d.reason == "daily_loss"


def test_drawdown_halt() -> None:
    cfg = BotConfig()
    rm = RiskManager(cfg)
    day1 = _now()
    day2 = datetime(2024, 1, 4, 12, 0, tzinfo=timezone.utc)
    rm.observe(_acct(10_000), day1)
    rm.observe(_acct(9_000), day2)  # new UTC day: daily clock resets, peak does not
    d = rm.evaluate(
        account=_acct(8_900),  # 11% off peak, 1.1% on the day
        signal=_sig(),
        spec=default_spec("EURUSD"),
        tick=_tick(),
        positions=[],
        now=day2,
    )
    assert d.halt and d.reason == "max_drawdown"


def test_live_blocked_without_flag() -> None:
    cfg = BotConfig(mode="mt5", live_accepted=False)
    rm = RiskManager(cfg)
    d = rm.evaluate(
        account=_acct(trade_mode=2),
        signal=_sig(),
        spec=default_spec("EURUSD"),
        tick=_tick(),
        positions=[],
        now=_now(),
    )
    assert not d.allowed and d.reason == "live_not_accepted"


def test_live_blocked_without_flag_mt4() -> None:
    cfg = BotConfig(mode="mt4", live_accepted=False)
    rm = RiskManager(cfg)
    d = rm.evaluate(
        account=_acct(trade_mode=2),
        signal=_sig(),
        spec=default_spec("EURUSD"),
        tick=_tick(),
        positions=[],
        now=_now(),
    )
    assert not d.allowed and d.reason == "live_not_accepted"


def test_operator_halt_clears_file_not_drawdown(tmp_path: Path) -> None:
    cfg = BotConfig()
    cfg.risk.halt_file = str(tmp_path / "HALT")
    rm = RiskManager(cfg, halt_dir=tmp_path)
    day1 = _now()
    day2 = datetime(2024, 1, 4, 12, 0, tzinfo=timezone.utc)
    rm.observe(_acct(10_000), day1)
    rm.observe(_acct(9_000), day2)
    d = rm.evaluate(
        account=_acct(8_900),
        signal=_sig(),
        spec=default_spec("EURUSD"),
        tick=_tick(),
        positions=[],
        now=day2,
    )
    assert d.reason == "max_drawdown"
    leftover = rm.clear_operator_halt()
    assert leftover == "max_drawdown"
    rm.write_halt_file("telegram")
    assert rm.halt_path().exists()
    leftover = rm.clear_operator_halt()
    assert leftover == "max_drawdown"
    assert not rm.halt_path().exists()


def test_rr_and_size_ok() -> None:
    rm = RiskManager(BotConfig())
    d = rm.evaluate(
        account=_acct(),
        signal=_sig(),
        spec=default_spec("EURUSD"),
        tick=_tick(),
        positions=[],
        now=_now(),
    )
    assert d.allowed
    assert d.volume > 0
