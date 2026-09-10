from datetime import datetime, timezone

from mt5_risk_bot.broker.paper import PaperBroker
from mt5_risk_bot.config import AdviceConfig, BotConfig
from mt5_risk_bot.constants import (
    TRADE_ACTION_DEAL,
    TRADE_ACTION_SLTP,
    TRADE_RETCODE_INVALID,
    TRADE_RETCODE_INVALID_STOPS,
)
from mt5_risk_bot.engine import Engine
from mt5_risk_bot.llm import Advisor, parse_advice
from mt5_risk_bot.models import OrderResult
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
    engine.start()
    book = engine.handle_command(TgCommand("1", 1, "/quote", 1))
    assert "EURUSD" in book
    assert "bid=" in book
    assert "usage" in engine.handle_command(TgCommand("1", 1, "/close", 2))
    assert "unknown" in engine.handle_command(TgCommand("1", 1, "/nope", 3))
    engine.stop()


def test_risk_and_trail(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    risk = engine.handle_command(TgCommand("1", 1, "/risk", 1))
    assert "risk_pct=" in risk
    assert "daily_loss=" in risk
    assert "usage" in engine.handle_command(TgCommand("1", 1, "/trail", 2))
    assert "no such ticket" in engine.handle_command(TgCommand("1", 1, "/trail 999", 3))
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 4))
    engine.handle_command(TgCommand("1", 1, "/confirm", 5))
    pos = engine.broker.positions()[0]
    reply = engine.handle_command(TgCommand("1", 1, f"/trail {pos.ticket}", 6))
    assert reply.startswith("trail #")
    engine.stop()


def test_confirm_after_halt_does_not_open(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    engine.handle_command(TgCommand("1", 1, "/halt", 2))
    reply = engine.handle_command(TgCommand("1", 1, "/confirm", 3))
    assert "sent" not in reply
    assert not engine.broker.positions()
    engine.stop()


def test_stage_refuses_overwrite(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    first = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    assert "confirm buy EURUSD" in first
    second = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 2))
    assert "pending buy EURUSD" in second
    assert "/cancel" in second
    engine.stop()


def test_ask_http_error_does_not_kill(tmp_path) -> None:
    class Boom:
        def post_json(self, url, payload, timeout=10.0, headers=None):
            raise RuntimeError("grok empty")

    engine = _engine(tmp_path, llm=Boom())
    reply = engine.handle_command(TgCommand("1", 1, "should I buy?", 1))
    assert "grok empty" in reply


def test_buy_wrong_side_sl_refused(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    tick = engine.broker.tick("EURUSD")
    reply = engine.handle_command(TgCommand("1", 1, f"/buy EURUSD sl={tick.ask + 0.01} tp={tick.ask + 0.02}", 1))
    assert "sl < entry < tp" in reply
    assert engine.desk.pending is None
    engine.stop()


def test_partial_close_and_history(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    pos = engine.broker.positions()[0]
    opened = pos.volume
    half = round(opened / 2, 2)
    reply = engine.handle_command(TgCommand("1", 1, f"/close {pos.ticket} {half}", 3))
    assert reply.startswith("closed")
    left = engine.broker.positions()
    assert len(left) == 1
    assert left[0].volume == round(opened - half, 8)
    hist = engine.handle_command(TgCommand("1", 1, "/history", 4))
    assert "open" in hist or "close" in hist
    engine.stop()


def test_be_missing_ticket_and_winner(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    assert "no such ticket" in engine.handle_command(TgCommand("1", 1, "/be 999", 1))
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 2))
    engine.handle_command(TgCommand("1", 1, "/confirm", 3))
    pos = engine.broker.positions()[0]
    last = engine.broker.rates("EURUSD", engine.cfg.strategy.timeframe_id, 1)[-1]
    from mt5_risk_bot.models import Bar

    engine.broker.seed_bars(
        "EURUSD",
        engine.broker.rates("EURUSD", engine.cfg.strategy.timeframe_id, 200)
        + [
            Bar(
                time=last.time + 3600,
                open=last.close,
                high=last.close + 0.05,
                low=last.close,
                close=last.close + 0.04,
            )
        ],
    )
    reply = engine.handle_command(TgCommand("1", 1, f"/be {pos.ticket}", 4))
    assert reply.startswith("be #")
    updated = engine.broker.positions()[0]
    assert abs(updated.sl - updated.price_open) < 1e-9
    engine.stop()


def test_advisor_memory_includes_prior_turn(tmp_path) -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": (
                        "Noted.\n"
                        '{"action":"hold","symbol":null,"sl":null,"tp":null,"summary":"x"}'
                    )
                }
            }
        ]
    }
    llm = FakeLlm(payload)
    engine = _engine(tmp_path, llm=llm)
    engine.handle_command(TgCommand("1", 1, "/ask first turn", 1))
    engine.handle_command(TgCommand("1", 1, "/ask second turn", 2))
    assert len(llm.sent) == 2
    second_msgs = llm.sent[1][1]["messages"]
    blob = str(second_msgs)
    assert "first turn" in blob


def _fail_send(broker, *, opens: bool = False, closes: bool = False, sltp: bool = False) -> None:
    orig = broker.order_send

    def wrapped(request: dict) -> OrderResult:
        action = int(request.get("action", 0))
        if opens and action == TRADE_ACTION_DEAL and not request.get("position"):
            return OrderResult(retcode=TRADE_RETCODE_INVALID, comment="nope", request=request)
        if closes and action == TRADE_ACTION_DEAL and request.get("position"):
            return OrderResult(retcode=TRADE_RETCODE_INVALID, comment="nope", request=request)
        if sltp and action == TRADE_ACTION_SLTP:
            return OrderResult(
                retcode=TRADE_RETCODE_INVALID_STOPS, comment="stops_level", request=request
            )
        return orig(request)

    broker.order_send = wrapped  # type: ignore[method-assign]


def test_confirm_repreview_halt_file_refuses(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    staged = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    assert "confirm buy EURUSD" in staged
    engine.risk.write_halt_file("operator")
    reply = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert not reply.startswith("sent")
    assert "refused" in reply
    assert not engine.broker.positions()
    engine.stop()


def test_confirm_send_failure_not_hardcoded_success(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    _fail_send(engine.broker, opens=True)
    reply = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert not reply.startswith("sent")
    assert "fail" in reply.lower() or "retcode" in reply.lower()
    assert not engine.broker.positions()
    engine.stop()


def test_sl_tp_success_only_when_applied(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    sent = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert sent.startswith("sent ")
    pos = engine.broker.positions()[0]
    spec = engine.broker.symbol(pos.symbol)
    new_sl = spec.normalize_price(pos.price_open - abs(pos.price_open - pos.sl) * 0.5)
    ok = engine.handle_command(TgCommand("1", 1, f"/sl {pos.ticket} {new_sl}", 3))
    assert "sl #" in ok
    assert abs(engine.broker.positions()[0].sl - new_sl) < spec.point
    other_sl = spec.normalize_price(pos.price_open - abs(pos.price_open - pos.sl) * 0.35)
    other_tp = spec.normalize_price(pos.price_open + abs(pos.tp - pos.price_open) * 0.7)
    _fail_send(engine.broker, sltp=True)
    bad_sl = engine.handle_command(TgCommand("1", 1, f"/sl {pos.ticket} {other_sl}", 4))
    assert "sl #" not in bad_sl
    assert "fail" in bad_sl.lower()
    bad_tp = engine.handle_command(TgCommand("1", 1, f"/tp {pos.ticket} {other_tp}", 5))
    assert "tp #" not in bad_tp
    assert "fail" in bad_tp.lower()
    engine.stop()


def test_close_success_only_when_applied(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    pos = engine.broker.positions()[0]
    _fail_send(engine.broker, closes=True)
    reply = engine.handle_command(TgCommand("1", 1, f"/close {pos.ticket}", 3))
    assert reply != "closed"
    assert "fail" in reply.lower() or "retcode" in reply.lower()
    assert engine.broker.positions()
    engine.stop()

