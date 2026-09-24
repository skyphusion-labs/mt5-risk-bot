"""Durable risk state beside the journal. The gate's INPUT, never its verdict.

Restart must not hand out a new loss budget inside the same UTC day, and must
not zero the equity peak that the drawdown gate reads. This module persists the
`EquitySnapshot` that both gates recompute from.

Four rules, each load-bearing:

- The snapshot is the INPUT to the recomputation, not a cached verdict. Every
  gate still recomputes from it on every call. Nothing here makes a halt sticky.
- Three fields are load-bearing on restore: `day_key`, `day_start_equity` and
  `peak_equity`. `time`, `balance` and `equity` are written for forensics only;
  the broker re-supplies them through `RiskManager.observe()` before any gate
  reads them, so on disk they are AS OF `written_at`, not current.
- The write is atomic: temp file in the same directory, fsync, `os.replace`.
  This process can be SIGKILLed mid-write, so a partial file must never be
  reachable; the reader sees the previous snapshot or the new one.
- A read or write that fails is COULD NOT MEASURE, and is NOT a clean state. An
  absent file returns None (a genuine first start). Anything else raises, and
  the money gate fails CLOSED on it. Non-finite numbers are rejected on purpose:
  a NaN `peak_equity` would make the drawdown comparison silently false forever.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mt5_risk_bot.models import EquitySnapshot

SNAPSHOT_VERSION = 1

_WRITE_FLAGS = os.O_WRONLY
_WRITE_FLAGS |= os.O_CREAT
_WRITE_FLAGS |= os.O_TRUNC


class StateUnreadable(RuntimeError):
    """The snapshot exists but could not be trusted. Not a clean state."""


class StateUnwritable(RuntimeError):
    """The snapshot could not be written. The next restart would lose it."""


def snapshot_path_for(journal_path: str | Path) -> Path:
    """Sidecar beside the journal: journal.jsonl -> journal.equity.json."""
    p = Path(journal_path)
    return p.with_name(p.stem + ".equity.json")


def load_snapshot(path: str | Path) -> EquitySnapshot | None:
    """Restore the snapshot. None means absent: a clean first start.

    Raises StateUnreadable for a corrupt, truncated, mistyped, non-finite or
    unknown-version file. The caller must not treat that as a clean start.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StateUnreadable(f"cannot read {p}: {exc}") from exc
    if not raw.strip():
        raise StateUnreadable(f"{p} is empty")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StateUnreadable(f"{p} is not valid json: {exc}") from exc
    if not isinstance(data, dict):
        raise StateUnreadable(f"{p} is not a json object")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise StateUnreadable(f"{p} has no integer version")
    if version != SNAPSHOT_VERSION:
        raise StateUnreadable(
            f"{p} version {version}, this build reads {SNAPSHOT_VERSION}"
        )
    day = data.get("day_key")
    if not isinstance(day, str):
        raise StateUnreadable(f"{p} day_key is not a string")
    return EquitySnapshot(
        time=_req_int(p, data, "time"),
        balance=_req_float(p, data, "balance"),
        equity=_req_float(p, data, "equity"),
        peak_equity=_req_float(p, data, "peak_equity", non_negative=True),
        day_start_equity=_req_float(p, data, "day_start_equity", non_negative=True),
        day_key=day,
    )


def save_snapshot(path: str | Path, snap: EquitySnapshot) -> Path:
    """Atomically replace the snapshot. Raises StateUnwritable on any OSError."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    payload = {
        "version": SNAPSHOT_VERSION,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "time": int(snap.time),
        "balance": float(snap.balance),
        "equity": float(snap.equity),
        "peak_equity": float(snap.peak_equity),
        "day_start_equity": float(snap.day_start_equity),
        "day_key": str(snap.day_key),
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, _WRITE_FLAGS, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, allow_nan=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        except BaseException:
            # Covers a failed serialise AND a failed replace. Leaving the temp
            # file behind would leak a 0600 file and litter the journal dir.
            _unlink_quiet(tmp)
            raise
        os.chmod(p, 0o600)
    except (OSError, ValueError) as exc:
        raise StateUnwritable(f"cannot write {p}: {exc}") from exc
    return p


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        return


def _number(p: Path, data: dict[str, Any], key: str) -> float:
    v = data.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise StateUnreadable(f"{p} {key} is not a number")
    if not math.isfinite(float(v)):
        raise StateUnreadable(f"{p} {key} is not finite")
    return float(v)


def _req_float(
    p: Path, data: dict[str, Any], key: str, *, non_negative: bool = False
) -> float:
    v = _number(p, data, key)
    if non_negative and v < 0:
        raise StateUnreadable(f"{p} {key} is negative")
    return v


def _req_int(p: Path, data: dict[str, Any], key: str) -> int:
    return int(_number(p, data, key))
