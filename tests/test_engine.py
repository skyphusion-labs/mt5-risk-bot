from pathlib import Path

from mt5_risk_bot.broker.paper import PaperBroker, default_spec
from mt5_risk_bot.config import BotConfig, SessionConfig
from mt5_risk_bot.engine import Engine, run_backtest
from mt5_risk_bot.models import Side
from mt5_risk_bot.strategy import TrendStrategy
from mt5_risk_bot.synthetic import generate_bars, generate_ranging


def _cfg(**kw) -> BotConfig:
    cfg = BotConfig()
    cfg.session = SessionConfig(enabled=False)
    cfg.symbols = kw.get("symbols", ["EURUSD"])
    cfg.initial_balance = kw.get("balance", 10_000.0)
    cfg.journal_path = kw.get("journal", "journal.jsonl")
    return cfg


def test_signal_reprice_keeps_distance() -> None:
    from mt5_risk_bot.models import Signal, SignalKind

    spec = default_spec("EURUSD")
    sig = Signal(SignalKind.BUY, "EURUSD", 1.10000, 1.09500, 1.11250, 0.003)
    moved = sig.reprice(1.10100, spec)
    assert abs((moved.entry - moved.sl) - (sig.entry - sig.sl)) < spec.point
    assert abs((moved.tp - moved.entry) - (sig.tp - sig.entry)) < spec.point


def test_strategy_buys_uptrend() -> None:
    bars = generate_bars(250, drift=0.0006, vol=0.0002, seed=7)
    sig = TrendStrategy(BotConfig().strategy).signal("EURUSD", bars, default_spec("EURUSD"))
    assert sig.side is Side.BUY
    assert sig.sl < sig.entry < sig.tp
    assert sig.rr >= 1.5


def test_strategy_flat_on_dead_market() -> None:
    from mt5_risk_bot.models import Bar, SignalKind

    bars = [
        Bar(time=i * 3600, open=1.1, high=1.10005, low=1.09995, close=1.1) for i in range(250)
    ]
    sig = TrendStrategy(BotConfig().strategy).signal("EURUSD", bars, default_spec("EURUSD"))
    assert sig.kind is SignalKind.FLAT


def test_paper_roundtrip_stop() -> None:
    broker = PaperBroker(balance=10_000)
    bars = generate_bars(10, start=1.10, drift=0.0, vol=0.0001, seed=1)
    broker.seed_bars("EURUSD", bars)
    spec = default_spec("EURUSD")
    tick = broker.tick("EURUSD")
    from mt5_risk_bot.constants import (
        ORDER_TYPE_BUY,
        TRADE_ACTION_DEAL,
        TRADE_RETCODE_DONE,
    )

    sl = spec.normalize_price(tick.ask - 0.005)
    tp = spec.normalize_price(tick.ask + 0.010)
    res = broker.order_send(
        {
            "action": TRADE_ACTION_DEAL,
            "symbol": "EURUSD",
            "volume": 0.10,
            "type": ORDER_TYPE_BUY,
            "price": tick.ask,
            "sl": sl,
            "tp": tp,
            "magic": 1,
        }
    )
    assert res.retcode == TRADE_RETCODE_DONE
    assert broker.positions()
    # Drive price through the stop.
    from mt5_risk_bot.models import Bar

    last = bars[-1]
    crash = Bar(
        time=last.time + 3600,
        open=last.close,
        high=last.close,
        low=sl - 0.001,
        close=sl - 0.0005,
    )
    closed = broker.on_bar("EURUSD", crash)
    assert closed
    assert not broker.positions()
    assert broker.account().balance < 10_000


def test_backtest_trending_not_ruin(tmp_path: Path) -> None:
    cfg = _cfg(journal=str(tmp_path / "j.jsonl"))
    series = {"EURUSD": generate_bars(1200, drift=0.00035, vol=0.0004, seed=11)}
    result = run_backtest(cfg, series, journal_path=cfg.journal_path)
    assert result["equity"] > cfg.initial_balance * 0.90  # did not blow up
    assert result["open_positions"] <= 1


def test_backtest_trending_positive(tmp_path: Path) -> None:
    cfg = _cfg(journal=str(tmp_path / "j.jsonl"))
    series = {"EURUSD": generate_bars(2000, drift=0.0005, vol=0.00025, seed=21)}
    result = run_backtest(cfg, series, journal_path=cfg.journal_path)
    assert result["equity"] > cfg.initial_balance


def test_backtest_range_survives(tmp_path: Path) -> None:
    cfg = _cfg(journal=str(tmp_path / "j.jsonl"))
    series = {"EURUSD": generate_ranging(1500, seed=12)}
    result = run_backtest(cfg, series, journal_path=cfg.journal_path)
    # Whipsaw is allowed; ruin is not.
    assert result["equity"] > cfg.initial_balance * 0.85


def test_engine_respects_halt_file(tmp_path: Path) -> None:
    cfg = _cfg(journal=str(tmp_path / "j.jsonl"))
    cfg.risk.halt_file = str(tmp_path / "HALT")
    broker = PaperBroker(balance=10_000)
    bars = generate_bars(100, drift=0.0005, seed=1)
    broker.seed_bars("EURUSD", bars)
    engine = Engine(cfg, broker, halt_dir=tmp_path)
    engine.start()
    (tmp_path / "HALT").write_text("x")
    engine.step_all()
    assert engine.halted
    engine.stop()


def test_unknown_and_positions_commands(tmp_path: Path) -> None:
    from mt5_risk_bot.telegram import TgCommand

    cfg = _cfg(journal=str(tmp_path / "j.jsonl"))
    engine = Engine(cfg, PaperBroker(balance=10_000), halt_dir=str(tmp_path))
    engine.start()
    assert "unknown" in engine.handle_command(TgCommand("1", 1, "/nope", 1))
    assert "no open" in engine.handle_command(TgCommand("1", 1, "/positions", 2))
    assert "status" in engine.handle_command(TgCommand("1", 1, "/help", 3))
    engine.stop()


def test_filling_choice() -> None:
    from mt5_risk_bot.constants import (
        ORDER_FILLING_FOK,
        ORDER_FILLING_IOC,
        ORDER_FILLING_RETURN,
        choose_filling,
    )

    assert choose_filling(1) == ORDER_FILLING_FOK
    assert choose_filling(2) == ORDER_FILLING_IOC
    assert choose_filling(3) == ORDER_FILLING_FOK
    assert choose_filling(0) == ORDER_FILLING_RETURN
