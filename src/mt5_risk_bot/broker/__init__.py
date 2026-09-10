from __future__ import annotations

from mt5_risk_bot.broker.base import Broker
from mt5_risk_bot.config import BotConfig

__all__ = ["Broker", "broker_for"]


def broker_for(cfg: BotConfig) -> Broker:
    """Return the venue adapter for account.mode (paper or mt5)."""
    if cfg.mode == "mt5":
        from mt5_risk_bot.broker.mt5_live import Mt5Broker

        return Mt5Broker(
            login=cfg.mt5.login,
            password=cfg.mt5.password,
            server=cfg.mt5.server,
            path=cfg.mt5.terminal_path,
            timeout_ms=cfg.mt5.timeout_ms,
        )
    from mt5_risk_bot.broker.paper import PaperBroker

    return PaperBroker(balance=cfg.initial_balance)
