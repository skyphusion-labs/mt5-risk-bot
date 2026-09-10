from pathlib import Path

import pytest

from mt5_risk_bot.config import BotConfig, load_config


def test_example_config_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.example.toml")
    assert cfg.mode == "paper"
    assert cfg.risk.risk_pct == 0.005
    assert "EURUSD" in cfg.symbols
    assert cfg.strategy.timeframe_id == 16385
    assert cfg.strategy.auto is False
    assert cfg.strategy.trail is False
    assert cfg.telegram.enabled is False
    assert "open" in cfg.telegram.notify_events


def test_validate_rejects_non_positive_risk() -> None:
    cfg = BotConfig()
    cfg.risk.risk_pct = 0.0
    with pytest.raises(ValueError, match="risk_pct"):
        cfg.validate()
    cfg = BotConfig()
    cfg.risk.daily_loss_pct = 0.0
    with pytest.raises(ValueError, match="daily_loss_pct"):
        cfg.validate()
    cfg = BotConfig()
    cfg.risk.max_drawdown_pct = -0.1
    with pytest.raises(ValueError, match="max_drawdown_pct"):
        cfg.validate()


def test_validate_rejects_empty_symbols_and_bad_confirm() -> None:
    cfg = BotConfig()
    cfg.symbols = []
    with pytest.raises(ValueError, match="symbol"):
        cfg.validate()
    cfg = BotConfig()
    cfg.telegram.confirm_seconds = 0
    with pytest.raises(ValueError, match="confirm_seconds"):
        cfg.validate()


def test_load_config_rejects_zero_risk_pct(tmp_path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[risk]\nrisk_pct = 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="risk_pct"):
        load_config(path)
