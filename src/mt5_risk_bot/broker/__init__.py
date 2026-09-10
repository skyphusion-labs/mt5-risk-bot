from __future__ import annotations

from mt5_risk_bot.broker.base import Broker
from mt5_risk_bot.config import BotConfig

__all__ = ["Broker", "broker_for"]


def broker_for(cfg: BotConfig) -> Broker:
    """Return the venue adapter for account.mode (paper, mt5, or mt4)."""
    if cfg.mode == "mt5":
        from mt5_risk_bot.broker.mt5_live import Mt5Broker

        return Mt5Broker(
            login=cfg.mt5.login,
            password=cfg.mt5.password,
            server=cfg.mt5.server,
            path=cfg.mt5.terminal_path,
            timeout_ms=cfg.mt5.timeout_ms,
        )
    if cfg.mode == "mt4":
        from mt5_risk_bot.broker.mt4_live import FileBridge, Mt4Broker

        path = cfg.mt4.files_dir
        if not path:
            raise RuntimeError(
                "mt4.files_dir is empty. Set it to the MT4 Common Files folder "
                "and attach mt4/Experts/Mt4RiskBot.mq4 to a chart."
            )
        bridge = FileBridge(path, timeout_sec=max(1.0, cfg.mt4.timeout_ms / 1000.0))
        return Mt4Broker(bridge.call, magic=cfg.risk.magic)
    from mt5_risk_bot.broker.paper import PaperBroker

    return PaperBroker(balance=cfg.initial_balance)
