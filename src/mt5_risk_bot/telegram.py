"""Telegram Bot API client. Stdlib only. Token never logged.

Alerts and operator commands. Unauthorized chat ids are ignored.
Transport is injectable so tests never hit api.telegram.org.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from mt5_risk_bot.config import TelegramConfig

API_ROOT = "https://api.telegram.org"


class Transport(Protocol):
    def post_json(self, url: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]: ...


class UrlLibTransport:
    def post_json(self, url: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise TelegramError("telegram http failed") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TelegramError("telegram non-json") from exc
        if not isinstance(data, dict):
            raise TelegramError("telegram bad payload")
        return data


class TelegramError(RuntimeError):
    pass


@dataclass(frozen=True)
class TgCommand:
    chat_id: str
    user_id: int
    text: str
    update_id: int

    @property
    def name(self) -> str:
        if not self.text:
            return ""
        first = self.text.strip().split(maxsplit=1)[0]
        first = first.split("@", 1)[0]
        if first.startswith("/"):
            return first[1:].lower()
        return first.lower()


def parse_command(update: dict[str, Any]) -> TgCommand | None:
    msg = update.get("message") or update.get("edited_message") or {}
    if not isinstance(msg, dict):
        return None
    text = msg.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    chat = msg.get("chat") or {}
    user = msg.get("from") or {}
    return TgCommand(
        chat_id=str(chat.get("id", "")),
        user_id=int(user.get("id", 0) or 0),
        text=text.strip(),
        update_id=int(update.get("update_id", 0) or 0),
    )


HELP = (
    "mt5-risk-bot\n"
    "/status     equity, halt, peak\n"
    "/positions  open trades (this magic)\n"
    "/halt       flatten and stop new trades\n"
    "/resume     clear operator HALT file only\n"
    "/help"
)


@dataclass
class TelegramClient:
    token: str
    chat_id: str
    notify_events: frozenset[str] = field(
        default_factory=lambda: frozenset({"start", "stop", "open", "close", "halt", "order_check_fail"})
    )
    transport: Transport = field(default_factory=UrlLibTransport)
    offset: int = 0

    @classmethod
    def from_config(cls, cfg: TelegramConfig, transport: Transport | None = None) -> TelegramClient | None:
        if not cfg.enabled:
            return None
        kwargs: dict[str, Any] = {
            "token": cfg.token,
            "chat_id": str(cfg.chat_id),
            "notify_events": frozenset(cfg.notify_events),
        }
        if transport is not None:
            kwargs["transport"] = transport
        return cls(**kwargs)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _url(self, method: str) -> str:
        return f"{API_ROOT}/bot{self.token}/{method}"

    def send(self, text: str) -> bool:
        if not self.enabled or not text:
            return False
        try:
            data = self.transport.post_json(
                self._url("sendMessage"),
                {
                    "chat_id": self.chat_id,
                    "text": text[:3900],
                    "disable_web_page_preview": True,
                },
            )
        except TelegramError:
            return False
        return bool(data.get("ok"))

    def poll_commands(self, *, timeout: int = 0) -> list[TgCommand]:
        if not self.enabled:
            return []
        payload = {
            "offset": self.offset,
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        try:
            data = self.transport.post_json(self._url("getUpdates"), payload, timeout=float(timeout + 5))
        except TelegramError:
            return []
        if not data.get("ok"):
            return []
        out: list[TgCommand] = []
        for upd in data.get("result") or []:
            if not isinstance(upd, dict):
                continue
            uid = int(upd.get("update_id", 0) or 0)
            self.offset = max(self.offset, uid + 1)
            cmd = parse_command(upd)
            if cmd is None:
                continue
            if cmd.chat_id != str(self.chat_id):
                continue
            out.append(cmd)
        return out

    def notify(self, event: str, text: str) -> bool:
        if event not in self.notify_events:
            return False
        return self.send(text)
