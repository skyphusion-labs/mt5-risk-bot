"""Append-only JSONL audit log. Every risk reject and every fill goes here.

Rotate the live file to <name>.1 (replacing any previous .1) before a
write that would exceed 10 MiB. The new live file is chmod 0600.
tail() and last_event() (confirm restore) read only the live file.
Rotated history is <name>.1.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

_SECRET_KEYS = frozenset({"token", "password", "api_key", "grok_key", "claude_key"})
_REDACTED = "[REDACTED]"
# BotFather tokens: <id>:<secret> with 8-12 digit id and 30+ url-safe chars.
_TG_TOKEN_RE = re.compile(r"\d{8,12}:[A-Za-z0-9_-]{30,}")
_ROTATE_BYTES = 10 * 1024 * 1024


class Journal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            _chmod600(self.path)

    def write(self, event: str, **fields: Any) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{k: _jsonable(v) for k, v in fields.items()},
        }
        rec = redact(rec)
        line = json.dumps(rec, default=str) + "\n"
        self._rotate_if_needed(len(line.encode("utf-8")))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        _chmod600(self.path)

    def _rotate_if_needed(self, incoming: int) -> None:
        if not self.path.exists():
            return
        size = self.path.stat().st_size
        if size + incoming <= _ROTATE_BYTES:
            return
        dest = self.path.with_name(self.path.name + ".1")
        self.path.replace(dest)
        self.path.touch()
        _chmod600(self.path)

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


class InstanceLockError(RuntimeError):
    """Another process already holds this journal's run lock."""


def lock_path_for(journal_path: str | Path) -> Path:
    p = Path(journal_path)
    return p.with_name(p.stem + ".lock")


class InstanceLock:
    """Exclusive lock next to the journal. Released on close or crash.

    Unix: flock. Windows: msvcrt.locking. Same file, same fail.
    """

    def __init__(self, journal_path: str | Path) -> None:
        self.path = lock_path_for(journal_path)
        self._fh: TextIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+", encoding="utf-8")
        _chmod600(self.path)
        try:
            _lock_nb(fh)
        except OSError as exc:
            fh.close()
            raise InstanceLockError(
                f"already running: another process holds {self.path} "
                "(two run --loop cannot share journal/offset)"
            ) from exc
        self._fh = fh

    def release(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        try:
            _unlock(fh)
        finally:
            fh.close()

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def redact_text(s: str) -> str:
    """Replace BotFather tokens in free text (stderr, Telegram echoes)."""
    return _TG_TOKEN_RE.sub(_REDACTED, s)


def redact(v: Any) -> Any:
    """Strip secret-named keys and BotFather tokens from logs and chat."""
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        for k, val in v.items():
            if str(k).lower() in _SECRET_KEYS:
                out[k] = _REDACTED
            else:
                out[k] = redact(val)
        return out
    if isinstance(v, (list, tuple)):
        return [redact(x) for x in v]
    if isinstance(v, str):
        return redact_text(v)
    return v


def _chmod600(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


def _lock_nb(fh: TextIO) -> None:
    """Non-blocking exclusive lock. Raises OSError if held."""
    fd = fh.fileno()
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        if fh.read(1) == "":
            fh.write("0")
            fh.flush()
        fh.seek(0)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fh: TextIO) -> None:
    fd = fh.fileno()
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


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
