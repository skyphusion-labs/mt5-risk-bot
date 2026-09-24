"""Redaction gaps on the advice path (#15 item 1).

The scrubber (`journal.redact_text`) covered the BotFather token pattern
only. `Advisor._remember` redacts free text before it joins `self._memory`,
and `self._memory` is replayed verbatim into the `messages` payload on
every subsequent `_grok` / `_claude` call, and persisted to
`journal.advice.json`. So a secret shaped like an Anthropic or xAI key,
pasted once into a chat question or echoed in a model reply, was written
to disk in the clear and re-sent to the THIRD-PARTY PROVIDER on every
following turn until it aged out of the 40-turn window. That is an
exfiltration path, not a logging gap, and it is the property under test
here, on the persisted artifact and the actual outbound payload, never on
the chat reply.

Fake key material below has no live scope and is shaped only to match the
patterns being tested; it authenticates nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from mt5_risk_bot.config import AdviceConfig
from mt5_risk_bot.journal import redact_text
from mt5_risk_bot.llm import Advisor

FAKE_ANTHROPIC_KEY = "sk-ant-" + "a1B2c3D4e5F6g7H8i9J0" * 2
FAKE_XAI_KEY = "xai-" + "k1L2m3N4o5P6q7R8s9T0" * 2


class _RecordingTransport:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []

    def post_json(self, url: str, payload: dict, timeout: float = 10.0, headers=None) -> dict:
        del timeout, headers
        self.calls.append({"url": url, "payload": payload})
        text = self._replies.pop(0) if self._replies else "hold"
        return {"choices": [{"message": {"content": text}}]}


def _grok_advisor(tmp_path: Path, replies: list[str]) -> tuple[Advisor, _RecordingTransport]:
    tr = _RecordingTransport(replies)
    cfg = AdviceConfig(provider="grok", grok_key="fake-grok-key")
    advisor = Advisor(cfg, transport=tr, persist_path=tmp_path / "j.advice.json")
    return advisor, tr


# --- 1. the scrubber patterns, direct ---------------------------------------


def test_redact_text_strips_anthropic_key() -> None:
    out = redact_text(f"here is my key {FAKE_ANTHROPIC_KEY} use it")
    assert FAKE_ANTHROPIC_KEY not in out
    assert "[REDACTED]" in out
    assert "use it" in out


def test_redact_text_strips_xai_key() -> None:
    out = redact_text(f"here is my key {FAKE_XAI_KEY} use it")
    assert FAKE_XAI_KEY not in out
    assert "[REDACTED]" in out
    assert "use it" in out


# --- 2. the persisted artifact never carries the raw secret ------------------


def test_advisor_persists_redacted_not_raw_anthropic_key(tmp_path: Path) -> None:
    advisor, _ = _grok_advisor(tmp_path, ["hold {\"action\":\"hold\"}"])
    advisor.ask(f"my key is {FAKE_ANTHROPIC_KEY}", context="ctx")
    on_disk = (tmp_path / "j.advice.json").read_text(encoding="utf-8")
    assert FAKE_ANTHROPIC_KEY not in on_disk
    assert "[REDACTED]" in on_disk


def test_advisor_persists_redacted_not_raw_xai_key(tmp_path: Path) -> None:
    advisor, _ = _grok_advisor(tmp_path, ["hold {\"action\":\"hold\"}"])
    advisor.ask(f"my key is {FAKE_XAI_KEY}", context="ctx")
    on_disk = (tmp_path / "j.advice.json").read_text(encoding="utf-8")
    assert FAKE_XAI_KEY not in on_disk
    assert "[REDACTED]" in on_disk


# --- 3. the replay claim: does a SECOND call resend the FIRST turn's secret -


def test_advisor_does_not_replay_secret_on_next_call(tmp_path: Path) -> None:
    """The property that matters: not what is stored, what is SENT again."""
    advisor, tr = _grok_advisor(
        tmp_path, ["hold {\"action\":\"hold\"}", "hold {\"action\":\"hold\"}"]
    )
    advisor.ask(f"my key is {FAKE_ANTHROPIC_KEY}", context="ctx")
    advisor.ask("what now", context="ctx")
    assert len(tr.calls) == 2
    second_payload = json.dumps(tr.calls[1]["payload"])
    assert FAKE_ANTHROPIC_KEY not in second_payload, (
        "a secret from an earlier turn was replayed to the provider"
    )
    assert "[REDACTED]" in second_payload


def test_advisor_does_not_replay_a_key_echoed_in_the_reply(tmp_path: Path) -> None:
    """The MODEL can echo a pasted key back in its own text; that must be
    scrubbed on the assistant turn too, not just the user turn."""
    advisor, tr = _grok_advisor(
        tmp_path,
        [f"noted, your key {FAKE_XAI_KEY} looks valid", "hold {\"action\":\"hold\"}"],
    )
    advisor.ask("is this a valid key", context="ctx")
    advisor.ask("what now", context="ctx")
    second_payload = json.dumps(tr.calls[1]["payload"])
    assert FAKE_XAI_KEY not in second_payload


# --- 4. an already-persisted secret is scrubbed on the NEXT process's load --


def test_load_scrubs_a_legacy_unredacted_turn_before_any_replay(tmp_path: Path) -> None:
    """Simulates upgrading from before this fix: an old journal.advice.json
    already holds a raw key. The next process must not replay it either."""
    path = tmp_path / "j.advice.json"
    path.write_text(
        json.dumps({"turns": [{"role": "user", "content": f"key {FAKE_ANTHROPIC_KEY}"}]}),
        encoding="utf-8",
    )
    advisor, tr = _grok_advisor(tmp_path, ["hold {\"action\":\"hold\"}"])
    advisor.ask("what now", context="ctx")
    assert len(tr.calls) == 1
    payload = json.dumps(tr.calls[0]["payload"])
    assert FAKE_ANTHROPIC_KEY not in payload, (
        "a legacy unredacted turn was replayed on the first call after load"
    )
