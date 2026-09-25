from pathlib import Path

from straightedge.config import BotConfig
from straightedge.risk import RiskManager
from wincompat import assert_owner_mode


def test_write_halt_file_is_0600(tmp_path: Path) -> None:
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.risk.halt_file = str(tmp_path / "HALT")
    path = RiskManager(cfg, halt_dir=tmp_path).write_halt_file("operator")
    assert_owner_mode(path)
