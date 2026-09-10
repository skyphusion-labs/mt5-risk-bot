from mt5_risk_bot.__main__ import main, paper_round_trip, telegram_ping
from mt5_risk_bot.config import BotConfig, TelegramConfig


class _FakeTg:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []

    def post_json(self, url: str, payload: dict, timeout: float = 10.0, headers=None) -> dict:
        del url, timeout, headers
        self.sent.append(str(payload.get("text") or ""))
        return {"ok": self.ok, "result": {"message_id": 1}}


def test_doctor(capsys, monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "telegram ping: skip" in out
    assert "paper round-trip /buy /confirm /close: ok" in out


def test_paper_round_trip_ok() -> None:
    assert paper_round_trip() == "ok"


def test_telegram_ping_skip_and_ok() -> None:
    cfg = BotConfig()
    assert telegram_ping(cfg) == "skip"
    cfg.telegram = TelegramConfig(token="t", chat_id="1")
    fake = _FakeTg()
    assert telegram_ping(cfg, transport=fake) == "ok"
    assert any("doctor ping" in t for t in fake.sent)
    fake.ok = False
    assert telegram_ping(cfg, transport=fake) == "fail"


def test_backtest_trend(tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    rc = main(
        [
            "backtest",
            "--bars",
            "400",
            "--market",
            "trend",
            "--no-session-filter",
            "--journal",
            str(journal),
        ]
    )
    assert rc == 0
    assert journal.exists()


def test_telegram_disabled() -> None:
    assert main(["telegram"]) == 2


def test_help() -> None:
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0


def test_run_requires_telegram() -> None:
    assert main(["run", "--mode", "paper"]) == 2


def test_run_synthetic(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--mode", "paper", "--synthetic"]) == 0


def test_backtest_csv_and_range(tmp_path) -> None:
    csv_path = tmp_path / "x.csv"
    csv_path.write_text("time,open,high,low,close\n1000,1.1,1.11,1.09,1.105\n")
    rc = main(
        [
            "backtest",
            "--csv",
            str(csv_path),
            "--symbol",
            "EURUSD",
            "--no-session-filter",
            "--journal",
            str(tmp_path / "j.jsonl"),
        ]
    )
    assert rc == 0
    rc = main(
        [
            "backtest",
            "--market",
            "range",
            "--bars",
            "250",
            "--no-session-filter",
            "--journal",
            str(tmp_path / "r.jsonl"),
        ]
    )
    assert rc == 0
