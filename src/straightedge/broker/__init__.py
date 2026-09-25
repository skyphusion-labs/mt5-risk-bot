from __future__ import annotations

from straightedge.broker.base import Broker
from straightedge.config import BotConfig

__all__ = ["Broker", "broker_for"]


def broker_for(cfg: BotConfig) -> Broker:
    """Return the venue adapter for account.mode (paper, mt5, or mt4)."""
    if cfg.mode == "mt5":
        from straightedge.broker.mt5_live import Mt5Broker

        return Mt5Broker(
            login=cfg.mt5.login,
            password=cfg.mt5.password,
            server=cfg.mt5.server,
            path=cfg.mt5.terminal_path,
            timeout_ms=cfg.mt5.timeout_ms,
        )
    if cfg.mode == "mt4":
        from straightedge.broker.mt4_live import (
            DEFAULT_STARTUP_WAIT_SEC,
            FileBridge,
            Mt4Broker,
        )

        path = cfg.mt4.files_dir
        if not path:
            raise RuntimeError(
                "mt4.files_dir is empty. Set it to the MT4 Common Files folder "
                "and attach mt4/Experts/Mt4RiskBot.mq4 to a chart."
            )
        bridge = FileBridge(path, timeout_sec=max(1.0, cfg.mt4.timeout_ms / 1000.0))
        wait = cfg.mt4.startup_wait_sec
        return Mt4Broker(
            bridge.call,
            magic=cfg.risk.magic,
            startup_wait_sec=DEFAULT_STARTUP_WAIT_SEC if wait is None else wait,
        )
    from straightedge.broker.paper import PaperBroker

    return PaperBroker(balance=cfg.initial_balance)
