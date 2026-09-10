from email.message import EmailMessage
from io import BytesIO

import urllib.error

from mt5_risk_bot.broker.paper import PaperBroker
from mt5_risk_bot.config import AdviceConfig, BotConfig, TelegramConfig
from mt5_risk_bot.engine import Engine
from mt5_risk_bot.llm import Advisor
from mt5_risk_bot.telegram import (
    RETRY_CAP_S,
    RETRY_TRIES,
    TelegramClient,
    TelegramError,
    TgCommand,
    UrlLibTransport,
    _chunks,
    backoff_seconds,
    parse_command,
)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.updates: list[dict] = []
        self.fail = False

    def post_json(self, url: str, payload: dict, timeout: float = 10.0, headers=None) -> dict:
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


def test_send_splits_long_text() -> None:
    tr = FakeTransport()
    tg = TelegramClient(token="t", chat_id="42", transport=tr)
    text = "x" * 4000
    assert tg.send(text)
    bodies = [p["text"] for _, p in tr.sent]
    assert len(bodies) == 2
    assert "".join(bodies) == text


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


def test_chunks_splits_and_preserves() -> None:
    assert _chunks("abcdef", 2) == ["ab", "cd", "ef"]
    assert _chunks("a", 10) == ["a"]
    assert _chunks("", 4) == []


def test_send_chunks_long_text() -> None:
    tr = FakeTransport()
    tg = TelegramClient(token="t", chat_id="42", transport=tr)
    text = "x" * 5000
    assert tg.send(text)
    bodies = [p["text"] for _, p in tr.sent]
    assert len(bodies) >= 2
    assert "".join(bodies) == text
    assert all(len(b) <= 3900 for b in bodies)


def test_poll_survives_freetext_llm_error(tmp_path) -> None:
    class Boom:
        def post_json(self, *a, **k):
            raise RuntimeError("grok down")

    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    tr = FakeTransport()
    tr.updates = [
        {
            "update_id": 8,
            "message": {"text": "should I buy euro?", "chat": {"id": "1"}, "from": {"id": 1}},
        }
    ]
    tg = TelegramClient(token="t", chat_id="1", transport=tr)
    advisor = Advisor(AdviceConfig(provider="grok", grok_key="x"), transport=Boom())
    engine = Engine(
        cfg, PaperBroker(balance=10_000), halt_dir=str(tmp_path), telegram=tg, advisor=advisor
    )
    engine.poll_telegram()
    texts = [p.get("text", "") for _, p in tr.sent]
    assert any("grok down" in t for t in texts)


class SeqTransport:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.sent: list[tuple[str, dict]] = []

    def post_json(self, url: str, payload: dict, timeout: float = 10.0, headers=None) -> dict:
        del timeout, headers
        self.sent.append((url, payload))
        if not self.responses:
            raise TelegramError("empty")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _help_update(uid: int = 10) -> dict:
    return {
        "update_id": uid,
        "message": {"text": "/help", "chat": {"id": "42"}, "from": {"id": 1}},
    }


def test_backoff_retry_after_and_cap() -> None:
    assert backoff_seconds(0, 1.5) == 1.5
    assert backoff_seconds(0, 999) == RETRY_CAP_S
    assert backoff_seconds(0, None) == 0.5
    assert backoff_seconds(1, None) == 1.0


def test_poll_retries_429_then_resumes_offset() -> None:
    tr = SeqTransport(
        [
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}},
            {"ok": True, "result": [_help_update(10)]},
        ]
    )
    sleeps: list[float] = []
    tg = TelegramClient(token="t", chat_id="42", transport=tr, sleep_fn=sleeps.append)
    cmds = tg.poll_commands()
    assert len(cmds) == 1
    assert cmds[0].name == "help"
    assert tg.offset == 11
    assert sleeps == [1.0]
    tr.responses.append({"ok": True, "result": []})
    tg.poll_commands()
    assert tr.sent[-1][1]["offset"] == 11


def test_poll_429_exhausted_keeps_offset() -> None:
    payload = {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}}
    tr = SeqTransport([payload] * RETRY_TRIES)
    tg = TelegramClient(token="t", chat_id="42", transport=tr, sleep_fn=lambda _s: None)
    assert tg.poll_commands() == []
    assert tg.offset == 0
    assert len(tr.sent) == RETRY_TRIES


def test_send_retries_503() -> None:
    tr = SeqTransport(
        [
            TelegramError("telegram http failed", status=503),
            {"ok": True, "result": {"message_id": 1}},
        ]
    )
    sleeps: list[float] = []
    tg = TelegramClient(token="t", chat_id="42", transport=tr, sleep_fn=sleeps.append)
    assert tg.send("hello") is True
    assert len(tr.sent) == 2
    assert sleeps == [0.5]


def test_send_400_not_retried() -> None:
    tr = SeqTransport([{"ok": False, "error_code": 400}])
    sleeps: list[float] = []
    tg = TelegramClient(token="t", chat_id="42", transport=tr, sleep_fn=sleeps.append)
    assert tg.send("hello") is False
    assert len(tr.sent) == 1
    assert sleeps == []


def test_http_error_status_and_no_token_leak(monkeypatch) -> None:
    hdrs = EmailMessage()
    hdrs["Retry-After"] = "3"
    fp = BytesIO(b'{"ok":false,"error_code":429,"parameters":{"retry_after":9}}')
    url = "https://api.telegram.org/botSECRETTOKEN/sendMessage"

    def boom(_req, timeout=10.0):
        del timeout
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", hdrs, fp)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    try:
        UrlLibTransport().post_json(url, {"chat_id": "1", "text": "x"})
        raise AssertionError("expected TelegramError")
    except TelegramError as exc:
        assert exc.status == 429
        assert exc.retry_after == 3.0
        assert "SECRETTOKEN" not in str(exc)
        assert exc.__cause__ is None
