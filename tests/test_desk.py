from datetime import datetime, timezone

from mt5_risk_bot.broker.paper import PaperBroker
from mt5_risk_bot.config import AdviceConfig, BotConfig
from mt5_risk_bot.engine import Engine
from mt5_risk_bot.llm import Advisor, parse_advice
from mt5_risk_bot.synthetic import generate_bars
from mt5_risk_bot.telegram import TgCommand


class FakeLlm:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.sent: list[tuple[str, dict]] = []

    def post_json(self, url: str, payload: dict, timeout: float = 10.0, headers=None) -> dict:
        self.sent.append((url, payload))
        return self.payload


def _engine(tmp_path, *, llm=None) -> Engine:
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.session.enabled = False
    cfg.risk.max_spread_atr_frac = 10.0
    cfg.risk.halt_file = str(tmp_path / "HALT")
    broker = PaperBroker(balance=10_000)
    broker.seed_bars("EURUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=3))
    advisor = None
    if llm is not None:
        advisor = Advisor(AdviceConfig(provider="grok", grok_key="x"), transport=llm)
    return Engine(
        cfg,
        broker,
        halt_dir=str(tmp_path),
        advisor=advisor,
        now_fn=lambda: datetime(2024, 1, 3, 12, tzinfo=timezone.utc),
    )


def test_buy_confirm_opens(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    assert "confirm buy EURUSD" in reply
    assert not engine.broker.positions()
    reply = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert reply.startswith("sent buy")
    assert engine.broker.positions()
    engine.stop()


def test_quote_and_close(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    q = engine.handle_command(TgCommand("1", 1, "/quote EURUSD", 1))
    assert "bid=" in q
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 2))
    engine.handle_command(TgCommand("1", 1, "/confirm", 3))
    closed = engine.handle_command(TgCommand("1", 1, "/close EURUSD", 4))
    assert closed.startswith("closed")
    assert not engine.broker.positions()
    engine.stop()


def test_ask_stages_grok_trade(tmp_path) -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": (
                        "Trend is up.\n"
                        '{"action":"buy","symbol":"EURUSD","sl":null,"tp":null,"summary":"join long"}'
                    )
                }
            }
        ]
    }
    engine = _engine(tmp_path, llm=FakeLlm(payload))
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "should I buy euro?", 1))
    assert "Trend is up" in reply
    assert "/confirm" in reply
    engine.stop()


def test_parse_advice_json() -> None:
    adv = parse_advice('Stay out.\n{"action":"hold","symbol":null,"sl":null,"tp":null,"summary":"range"}')
    assert adv.action == "hold"
    assert "Stay out" in adv.text


def test_auto_toggle(tmp_path) -> None:
    engine = _engine(tmp_path)
    assert engine.cfg.strategy.auto is False
    assert "auto on" in engine.handle_command(TgCommand("1", 1, "/auto on", 1))
    assert engine.cfg.strategy.auto is True


def test_cancel_and_help(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    assert "cancelled" in engine.handle_command(TgCommand("1", 1, "/cancel", 2))
    assert "nothing to cancel" in engine.handle_command(TgCommand("1", 1, "/cancel", 3))
    assert "/buy" in engine.handle_command(TgCommand("1", 1, "/help", 4))
    engine.stop()


def test_close_ticket_and_stops(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    pos = engine.broker.positions()[0]
    assert "sl #" in engine.handle_command(TgCommand("1", 1, f"/sl {pos.ticket} {pos.sl}", 3))
    assert "closed" in engine.handle_command(TgCommand("1", 1, f"/close {pos.ticket}", 4))
    engine.stop()


def test_model_and_missing_ask(tmp_path) -> None:
    engine = _engine(tmp_path)
    assert "no AI key" in engine.handle_command(TgCommand("1", 1, "/ask hi", 1))
    payload = {"content": [{"type": "text", "text": "Hold.\n{\"action\":\"hold\",\"symbol\":null,\"sl\":null,\"tp\":null,\"summary\":\"x\"}"}]}
    engine2 = _engine(tmp_path, llm=FakeLlm(payload))
    engine2.cfg.advice.provider = "claude"
    engine2.advisor.cfg.provider = "claude"
    engine2.advisor.cfg.claude_key = "x"
    reply = engine2.handle_command(TgCommand("1", 1, "/ask hold?", 1))
    assert "Hold" in reply
    assert "provider=claude" in engine2.handle_command(TgCommand("1", 1, "/model claude", 2))


def test_parse_advice_bad_json() -> None:
    adv = parse_advice("just text {not json}")
    assert adv.action == "hold"
    assert "just text" in adv.text


def test_quote_usage(tmp_path) -> None:
    engine = _engine(tmp_path)
    assert "usage" in engine.handle_command(TgCommand("1", 1, "/quote", 1))
    assert "usage" in engine.handle_command(TgCommand("1", 1, "/close", 2))
    assert "unknown" in engine.handle_command(TgCommand("1", 1, "/nope", 3))
