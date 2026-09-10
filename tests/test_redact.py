from dataclasses import dataclass
from pathlib import Path

from mt5_risk_bot.journal import Journal, redact_text

FAKE_TOKEN = "123456789:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
FAKE_PASSWORD = "s3cr3t-pass-value"


def test_journal_write_redacts_password_and_token(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    Journal(path).write("auth", password=FAKE_PASSWORD, token=FAKE_TOKEN, ok=True)
    text = path.read_text(encoding="utf-8")
    assert FAKE_PASSWORD not in text
    assert FAKE_TOKEN not in text
    rec = Journal(path).tail(1)[0]
    assert rec["event"] == "auth"
    assert rec["ok"] is True
    assert rec["password"] != FAKE_PASSWORD
    assert rec["token"] != FAKE_TOKEN


def test_journal_write_redacts_named_keys_and_keeps_safe_fields(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    Journal(path).write(
        "keys",
        api_key="sk-test-api-key-value",
        grok_key="xai-test-grok-key",
        claude_key="sk-ant-test-claude-key",
        symbol="EURUSD",
    )
    text = path.read_text(encoding="utf-8")
    assert "sk-test-api-key-value" not in text
    assert "xai-test-grok-key" not in text
    assert "sk-ant-test-claude-key" not in text
    assert "EURUSD" in text


def test_journal_write_redacts_nested_and_embedded_token(tmp_path: Path) -> None:
    @dataclass
    class Creds:
        password: str
        login: int

    path = tmp_path / "j.jsonl"
    Journal(path).write(
        "nested",
        creds=Creds(password="nested-secret-pass", login=42),
        items=[{"password": "list-secret", "n": 1}],
        error=f"telegram http failed token={FAKE_TOKEN}",
    )
    text = path.read_text(encoding="utf-8")
    assert "nested-secret-pass" not in text
    assert "list-secret" not in text
    assert FAKE_TOKEN not in text
    rec = Journal(path).tail(1)[0]
    assert rec["creds"]["login"] == 42
    assert rec["items"][0]["n"] == 1
    assert FAKE_TOKEN not in rec["error"]


def test_redact_text_strips_bot_token_keeps_rest() -> None:
    msg = f"loop error: telegram http failed token={FAKE_TOKEN} ok"
    out = redact_text(msg)
    assert FAKE_TOKEN not in out
    assert "[REDACTED]" in out
    assert "loop error" in out


def test_redact_text_strips_botfather_token() -> None:
    secret = "1234567890:AA" + "x" * 35
    out = redact_text("token " + secret + " leftover")
    assert secret not in out
    assert "leftover" in out
    assert "[REDACTED]" in out


def test_journal_file_is_0600_after_write(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    Journal(path).write("ping")
    assert path.stat().st_mode & 0o777 == 0o600
