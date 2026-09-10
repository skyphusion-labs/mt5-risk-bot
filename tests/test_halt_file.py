from pathlib import Path

from mt5_risk_bot.config import BotConfig
from mt5_risk_bot.risk import RiskManager


def test_write_halt_file_is_0600(tmp_path: Path) -> None:
    cfg = BotConfig()
    cfg.risk.halt_file = str(tmp_path / "HALT")
    path = RiskManager(cfg, halt_dir=tmp_path).write_halt_file("operator")
    assert path.stat().st_mode & 0o777 == 0o600
