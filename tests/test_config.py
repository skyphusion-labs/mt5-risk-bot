from pathlib import Path

import pytest

from straightedge.config import AdviceConfig, BotConfig, load_config


# --- model pin is current generation (#15 item 5) ---------------------------
#
# Denominator: 3 places in src/ + config.example.toml name a Claude model.
# One is stale. grok_model ("grok-4") and computer_model ("xai/grok-4.6")
# were checked too and are current; not touched.


def test_advice_config_default_claude_pin_is_current() -> None:
    assert AdviceConfig().claude_model == "claude-sonnet-5"


def test_load_config_default_claude_pin_is_current(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[telegram]\ntoken = \"t\"\nchat_id = \"1\"\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.advice.claude_model == "claude-sonnet-5"


def test_example_config_claude_pin_is_current() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.example.toml")
    assert cfg.advice.claude_model == "claude-sonnet-5"


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


def test_relative_journal_and_halt_paths_anchor_to_config_dir(tmp_path: Path, monkeypatch) -> None:
    """A relative journal_path or halt_file must resolve against the config
    file's own directory, not the process working directory (fc34).

    Under Windows Task Scheduler the working directory is not the repo, so
    an operator relying on `journal_path`/`halt_file` staying near the
    config would find the journal, the lock, and the emergency HALT file
    all landing somewhere else -- silently. A HALT file created there does
    nothing: the running desk never looks in the working directory, it
    looks next to its own config.
    """
    cfg_dir = tmp_path / "cfgdir"
    cfg_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    path = cfg_dir / "c.toml"
    path.write_text(
        '[engine]\njournal_path = "journal.jsonl"\n'
        '[risk]\nhalt_file = "HALT"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(elsewhere)
    cfg = load_config(path)
    assert Path(cfg.journal_path) == cfg_dir / "journal.jsonl", (
        f"journal_path resolved to {cfg.journal_path!r}, "
        f"not next to the config at {cfg_dir}"
    )
    assert Path(cfg.risk.halt_file) == cfg_dir / "HALT", (
        f"halt_file resolved to {cfg.risk.halt_file!r}, "
        f"not next to the config at {cfg_dir}"
    )


def test_absolute_journal_and_halt_paths_pass_through(tmp_path: Path) -> None:
    """An operator who already pins an absolute path keeps exactly that path."""
    cfg_dir = tmp_path / "cfgdir"
    cfg_dir.mkdir()
    journal_abs = tmp_path / "state" / "journal.jsonl"
    halt_abs = tmp_path / "state" / "HALT"
    path = cfg_dir / "c.toml"
    path.write_text(
        f'[engine]\njournal_path = "{journal_abs.as_posix()}"\n'
        f'[risk]\nhalt_file = "{halt_abs.as_posix()}"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert Path(cfg.journal_path) == journal_abs
    assert Path(cfg.risk.halt_file) == halt_abs


def test_relative_paths_with_no_config_file_anchor_to_cwd(tmp_path: Path, monkeypatch) -> None:
    """No --config at all (e.g. bare `doctor`): the documented base is CWD."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ACCOUNT_MODE", raising=False)
    cfg = load_config()
    assert Path(cfg.journal_path) == tmp_path / "journal.jsonl"
    assert Path(cfg.risk.halt_file) == tmp_path / "HALT"
