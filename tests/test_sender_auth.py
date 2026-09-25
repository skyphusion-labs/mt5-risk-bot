"""Sender-level authorization on the inbound command path (GHSA-9fg6-2x5f-3jvp)."""

from __future__ import annotations

import pytest

from straightedge.broker.paper import PaperBroker
from straightedge.config import BotConfig, TelegramConfig, load_config
from straightedge.engine import Engine
from straightedge.telegram import TelegramClient


class FakeTransport:
    def __init__(self, updates: list[dict] | None = None) -> None:
        self.updates = list(updates or [])
        self.sent: list[tuple[str, dict]] = []

    def post_json(self, url: str, payload: dict, timeout: float = 10.0, headers=None) -> dict:
        del timeout, headers
        self.sent.append((url, payload))
        if url.endswith("/getUpdates"):
            out = self.updates
            self.updates = []
            return {"ok": True, "result": out}
        return {"ok": True, "result": {"message_id": 1}}


def _update(uid: int, text: str, chat: str = "42", sender: int | None = 1) -> dict:
    msg: dict = {"text": text, "chat": {"id": chat}}
    if sender is not None:
        msg["from"] = {"id": sender}
    return {"update_id": uid, "message": msg}


def test_unauthorized_sender_refused_on_trade_command() -> None:
    tr = FakeTransport([_update(1, "/buy EURUSD", sender=999)])
    tg = TelegramClient(token="t", chat_id="42", transport=tr, allow_senders=frozenset({7}))
    assert tg.poll_commands() == []
    assert tg.offset == 2


def test_unauthorized_sender_refused_on_read_only_command() -> None:
    tr = FakeTransport([_update(1, "/status", sender=999), _update(2, "/positions", sender=999)])
    tg = TelegramClient(token="t", chat_id="42", transport=tr, allow_senders=frozenset({7}))
    assert tg.poll_commands() == []


def test_unauthorized_sender_refused_on_free_text() -> None:
    tr = FakeTransport([_update(1, "should I buy euro?", sender=999)])
    tg = TelegramClient(token="t", chat_id="42", transport=tr, allow_senders=frozenset({7}))
    assert tg.poll_commands() == []


def test_authorized_sender_is_dispatched() -> None:
    tr = FakeTransport([_update(1, "/status", sender=7)])
    tg = TelegramClient(token="t", chat_id="42", transport=tr, allow_senders=frozenset({7}))
    cmds = tg.poll_commands()
    assert [c.name for c in cmds] == ["status"]
    assert cmds[0].user_id == 7


def test_update_without_sender_is_refused() -> None:
    tr = FakeTransport([_update(1, "/status", sender=None)])
    tg = TelegramClient(token="t", chat_id="42", transport=tr, allow_senders=frozenset({7}))
    assert tg.poll_commands() == []


def test_update_without_sender_is_refused_even_with_no_allow_list() -> None:
    tr = FakeTransport([_update(1, "/status", sender=None)])
    tg = TelegramClient(token="t", chat_id="42", transport=tr)
    assert tg.poll_commands() == []


def test_shared_chat_without_allow_list_refuses_to_start() -> None:
    cfg = BotConfig()
    cfg.telegram = TelegramConfig(token="t", chat_id="-1001234567890")
    with pytest.raises(ValueError, match="allow_senders"):
        cfg.validate()


def test_shared_chat_with_allow_list_starts() -> None:
    cfg = BotConfig()
    cfg.telegram = TelegramConfig(token="t", chat_id="-1001234567890", allow_senders=(7,))
    cfg.validate()


def test_shared_chat_without_allow_list_refuses_client_construction() -> None:
    with pytest.raises(ValueError, match="allow_senders"):
        TelegramClient.from_config(TelegramConfig(token="t", chat_id="-1001234567890"))


def test_private_chat_without_allow_list_is_unchanged() -> None:
    cfg = BotConfig()
    cfg.telegram = TelegramConfig(token="t", chat_id="42")
    cfg.validate()
    tg = TelegramClient.from_config(
        cfg.telegram, transport=FakeTransport([_update(1, "/status", sender=42)])
    )
    assert tg is not None
    assert [c.name for c in tg.poll_commands()] == ["status"]


def test_rejected_sender_is_audited() -> None:
    seen: list[tuple[str, dict]] = []
    tr = FakeTransport([_update(1, "/buy EURUSD", sender=999)])
    tg = TelegramClient(
        token="t",
        chat_id="42",
        transport=tr,
        allow_senders=frozenset({7}),
        audit_fn=lambda event, fields: seen.append((event, fields)),
    )
    tg.poll_commands()
    assert len(seen) == 1
    event, fields = seen[0]
    assert event == "command_rejected"
    assert fields["user_id"] == 999
    assert fields["command"] == "buy"


def test_engine_journals_the_rejected_sender(tmp_path) -> None:
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    tr = FakeTransport([_update(1, "/buy EURUSD", chat="1", sender=999)])
    tg = TelegramClient(token="t", chat_id="1", transport=tr, allow_senders=frozenset({7}))
    engine = Engine(cfg, PaperBroker(balance=10_000), halt_dir=str(tmp_path), telegram=tg)
    engine.poll_telegram()
    hits = [r for r in engine.journal.tail(20) if r.get("event") == "command_rejected"]
    assert hits and hits[-1]["user_id"] == 999
    assert not any(url.endswith("/sendMessage") for url, _ in tr.sent)


def _write_cfg(tmp_path, body: str):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_allow_senders_from_toml(tmp_path) -> None:
    path = _write_cfg(
        tmp_path,
        "[telegram]\ntoken = \"t\"\nchat_id = \"-1001\"\nallow_senders = [7, 9, 7]\n",
    )
    cfg = load_config(path)
    assert cfg.telegram.allow_senders == (7, 9)
    cfg.validate()


def test_allow_senders_from_env_overrides_toml(tmp_path, monkeypatch) -> None:
    path = _write_cfg(tmp_path, "[telegram]\ntoken = \"t\"\nchat_id = \"-1001\"\n")
    monkeypatch.setenv("TELEGRAM_ALLOW_SENDERS", " 11 , 12 ")
    cfg = load_config(path)
    assert cfg.telegram.allow_senders == (11, 12)
    cfg.validate()


def test_allow_senders_rejects_non_numeric(tmp_path) -> None:
    path = _write_cfg(tmp_path, "[telegram]\nallow_senders = [\"nope\"]\n")
    with pytest.raises(ValueError, match="allow_senders"):
        load_config(path)


def test_pre_fix_single_operator_config_needs_no_edit(tmp_path) -> None:
    from datetime import datetime, timezone

    from straightedge.synthetic import generate_bars

    path = _write_cfg(
        tmp_path,
        "[account]\nmode = \"paper\"\n\n[symbols]\nnames = [\"EURUSD\"]\n\n"
        "[session]\nenabled = false\n\n[risk]\nmax_spread_atr_frac = 10.0\n\n"
        "[telegram]\ntoken = \"t\"\nchat_id = \"42\"\n",
    )
    cfg = load_config(path)
    cfg.validate()
    assert cfg.telegram.allow_senders == ()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.risk.halt_file = str(tmp_path / "HALT")
    broker = PaperBroker(balance=10_000)
    broker.seed_bars("EURUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=3))
    tr = FakeTransport(
        [_update(1, "/buy EURUSD", chat="42", sender=42)]
    )
    tg = TelegramClient.from_config(cfg.telegram, transport=tr)
    engine = Engine(
        cfg,
        broker,
        halt_dir=str(tmp_path),
        telegram=tg,
        now_fn=lambda: datetime(2024, 1, 3, 12, tzinfo=timezone.utc),
    )
    engine.start()
    engine.poll_telegram()
    staged = [p for u, p in tr.sent if u.endswith("/sendMessage")]
    assert any("confirm buy" in p["text"] for p in staged)
    tr.updates = [_update(2, "/confirm", chat="42", sender=42)]
    engine.poll_telegram()
    assert engine.broker.positions()
    engine.stop()
