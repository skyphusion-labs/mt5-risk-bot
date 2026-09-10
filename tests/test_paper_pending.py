from mt5_risk_bot.broker.paper import PaperBroker
from mt5_risk_bot.constants import (
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    ORDER_TYPE_SELL,
    TRADE_ACTION_CLOSE_BY,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_RETCODE_INVALID,
    TRADE_RETCODE_INVALID_ORDER,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_PLACED,
    TRADE_RETCODE_POSITION_CLOSED,
)
from mt5_risk_bot.models import Bar
from mt5_risk_bot.synthetic import generate_bars


def _paper() -> PaperBroker:
    broker = PaperBroker(balance=10_000)
    broker.seed_bars("EURUSD", generate_bars(10, start=1.10, drift=0.0, vol=0.0001, seed=1))
    return broker


def _set_close(broker: PaperBroker, close: float) -> None:
    spec = broker.symbol("EURUSD")
    px = spec.normalize_price(close)
    bars = list(broker.rates("EURUSD", 0, 100))
    last = bars[-1]
    bars.append(Bar(time=last.time + 3600, open=px, high=px, low=px, close=px))
    broker.seed_bars("EURUSD", bars)


def _place(broker: PaperBroker, type_code: int, price: float, volume: float = 0.10):
    spec = broker.symbol("EURUSD")
    px = spec.normalize_price(price)
    return broker.order_send(
        {
            "action": TRADE_ACTION_PENDING,
            "symbol": "EURUSD",
            "volume": volume,
            "type": type_code,
            "price": px,
            "sl": spec.normalize_price(px - 0.005),
            "tp": spec.normalize_price(px + 0.010),
            "magic": 1,
        }
    )


def test_place_buy_limit_then_remove() -> None:
    broker = _paper()
    tick = broker.tick("EURUSD")
    res = _place(broker, ORDER_TYPE_BUY_LIMIT, tick.ask - 0.002)
    assert res.retcode == TRADE_RETCODE_PLACED
    assert len(broker.orders()) == 1
    gone = broker.order_send({"action": TRADE_ACTION_REMOVE, "order": res.order})
    assert gone.ok
    assert broker.orders() == []


def test_buy_limit_below_ask_fills_on_resolve() -> None:
    broker = _paper()
    spec = broker.symbol("EURUSD")
    tick = broker.tick("EURUSD")
    limit = spec.normalize_price(tick.ask - 0.002)
    res = _place(broker, ORDER_TYPE_BUY_LIMIT, limit)
    assert res.retcode == TRADE_RETCODE_PLACED
    assert len(broker.orders()) == 1
    assert broker.resolve_pending("EURUSD") == []
    assert len(broker.orders()) == 1
    _set_close(broker, limit - 0.001)
    filled = broker.resolve_pending("EURUSD")
    assert filled == [res.order]
    assert len(broker.positions()) == 1
    assert broker.orders() == []


def test_buy_stop_above_ask_waits_for_tick() -> None:
    broker = _paper()
    spec = broker.symbol("EURUSD")
    tick = broker.tick("EURUSD")
    stop = spec.normalize_price(tick.ask + 0.002)
    res = _place(broker, ORDER_TYPE_BUY_STOP, stop)
    assert len(broker.orders()) == 1
    assert broker.resolve_pending("EURUSD") == []
    assert broker.positions() == []
    _set_close(broker, stop + 0.001)
    filled = broker.resolve_pending("EURUSD")
    assert filled == [res.order]
    assert len(broker.positions()) == 1
    assert broker.orders() == []


def test_invalid_volume_rejected() -> None:
    broker = _paper()
    tick = broker.tick("EURUSD")
    res = _place(broker, ORDER_TYPE_BUY_LIMIT, tick.ask - 0.002, volume=0.0)
    assert res.retcode == TRADE_RETCODE_INVALID_VOLUME
    assert broker.orders() == []
    assert broker.positions() == []


def test_buy_limit_fills_on_bar() -> None:
    broker = _paper()
    spec = broker.symbol("EURUSD")
    tick = broker.tick("EURUSD")
    limit = spec.normalize_price(tick.ask - 0.002)
    res = _place(broker, ORDER_TYPE_BUY_LIMIT, limit)
    last = broker.rates("EURUSD", 0, 1)[-1]
    bar = Bar(
        time=last.time + 3600,
        open=limit,
        high=limit,
        low=limit - 0.001,
        close=limit - 0.0005,
    )
    broker.on_bar("EURUSD", bar)
    assert broker.orders() == []
    assert len(broker.positions()) == 1
    assert broker.positions()[0].ticket == res.order


def test_sell_limit_fills_when_bid_rises() -> None:
    from mt5_risk_bot.constants import ORDER_TYPE_SELL_LIMIT

    broker = _paper()
    spec = broker.symbol("EURUSD")
    tick = broker.tick("EURUSD")
    limit = spec.normalize_price(tick.bid + 0.002)
    res = broker.order_send(
        {
            "action": TRADE_ACTION_PENDING,
            "symbol": "EURUSD",
            "volume": 0.10,
            "type": ORDER_TYPE_SELL_LIMIT,
            "price": limit,
            "sl": spec.normalize_price(limit + 0.005),
            "tp": spec.normalize_price(limit - 0.010),
            "magic": 1,
        }
    )
    assert res.ok
    assert broker.resolve_pending("EURUSD") == []
    _set_close(broker, limit + 0.001)
    filled = broker.resolve_pending("EURUSD")
    assert filled == [res.order]
    assert len(broker.positions()) == 1
    assert broker.orders() == []


def test_modify_pending_sl_tp() -> None:
    broker = _paper()
    spec = broker.symbol("EURUSD")
    tick = broker.tick("EURUSD")
    limit = spec.normalize_price(tick.ask - 0.002)
    res = _place(broker, ORDER_TYPE_BUY_LIMIT, limit)
    order = broker.orders()[0]
    new_sl = spec.normalize_price(limit - 0.003)
    new_tp = spec.normalize_price(limit + 0.012)
    changed = broker.order_send(
        {
            "action": TRADE_ACTION_MODIFY,
            "order": res.order,
            "price": order.price,
            "sl": new_sl,
            "tp": new_tp,
        }
    )
    assert changed.ok
    updated = broker.orders()[0]
    assert abs(updated.sl - new_sl) < spec.point
    assert abs(updated.tp - new_tp) < spec.point
    assert abs(updated.price - order.price) < spec.point
    missing = broker.order_send({"action": TRADE_ACTION_MODIFY, "order": 999, "price": limit})
    assert missing.retcode == TRADE_RETCODE_INVALID_ORDER
    assert not missing.ok
    new_px = spec.normalize_price(limit - 0.001)
    moved = broker.order_send(
        {
            "action": TRADE_ACTION_MODIFY,
            "order": res.order,
            "price": new_px,
            "sl": new_sl,
            "tp": new_tp,
        }
    )
    assert moved.ok
    assert abs(broker.orders()[0].price - new_px) < spec.point


def _deal(broker: PaperBroker, type_code: int, volume: float = 0.02):
    spec = broker.symbol("EURUSD")
    tick = broker.tick("EURUSD")
    if type_code == ORDER_TYPE_BUY:
        sl = spec.normalize_price(tick.ask - 0.005)
        tp = spec.normalize_price(tick.ask + 0.010)
    else:
        sl = spec.normalize_price(tick.bid + 0.005)
        tp = spec.normalize_price(tick.bid - 0.010)
    return broker.order_send(
        {
            "action": TRADE_ACTION_DEAL,
            "symbol": "EURUSD",
            "volume": volume,
            "type": type_code,
            "sl": sl,
            "tp": tp,
            "magic": 1,
        }
    )


def test_paper_close_by() -> None:
    broker = _paper()
    buy = _deal(broker, ORDER_TYPE_BUY, 0.02)
    sell = _deal(broker, ORDER_TYPE_SELL, 0.02)
    assert buy.ok and sell.ok
    missing = broker.order_send(
        {"action": TRADE_ACTION_CLOSE_BY, "position": buy.order, "position_by": 999}
    )
    assert missing.retcode == TRADE_RETCODE_POSITION_CLOSED
    same = broker.order_send(
        {"action": TRADE_ACTION_CLOSE_BY, "position": buy.order, "position_by": buy.order}
    )
    assert same.retcode == TRADE_RETCODE_INVALID
    check = broker.order_check(
        {"action": TRADE_ACTION_CLOSE_BY, "position": buy.order, "position_by": sell.order}
    )
    assert check.ok
    assert len(broker.positions()) == 2
    done = broker.order_send(
        {"action": TRADE_ACTION_CLOSE_BY, "position": buy.order, "position_by": sell.order}
    )
    assert done.ok
    assert done.volume == 0.02
    assert broker.positions() == []
    leftover_buy = _deal(broker, ORDER_TYPE_BUY, 0.03)
    leftover_sell = _deal(broker, ORDER_TYPE_SELL, 0.01)
    part = broker.order_send(
        {
            "action": TRADE_ACTION_CLOSE_BY,
            "position": leftover_buy.order,
            "position_by": leftover_sell.order,
        }
    )
    assert part.ok
    rows = broker.positions()
    assert len(rows) == 1
    assert abs(rows[0].volume - 0.02) < 1e-12
    tiny_buy = _deal(broker, ORDER_TYPE_BUY, 0.02)
    tiny_sell = _deal(broker, ORDER_TYPE_SELL, 0.011)
    bad = broker.order_send(
        {
            "action": TRADE_ACTION_CLOSE_BY,
            "position": tiny_buy.order,
            "position_by": tiny_sell.order,
        }
    )
    assert bad.retcode == TRADE_RETCODE_INVALID_VOLUME

