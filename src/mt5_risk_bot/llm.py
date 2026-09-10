"""Grok (xAI) and Claude (Anthropic) chat. Stdlib HTTP. Keys never logged.

Conversation turns persist next to the journal so a restart does not
wipe desk context. Bound to KEEP_TURNS messages. Secrets redacted.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mt5_risk_bot.config import AdviceConfig
from mt5_risk_bot.journal import redact_text
from mt5_risk_bot.telegram import Transport, UrlLibTransport

KEEP_TURNS = 40

SYSTEM = (
    "You are a risk desk, not a tipster. One account. One book. "
    "Use only the snapshot, quotes (bid/ask/spread/ATR/ADX/EMA), positions, "
    "working orders, daily_loss and drawdown room, and history.json fills. "
    "Name a price, a stop, a target, and why the stop is invalidation. "
    "If spread is large vs ATR, or RR after costs is poor, hold. "
    "Do not stack correlated majors in the same direction. "
    "Do not size the order; the risk engine sizes and can refuse. "
    "You do not send. Prefer hold or close when daily_loss or drawdown "
    "room is thin. Never claim consistent profits. Conservative means "
    "defined SL, no chase, no martingale, no averaging into a loser. "
    "Always set sl and tp on buy/sell. Limit XOR stop. Close needs ticket. "
    "End with one JSON object, no fence:\n"
    '{"action":"buy"|"sell"|"close"|"hold","symbol":"EURUSD"|null,'
    '"sl":number|null,"tp":number|null,"limit":number|null,"stop":number|null,'
    '"ticket":number|null,"summary":"one line"}'
)

_JSON_TAIL = re.compile(r"\{[^{}]*\}\s*$", re.DOTALL)


@dataclass
class Advice:
    text: str
    action: str = "hold"
    symbol: str | None = None
    sl: float | None = None
    tp: float | None = None
    limit: float | None = None
    stop: float | None = None
    ticket: int | None = None
    summary: str = ""


def parse_advice(raw: str) -> Advice:
    text = (raw or "").strip()
    match = _JSON_TAIL.search(text)
    action, symbol, sl, tp, summary = "hold", None, None, None, ""
    limit, stop, ticket = None, None, None
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
            limit = _num(obj.get("limit"))
            stop = _num(obj.get("stop"))
            ticket = _int(obj.get("ticket"))
            summary = str(obj.get("summary") or "")
    return Advice(
        text=body or text,
        action=action,
        symbol=symbol,
        sl=sl,
        tp=tp,
        limit=limit,
        stop=stop,
        ticket=ticket,
        summary=summary,
    )


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    if n is None:
        return None
    try:
        return int(n)
    except (TypeError, ValueError):
        return None


def advice_path_for(journal_path: str | Path) -> Path:
    p = Path(journal_path)
    return p.with_name(p.stem + ".advice.json")


class Advisor:
    def __init__(
        self,
        cfg: AdviceConfig,
        transport: Transport | None = None,
        persist_path: str | Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.transport = transport or UrlLibTransport()
        self.persist_path = Path(persist_path) if persist_path else None
        self._memory: list[dict[str, str]] = []
        self.load()

    def ask(
        self,
        question: str,
        context: str,
        session: str = "",
        history: list[dict[str, Any]] | None = None,
    ) -> Advice:
        if not self.cfg.enabled:
            return Advice(
                text=(
                    "no AI key. set XAI_API_KEY, ANTHROPIC_API_KEY, or "
                    "ADVICE_URL+ADVICE_TOKEN (AI_PROVIDER=grok|claude|computer)"
                )
            )
        user = f"{context}\n\nUser: {question}"
        if self.cfg.provider == "computer":
            raw = self._computer(question, context, session, history or [])
        elif self.cfg.provider == "claude":
            raw = self._claude(user)
        else:
            raw = self._grok(user)
        advice = parse_advice(raw)
        self._remember("user", question)
        self._remember("assistant", advice.text or raw)
        return advice

    def _remember(self, role: str, content: str) -> None:
        self._memory.append({"role": role, "content": redact_text(content)})
        self._memory = self._memory[-KEEP_TURNS:]
        self.save()

    def load(self) -> None:
        path = self.persist_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        turns = raw.get("turns") if isinstance(raw, dict) else raw
        if not isinstance(turns, list):
            return
        out: list[dict[str, str]] = []
        for item in turns:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            content = redact_text(str(item.get("content") or ""))
            if content:
                out.append({"role": role, "content": content})
        self._memory = out[-KEEP_TURNS:]

    def save(self) -> None:
        path = self.persist_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"turns": self._memory}, ensure_ascii=False)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)

    def _computer(
        self,
        question: str,
        context: str,
        session: str,
        history: list[dict[str, Any]],
    ) -> str:
        data = self.transport.post_json(
            self.cfg.computer_url,
            {
                "session": session or "default",
                "question": question,
                "context": context,
                "history": history,
                "model": self.cfg.computer_model,
            },
            timeout=120.0,
            headers={"Authorization": f"Bearer {self.cfg.computer_token}"},
        )
        if data.get("error"):
            raise RuntimeError(str(data.get("error")))
        text = str(data.get("text") or "")
        if not text:
            raise RuntimeError("computer empty")
        return text

    def _grok(self, user: str) -> str:
        messages = [{"role": "system", "content": SYSTEM}, *self._memory, {"role": "user", "content": user}]
        data = self.transport.post_json(
            self.cfg.grok_url,
            {
                "model": self.cfg.grok_model,
                "messages": messages,
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
                "messages": [*self._memory, {"role": "user", "content": user}],
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
