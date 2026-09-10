"""Append-only JSONL audit log. Every risk reject and every fill goes here."""

from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SECRET_KEYS = frozenset({"token", "password", "api_key", "grok_key", "claude_key"})
_REDACTED = "[REDACTED]"
# BotFather tokens: <id>:<secret> with 8-12 digit id and 30+ url-safe chars.
_TG_TOKEN_RE = re.compile(r"\d{8,12}:[A-Za-z0-9_-]{30,}")


class Journal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: Any) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{k: _jsonable(v) for k, v in fields.items()},
        }
        rec = _redact(rec)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        if n <= 0 or not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            lines = deque(fh, maxlen=n)
        out: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    def last_event(self, *names: str) -> dict[str, Any] | None:
        """Last record whose event is one of names. Full scan; start is rare."""
        if not names or not self.path.exists():
            return None
        wanted = set(names)
        found: dict[str, Any] | None = None
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and rec.get("event") in wanted:
                    found = rec
        return found


def _redact(v: Any) -> Any:
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        for k, val in v.items():
            if str(k).lower() in _SECRET_KEYS:
                out[k] = _REDACTED
            else:
                out[k] = _redact(val)
        return out
    if isinstance(v, (list, tuple)):
        return [_redact(x) for x in v]
    if isinstance(v, str):
        return _TG_TOKEN_RE.sub(_REDACTED, v)
    return v


def _jsonable(v: Any) -> Any:
    if hasattr(v, "__dataclass_fields__"):
        from dataclasses import asdict

        out = asdict(v)
        for k, val in list(out.items()):
            if hasattr(val, "value"):
                out[k] = val.value
        return out
    if hasattr(v, "value"):
        return v.value
    return v
