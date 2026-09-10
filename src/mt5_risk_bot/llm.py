"""Grok (xAI) and Claude (Anthropic) chat. Stdlib HTTP. Keys never logged."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from mt5_risk_bot.config import AdviceConfig
from mt5_risk_bot.telegram import Transport, UrlLibTransport

SYSTEM = (
    "You are a trading desk analyst for one MetaTrader 5 account. "
    "You see equity, positions, and quotes. You give a view, not a guarantee. "
    "Never claim consistent profits. The risk engine sizes and can refuse. "
    "End every reply with a single JSON object on its own, no markdown fence:\n"
    '{"action":"buy"|"sell"|"close"|"hold","symbol":"EURUSD"|null,'
    '"sl":number|null,"tp":number|null,"summary":"one line"}'
)

_JSON_TAIL = re.compile(r"\{[^{}]*\}\s*$", re.DOTALL)


@dataclass
class Advice:
    text: str
    action: str = "hold"
    symbol: str | None = None
    sl: float | None = None
    tp: float | None = None
    summary: str = ""


def parse_advice(raw: str) -> Advice:
    text = (raw or "").strip()
    match = _JSON_TAIL.search(text)
    action, symbol, sl, tp, summary = "hold", None, None, None, ""
    body = text
    if match:
        body = text[: match.start()].strip()
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            obj = {}
        if isinstance(obj, dict):
            action = str(obj.get("action") or "hold").lower()
            if action not in {"buy", "sell", "close", "hold"}:
                action = "hold"
            sym = obj.get("symbol")
            symbol = str(sym).upper() if sym else None
            sl = _num(obj.get("sl"))
            tp = _num(obj.get("tp"))
            summary = str(obj.get("summary") or "")
    return Advice(text=body or text, action=action, symbol=symbol, sl=sl, tp=tp, summary=summary)


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Advisor:
    def __init__(self, cfg: AdviceConfig, transport: Transport | None = None) -> None:
        self.cfg = cfg
        self.transport = transport or UrlLibTransport()

    def ask(self, question: str, context: str) -> Advice:
        if not self.cfg.enabled:
            return Advice(
                text=(
                    "no AI key. set XAI_API_KEY for Grok or ANTHROPIC_API_KEY for Claude "
                    "(AI_PROVIDER=grok|claude)"
                )
            )
        user = f"{context}\n\nUser: {question}"
        if self.cfg.provider == "claude":
            raw = self._claude(user)
        else:
            raw = self._grok(user)
        return parse_advice(raw)

    def _grok(self, user: str) -> str:
        data = self.transport.post_json(
            self.cfg.grok_url,
            {
                "model": self.cfg.grok_model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
            timeout=60.0,
            headers={"Authorization": f"Bearer {self.cfg.grok_key}"},
        )
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("grok empty")
        return str(choices[0].get("message", {}).get("content") or "")

    def _claude(self, user: str) -> str:
        data = self.transport.post_json(
            self.cfg.claude_url,
            {
                "model": self.cfg.claude_model,
                "max_tokens": 800,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60.0,
            headers={
                "x-api-key": self.cfg.claude_key,
                "anthropic-version": "2023-06-01",
            },
        )
        blocks = data.get("content") or []
        parts = []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text") or ""))
        return "\n".join(parts)
