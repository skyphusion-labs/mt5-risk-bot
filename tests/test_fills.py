from datetime import datetime, timezone

from mt5_risk_bot.broker.paper import PaperBroker
from mt5_risk_bot.config import BotConfig
from mt5_risk_bot.engine import Engine
from mt5_risk_bot.models import Bar
from mt5_risk_bot.synthetic import generate_bars
from mt5_risk_bot.telegram import TelegramClient, TgCommand


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.updates: list[dict] = []

    def post_json(self, url: str, payload: dict, timeout: float = 10.0, headers=None) -> dict:
        del timeout
        self.sent.append((url, payload))
        if url.endswith("/getUpdates"):
            result = self.updates
            self.updates = []
            return {"ok": True, "result": result}
        return {"ok": True, "result": {"message_id": 1}}


def _engine(tmp_path, *, telegram=None) -> Engine:
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.session.enabled = False
    cfg.risk.max_spread_atr_frac = 10.0
    cfg.risk.halt_file = str(tmp_path / "HALT")
    cfg.strategy.auto = False
    broker = PaperBroker(balance=10_000)
    broker.seed_bars("EURUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=3))
    broker.seed_bars("GBPUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=4))
    return Engine(
        cfg,
        broker,
        halt_dir=str(tmp_path),
        telegram=telegram,
        now_fn=lambda: datetime(2024, 1, 3, 12, tzinfo=timezone.utc),
    )


def _push_close(engine: Engine, symbol: str, close: float, dt: int = 3600) -> None:
    last = engine.broker.rates(symbol, engine.cfg.strategy.timeframe_id, 1)[-1]
    bars = engine.broker.rates(symbol, engine.cfg.strategy.timeframe_id, 200)
    spec = engine.broker.symbol(symbol)
    px = spec.normalize_price(close)
    engine.broker.seed_bars(
        symbol,
        bars
        + [
            Bar(
                time=last.time + dt,
                open=px,
                high=max(px, last.close),
                low=min(px, last.close),
                close=px,
            )
        ],
    )


def test_limit_buy_stages_pending_then_fills(tmp_path) -> None:
    tr = FakeTransport()
    tg = TelegramClient(
        token="t",
        chat_id="1",
        transport=tr,
        notify_events=frozenset({"start", "stop", "open", "close", "halt", "pending"}),
    )
    engine = _engine(tmp_path, telegram=tg)
    engine.start()
    tick = engine.broker.tick("EURUSD")
    spec = engine.broker.symbol("EURUSD")
    limit = spec.normalize_price(tick.ask - 0.002)
    sl = spec.normalize_price(limit - 0.005)
    tp = spec.normalize_price(limit + 0.010)
    reply = engine.handle_command(
        TgCommand("1", 1, f"/buy EURUSD limit={limit} sl={sl} tp={tp}", 1)
    )
    assert "confirm buy EURUSD" in reply
    assert "limit" in reply
    sent = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert sent.startswith("sent")
    assert engine.broker.orders()
    assert not engine.broker.positions()
    listed = engine.handle_command(TgCommand("1", 1, "/orders", 3))
    assert "EURUSD" in listed
    ticket = engine.broker.orders()[0].ticket
    assert str(ticket) in listed
    _push_close(engine, "EURUSD", limit - 0.001)
    engine.step_all()
    assert engine.broker.orders() == []
    assert engine.broker.positions()
    texts = [payload.get("text", "") for _, payload in tr.sent]
    blob = " ".join(texts)
    assert "PENDING" in blob or "FILL" in blob or "OPEN" in blob
    engine.stop()


def test_cancel_ticket_removes_pending(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    tick = engine.broker.tick("EURUSD")
    limit = tick.ask - 0.002
    engine.handle_command(TgCommand("1", 1, f"/buy EURUSD limit={limit}", 1))
    sent = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert sent.startswith("sent")
    ticket = engine.broker.orders()[0].ticket
    reply = engine.handle_command(TgCommand("1", 1, f"/cancel {ticket}", 3))
    assert "cancelled" in reply
    assert str(ticket) in reply
    assert engine.broker.orders() == []
    engine.stop()


def test_halt_cancels_pending_and_positions(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    sent = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert sent.startswith("sent")
    assert engine.broker.positions()
    tick = engine.broker.tick("GBPUSD")
    limit = tick.ask - 0.002
    engine.handle_command(TgCommand("1", 1, f"/buy GBPUSD limit={limit}", 3))
    sent2 = engine.handle_command(TgCommand("1", 1, "/confirm", 4))
    assert sent2.startswith("sent")
    assert engine.broker.orders()
    engine.handle_command(TgCommand("1", 1, "/halt", 5))
    assert engine.broker.orders() == []
    assert not engine.broker.positions()
    engine.stop()


def test_limit_and_stop_together_refused(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    reply = engine.handle_command(TgCommand("1", 1, "/buy EURUSD limit=1.0 stop=1.2", 1))
    assert "not both" in reply
    engine.stop()


def test_market_sl_notifies_journal_and_telegram(tmp_path) -> None:
    tr = FakeTransport()
    tg = TelegramClient(
        token="t",
        chat_id="1",
        transport=tr,
        notify_events=frozenset({"start", "stop", "open", "close", "halt", "pending"}),
    )
    engine = _engine(tmp_path, telegram=tg)
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    sent = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert sent.startswith("sent")
    pos = engine.broker.positions()[0]
    _push_close(engine, "EURUSD", pos.sl - 0.005)
    engine.step_all()
    assert not engine.broker.positions()
    events = [rec.get("event") for rec in engine.journal.tail(30)]
    assert "close" in events or "fill" in events
    texts = [payload.get("text", "") for _, payload in tr.sent]
    blob = " ".join(texts).lower()
    assert "close" in blob or "fill" in blob
    engine.stop()


def test_step_all_fill_then_stop(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    tick = engine.broker.tick("EURUSD")
    limit = tick.ask - 0.01
    sl = limit - 0.01
    tp = limit + 0.015
    staged = engine.handle_command(
        TgCommand("1", 1, f"/buy EURUSD limit={limit} sl={sl} tp={tp}", 1)
    )
    assert "confirm buy EURUSD" in staged
    sent = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert sent.startswith("sent")
    assert engine.broker.orders()
    assert not engine.broker.positions()
    _push_close(engine, "EURUSD", limit - 0.002)
    engine.step_all()
    assert engine.broker.orders() == []
    assert engine.broker.positions()
    pos = engine.broker.positions()[0]
    _push_close(engine, "EURUSD", pos.sl - 0.005, dt=7200)
    engine.step_all()
    assert not engine.broker.positions()
    engine.stop()


def test_sell_stop_and_buy_limit_price_rules(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    tick = engine.broker.tick("EURUSD")
    assert "below ask" in engine.handle_command(
        TgCommand("1", 1, f"/buy EURUSD limit={tick.ask + 0.01}", 1)
    )
    assert "above ask" in engine.handle_command(
        TgCommand("1", 1, f"/buy EURUSD stop={tick.ask - 0.01}", 2)
    )
    assert "above bid" in engine.handle_command(
        TgCommand("1", 1, f"/sell EURUSD limit={tick.bid - 0.01}", 3)
    )
    assert "below bid" in engine.handle_command(
        TgCommand("1", 1, f"/sell EURUSD stop={tick.bid + 0.01}", 4)
    )
    engine.stop()


def test_orders_empty_message(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    assert "no pending" in engine.handle_command(TgCommand("1", 1, "/orders", 1))
    engine.stop()


def test_sl_tp_modify_working_order(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine.start()
    tick = engine.broker.tick("EURUSD")
    spec = engine.broker.symbol("EURUSD")
    limit = spec.normalize_price(tick.ask - 0.002)
    sl = spec.normalize_price(limit - 0.005)
    tp = spec.normalize_price(limit + 0.010)
    engine.handle_command(
        TgCommand("1", 1, f"/buy EURUSD limit={limit} sl={sl} tp={tp}", 1)
    )
    sent = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    assert sent.startswith("sent")
    order = engine.broker.orders()[0]
    new_sl = spec.normalize_price(limit - 0.003)
    new_tp = spec.normalize_price(limit + 0.012)
    sl_reply = engine.handle_command(TgCommand("1", 1, f"/sl {order.ticket} {new_sl}", 3))
    assert f"sl #{order.ticket}" in sl_reply
    assert abs(engine.broker.orders()[0].sl - new_sl) < spec.point
    tp_reply = engine.handle_command(TgCommand("1", 1, f"/tp {order.ticket} {new_tp}", 4))
    assert f"tp #{order.ticket}" in tp_reply
    assert abs(engine.broker.orders()[0].tp - new_tp) < spec.point
    assert not engine.broker.positions()
    bad = engine.handle_command(
        TgCommand("1", 1, f"/sl {order.ticket} {spec.normalize_price(limit + 0.001)}", 5)
    )
    assert "sl #" not in bad
    assert "sl < entry" in bad or "fail" in bad.lower()
    engine.stop()


def test_utc_day_roll_sends_recap_not_a_trade(tmp_path) -> None:
    tr = FakeTransport()
    tg = TelegramClient(
        token="t",
        chat_id="1",
        transport=tr,
        notify_events=frozenset(
            {"start", "stop", "open", "close", "halt", "pending", "recap"}
        ),
    )
    clock = [datetime(2024, 1, 3, 12, tzinfo=timezone.utc)]
    cfg = BotConfig()
    cfg.journal_path = str(tmp_path / "j.jsonl")
    cfg.session.enabled = False
    cfg.risk.max_spread_atr_frac = 10.0
    cfg.risk.halt_file = str(tmp_path / "HALT")
    cfg.strategy.auto = False
    broker = PaperBroker(balance=10_000)
    broker.seed_bars("EURUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=3))
    engine = Engine(
        cfg,
        broker,
        halt_dir=str(tmp_path),
        telegram=tg,
        now_fn=lambda: clock[0],
    )
    engine.start()
    engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
    engine.handle_command(TgCommand("1", 1, "/confirm", 2))
    npos = len(engine.broker.positions())
    clock[0] = datetime(2024, 1, 4, 0, 5, tzinfo=timezone.utc)
    engine.step_all()
    assert len(engine.broker.positions()) == npos
    texts = [payload.get("text", "") for _, payload in tr.sent]
    blob = "\n".join(texts)
    assert "RECAP 2024-01-03" in blob
    assert "day_start=" in blob
    recaps = [t for t in texts if t.startswith("RECAP")]
    assert len(recaps) == 1
    engine.step_all()
    texts2 = [payload.get("text", "") for _, payload in tr.sent]
    recaps2 = [t for t in texts2 if t.startswith("RECAP")]
    assert len(recaps2) == 1
    engine.stop()
