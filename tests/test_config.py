from pathlib import Path

from mt5_risk_bot.config import load_config


def test_example_config_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.example.toml")
    assert cfg.mode == "paper"
    assert cfg.risk.risk_pct == 0.005
    assert "EURUSD" in cfg.symbols
    assert cfg.strategy.timeframe_id == 16385
    assert cfg.telegram.enabled is False
    assert "open" in cfg.telegram.notify_events
