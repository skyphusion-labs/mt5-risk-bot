from mt5_risk_bot.broker.paper import PaperBroker
from mt5_risk_bot.config import BotConfig, TelegramConfig
from mt5_risk_bot.engine import Engine
from mt5_risk_bot.telegram import TelegramClient, TgCommand, parse_command


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.updates: list[dict] = []
        self.fail = False

    def post_json(self, url: str, payload: dict, timeout: float = 10.0) -> dict:
        del timeout
        if self.fail:
            from mt5_risk_bot.telegram import TelegramError

            raise TelegramError("boom")
        self.sent.append((url, payload))
        if url.endswith("/getUpdates"):
            result = self.updates
            self.updates = []
            return {"ok": True, "result": result}
        return {"ok": True, "result": {"message_id": 1}}


def test_parse_command_strips_bot_suffix() -> None:
    cmd = parse_command(
        {
            "update_id": 9,
            "message": {
                "text": "/status@MyBot",
                "chat": {"id": 42},
                "from": {"id": 7},
            },
        }
    )
    assert cmd is not None
    assert cmd.name == "status"
    assert cmd.chat_id == "42"


def test_ignores_foreign_chat() -> None:
    tr = FakeTransport()
    tr.updates = [
        {
            "update_id": 1,
            "message": {"text": "/halt", "chat": {"id": 999}, "from": {"id": 1}},
        }
    ]
    tg = TelegramClient(token="t", chat_id="42", transport=tr)
    assert tg.poll_commands() == []
    assert tg.offset == 2


def test_send_and_notify_filter() -> None:
    tr = FakeTransport()
    tg = TelegramClient(token="t", chat_id="42", transport=tr)
    assert tg.send("hello")
    assert not tg.notify("reject", "nope")
    assert tg.notify("halt", "HALT daily_loss")
    methods = [u.rsplit("/", 1)[-1] for u, _ in tr.sent]
    assert methods == ["sendMessage", "sendMessage"]


def test_engine_halt_and_resume_via_telegram(tmp_path) -> None:
    cfg = BotConfig()
    cfg.risk.halt_file = str(tmp_path / "HALT")
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.session.enabled = False
    broker = PaperBroker(balance=10_000)
    tr = FakeTransport()
    tg = TelegramClient(token="t", chat_id="1", transport=tr)
    engine = Engine(cfg, broker, halt_dir=str(tmp_path), telegram=tg)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/halt", 1))
    assert "flattened" in reply
    assert engine.halted
    assert (tmp_path / "HALT").exists()
    reply = engine.handle_command(TgCommand("1", 1, "/resume", 2))
    assert "cleared" in reply
    assert not engine.halted
    assert not (tmp_path / "HALT").exists()
    engine.stop()


def test_poll_dispatches_help(tmp_path) -> None:
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    tr = FakeTransport()
    tr.updates = [
        {
            "update_id": 5,
            "message": {"text": "/help", "chat": {"id": "1"}, "from": {"id": 1}},
        }
    ]
    tg = TelegramClient(token="t", chat_id="1", transport=tr)
    engine = Engine(cfg, PaperBroker(balance=10_000), halt_dir=str(tmp_path), telegram=tg)
    engine.poll_telegram()
    texts = [p.get("text", "") for _, p in tr.sent]
    assert any("halt" in t for t in texts)


def test_from_config_disabled() -> None:
    assert TelegramClient.from_config(TelegramConfig()) is None


def test_from_config_enabled() -> None:
    tr = FakeTransport()
    tg = TelegramClient.from_config(
        TelegramConfig(token="t", chat_id="9"),
        transport=tr,
    )
    assert tg is not None
    assert tg.chat_id == "9"


def test_send_failure_returns_false() -> None:
    tr = FakeTransport()
    tr.fail = True
    tg = TelegramClient(token="t", chat_id="1", transport=tr)
    assert tg.send("x") is False
    assert tg.poll_commands() == []


def test_parse_skips_non_text() -> None:
    assert parse_command({"update_id": 1, "message": {"chat": {"id": 1}}}) is None
    assert parse_command({"update_id": 1, "message": "nope"}) is None


def test_status_command(tmp_path) -> None:
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    engine = Engine(cfg, PaperBroker(balance=10_000), halt_dir=str(tmp_path))
    engine.start()
    text = engine.handle_command(TgCommand("1", 1, "/status", 1))
    assert "equity=" in text
    engine.stop()
