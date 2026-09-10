from mt5_risk_bot.__main__ import main


def test_doctor() -> None:
    assert main(["doctor"]) == 0


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
