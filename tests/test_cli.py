from pathlib import Path

from mt5_risk_bot.__main__ import main, paper_round_trip, run_loop, telegram_ping
from mt5_risk_bot.config import BotConfig, TelegramConfig
from mt5_risk_bot.journal import Journal
from mt5_risk_bot.telegram import TelegramClient, offset_path_for


class _FakeTg:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []
        self.urls: list[str] = []

    def post_json(self, url: str, payload: dict, timeout: float = 10.0, headers=None) -> dict:
        del timeout, headers
        self.urls.append(url)
        self.sent.append(str(payload.get("text") or ""))
        return {"ok": self.ok, "result": {"message_id": 1}}


class _FakeConnectBroker:
    def __init__(self, **kwargs) -> None:
        del kwargs
        self.calls: list[str] = []

    def ensure_connected(self) -> None:
        self.calls.append("ensure_connected")

    def connect(self) -> None:
        self.calls.append("connect")

    def disconnect(self) -> None:
        self.calls.append("disconnect")

    def account(self):
        self.calls.append("account")
        return type(
            "Acct",
            (),
            {
                "login": 1,
                "server": "Demo",
                "equity": 10000.0,
                "currency": "USD",
                "trade_mode": 0,
            },
        )()


def test_doctor(capsys, monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "telegram ping: skip" in out
    assert "paper round-trip /buy /confirm /close: ok" in out


def test_doctor_connect_calls_ensure_connected(capsys, monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    created: list[_FakeConnectBroker] = []

    def factory(**kwargs):
        broker = _FakeConnectBroker(**kwargs)
        created.append(broker)
        return broker

    monkeypatch.setattr("mt5_risk_bot.broker.mt5_live.load_mt5_module", lambda: object())
    monkeypatch.setattr("mt5_risk_bot.broker.mt5_live.Mt5Broker", factory)
    assert main(["doctor", "--connect"]) == 0
    out = capsys.readouterr().out
    assert len(created) == 1
    assert created[0].calls[0] == "ensure_connected"
    assert "connect" not in created[0].calls
    assert created[0].calls.index("ensure_connected") < created[0].calls.index("account")
    assert "disconnect" in created[0].calls
    assert "connected login=1" in out
    assert "trade_mode=0" in out


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


def test_telegram_ping_preserves_update_offset(tmp_path, monkeypatch) -> None:
    seen: dict[str, str | None] = {}
    orig = TelegramClient.from_config

    def wrapped(cfg, transport=None, *, offset_path=None):
        seen["offset_path"] = offset_path
        return orig(cfg, transport=transport, offset_path=offset_path)

    monkeypatch.setattr("mt5_risk_bot.__main__.TelegramClient.from_config", wrapped)
    journal = tmp_path / "desk.jsonl"
    path = offset_path_for(journal)
    Path(path).write_text("99", encoding="utf-8")
    cfg = BotConfig()
    cfg.journal_path = str(journal)
    cfg.telegram = TelegramConfig(token="t", chat_id="1")
    fake = _FakeTg()
    assert telegram_ping(cfg, transport=fake) == "ok"
    assert seen["offset_path"] == path
    assert Path(path).read_text(encoding="utf-8") == "99"
    assert any("doctor ping" in t for t in fake.sent)
    assert not any(u.endswith("/getUpdates") for u in fake.urls)


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


def test_run_invalid_risk_pct(tmp_path, capsys) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[risk]\nrisk_pct = 0\n", encoding="utf-8")
    rc = main(["--config", str(path), "run", "--mode", "paper"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "risk_pct" in err


class _BoomEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.halted = False
        self.journal = self

    def write(self, event: str, **fields) -> None:
        del event, fields

    def step_all(self) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        if self.calls >= 3:
            self.halted = True


def test_run_loop_continues_after_step_all_error(capsys) -> None:
    engine = _BoomEngine()
    run_loop(engine, loop=True, keep_on_halt=False)
    assert engine.calls >= 3
    err = capsys.readouterr().err
    assert "boom" in err


def test_run_loop_keyboardinterrupt_stops() -> None:
    class _Kbd:
        halted = False
        journal = type("J", (), {"write": staticmethod(lambda *a, **k: None)})()

        def step_all(self) -> None:
            raise KeyboardInterrupt

    try:
        run_loop(_Kbd(), loop=True)
    except KeyboardInterrupt:
        return
    raise AssertionError("KeyboardInterrupt must stop the loop")


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


def test_run_loop_survives_step_error(tmp_path, capsys) -> None:
    class Boom:
        def __init__(self) -> None:
            self.n = 0
            self.halted = False
            self.journal = Journal(tmp_path / "j.jsonl")

        def step_all(self) -> None:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("broker hiccup")
            self.halted = True

    eng = Boom()
    run_loop(eng, loop=True, keep_on_halt=False)
    assert eng.n == 2
    err = capsys.readouterr().err
    assert "broker hiccup" in err
    events = [rec.get("event") for rec in eng.journal.tail(10)]
    assert "loop_error" in events


def test_run_loop_stderr_redacts_botfather_token(capsys) -> None:
    secret = "1234567890:AA" + "x" * 35

    class Boom:
        def __init__(self) -> None:
            self.n = 0
            self.halted = False
            self.journal = type("J", (), {"write": staticmethod(lambda *a, **k: None)})()

        def step_all(self) -> None:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("token 1234567890:AA" + "x" * 35)
            self.halted = True

    run_loop(Boom(), loop=True, keep_on_halt=False)
    err = capsys.readouterr().err
    assert "loop error" in err
    assert secret not in err
    assert "[REDACTED]" in err
