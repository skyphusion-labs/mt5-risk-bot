from types import SimpleNamespace

from mt5_risk_bot.broker.mt5_live import Mt5Broker
from mt5_risk_bot.constants import (
    ORDER_FILLING_IOC,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_SELL_LIMIT,
    TRADE_ACTION_CLOSE_BY,
    TRADE_ACTION_MODIFY,
    TRADE_ACTION_REMOVE,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_INVALID_FILL,
    TRADE_RETCODE_INVALID_ORDER,
)
from mt5_risk_bot.models import Side


def _nt(**kw):
    return SimpleNamespace(**kw, _asdict=lambda: dict(kw))


class FakeMt5:
    TRADE_RETCODE_DONE = TRADE_RETCODE_DONE
    TRADE_RETCODE_INVALID_FILL = TRADE_RETCODE_INVALID_FILL

    def __init__(self, *, netting: bool = False) -> None:
        self.netting = netting
        self.inited = False
        self.init_count = 0
        self.disconnected = False
        self.force_ipc = False
        self.dead_after_init = False
        self.sends: list[dict] = []
        self.fill_fail_once = False
        self.order_rows: list | None = [
            _nt(
                ticket=11,
                symbol="EURUSD",
                type=ORDER_TYPE_BUY_LIMIT,
                volume_current=0.2,
                volume_initial=0.2,
                price_open=1.08,
                sl=1.07,
                tp=1.10,
                magic=20260909,
                comment="lim",
                time_setup=99,
            ),
            _nt(
                ticket=12,
                symbol="GBPUSD",
                type=ORDER_TYPE_SELL_LIMIT,
                volume_current=0.1,
                price_open=1.27,
                sl=1.28,
                tp=1.25,
                magic=1,
                comment="",
                time=50,
            ),
        ]
        self.position_rows: list = [
            _nt(
                ticket=7,
                symbol="EURUSD",
                type=0,
                volume=0.1,
                price_open=1.1,
                sl=1.09,
                tp=1.12,
                price_current=1.11,
                profit=10.0,
                swap=0.0,
                magic=20260909,
                comment="x",
                time=1,
                identifier=7,
            )
        ]

    def initialize(self, *args, **kwargs) -> bool:
        del args, kwargs
        self.inited = True
        self.init_count += 1
        self.disconnected = False
        self.force_ipc = False
        if self.dead_after_init:
            self.disconnected = True
        return True

    def login(self, *args, **kwargs) -> bool:
        del args, kwargs
        return True

    def shutdown(self) -> None:
        self.inited = False

    def last_error(self):
        if self.force_ipc or self.disconnected or not self.inited:
            return (-10001, "IPC send failed")
        return (1, "ok")

    def _dead(self) -> bool:
        return self.disconnected or not self.inited

    def terminal_info(self):
        return _nt(trade_allowed=True)

    def account_info(self):
        if self._dead():
            return None
        return _nt(
            login=1,
            trade_mode=0,
            leverage=100,
            balance=10_000.0,
            equity=10_000.0,
            margin=0.0,
            margin_free=10_000.0,
            profit=0.0,
            currency="USD",
            trade_allowed=True,
            trade_expert=True,
            server="Demo",
            name="t",
            fifo_close=False,
            credit=0.0,
            margin_level=0.0,
        )

    def symbol_select(self, name, enable=True) -> bool:
        del name, enable
        return True

    def symbol_info(self, name):
        return _nt(
            name=name,
            digits=5,
            point=0.00001,
            trade_tick_size=0.00001,
            trade_tick_value=1.0,
            trade_contract_size=100000.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_stops_level=10,
            trade_freeze_level=0,
            filling_mode=2,
            currency_base="EUR",
            currency_profit="USD",
            currency_margin="USD",
            trade_mode=4,
            visible=True,
            spread=10,
        )

    def symbol_info_tick(self, name):
        del name
        if self._dead():
            return None
        return _nt(time=1, bid=1.1, ask=1.1001, last=1.1, volume=1)

    def copy_rates_from_pos(self, name, tf, start, count):
        del name, tf, start
        return [
            _nt(time=i, open=1.1, high=1.11, low=1.09, close=1.105, tick_volume=10, spread=10, real_volume=0)
            for i in range(count)
        ]

    def orders_get(self):
        if self._dead():
            return None
        return self.order_rows

    def positions_get(self):
        if self._dead():
            return None
        return self.position_rows

    def order_check(self, request):
        return _nt(retcode=0, comment="Done", deal=0, order=0, volume=request.get("volume", 0), price=1.1, bid=1.1, ask=1.1)

    def order_send(self, request):
        if self._dead():
            return None
        self.sends.append(request)
        if self.fill_fail_once:
            self.fill_fail_once = False
            return _nt(
                retcode=TRADE_RETCODE_INVALID_FILL,
                comment="invalid fill",
                deal=0,
                order=0,
                volume=0,
                price=0,
                bid=1.1,
                ask=1.1,
            )
        action = int(request.get("action", 0))
        if action == TRADE_ACTION_MODIFY:
            ticket = int(request.get("order") or 0)
            rows = list(self.order_rows or [])
            for i, row in enumerate(rows):
                if int(row.ticket) != ticket:
                    continue
                data = dict(row._asdict())
                if "price" in request:
                    data["price_open"] = request["price"]
                if "sl" in request:
                    data["sl"] = request["sl"]
                if "tp" in request:
                    data["tp"] = request["tp"]
                rows[i] = _nt(**data)
                self.order_rows = rows
                break
            return _nt(
                retcode=TRADE_RETCODE_DONE,
                comment="Done",
                deal=0,
                order=ticket,
                volume=request.get("volume", 0),
                price=request.get("price", 1.1),
                bid=1.1,
                ask=1.1,
            )
        if action == TRADE_ACTION_REMOVE:
            ticket = int(request.get("order") or 0)
            rows = list(self.order_rows or [])
            self.order_rows = [row for row in rows if int(row.ticket) != ticket]
            return _nt(
                retcode=TRADE_RETCODE_DONE,
                comment="Done",
                deal=0,
                order=ticket,
                volume=request.get("volume", 0),
                price=1.1,
                bid=1.1,
                ask=1.1,
            )
        if action == TRADE_ACTION_CLOSE_BY:
            if self.netting:
                return _nt(
                    retcode=TRADE_RETCODE_INVALID_ORDER,
                    comment="hedge only",
                    deal=0,
                    order=0,
                    volume=0,
                    price=0,
                    bid=1.1,
                    ask=1.1,
                )
            gone = {
                int(request.get("position") or 0),
                int(request.get("position_by") or 0),
            }
            self.position_rows = [row for row in self.position_rows if int(row.ticket) not in gone]
            return _nt(
                retcode=TRADE_RETCODE_DONE,
                comment="Done",
                deal=int(request.get("position") or 0),
                order=int(request.get("position") or 0),
                volume=request.get("volume", 0),
                price=1.1,
                bid=1.1,
                ask=1.1,
            )
        return _nt(
            retcode=TRADE_RETCODE_DONE,
            comment="Done",
            deal=1,
            order=2,
            volume=request.get("volume", 0),
            price=1.1,
            bid=1.1,
            ask=1.1,
        )


def test_adapter_maps_account_and_positions() -> None:
    fake = FakeMt5()
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    acct = broker.account()
    assert acct.login == 1
    assert acct.currency == "USD"
    spec = broker.symbol("EURUSD")
    assert spec.volume_min == 0.01
    tick = broker.tick("EURUSD")
    assert tick.ask > tick.bid
    rates = broker.rates("EURUSD", 16385, 3)
    assert len(rates) == 3
    pos = broker.positions(magic=20260909)
    assert len(pos) == 1
    assert pos[0].side is Side.BUY
    broker.disconnect()
    assert fake.inited is False


def test_adapter_maps_orders() -> None:
    fake = FakeMt5()
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    ours = broker.orders(magic=20260909)
    assert len(ours) == 1
    order = ours[0]
    assert order.ticket == 11
    assert order.symbol == "EURUSD"
    assert order.side is Side.BUY
    assert order.volume == 0.2
    assert order.price == 1.08
    assert order.sl == 1.07
    assert order.tp == 1.10
    assert order.type_code == ORDER_TYPE_BUY_LIMIT
    assert order.time == 99
    all_orders = broker.orders()
    assert len(all_orders) == 2
    assert all_orders[1].side is Side.SELL
    assert all_orders[1].ticket == 12
    fake.order_rows = []
    assert broker.orders() == []
    fake.order_rows = None
    try:
        broker.orders()
        raise AssertionError("expected RuntimeError on None orders_get")
    except RuntimeError as exc:
        assert "orders_get" in str(exc)
    broker.disconnect()


def test_orders_without_orders_get() -> None:
    assert Mt5Broker().orders() == []
    broker = Mt5Broker(mt5=object())
    assert broker.orders() == []


def test_invalid_fill_retries() -> None:
    fake = FakeMt5()
    fake.fill_fail_once = True
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    result = broker.order_send({"action": 1, "type_filling": 0, "volume": 0.01})
    assert result.ok
    assert len(fake.sends) == 2
    assert fake.sends[1]["type_filling"] == ORDER_FILLING_IOC


def test_connect_is_reentrant() -> None:
    fake = FakeMt5()
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    broker.connect()
    assert fake.init_count == 2
    assert fake.inited is True


def test_ensure_connected_skips_when_healthy() -> None:
    fake = FakeMt5()
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    broker.ensure_connected()
    assert fake.init_count == 1
    fake.disconnected = True
    broker.ensure_connected()
    assert fake.init_count == 2
    assert fake.disconnected is False


def test_reconnect_after_account_info_none() -> None:
    fake = FakeMt5()
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    fake.disconnected = True
    acct = broker.account()
    assert acct.login == 1
    assert fake.init_count == 2
    assert fake.inited is True
    tick = broker.tick("EURUSD")
    assert tick.ask > tick.bid
    assert broker.positions()
    assert broker.orders()


def test_reconnect_on_ipc_last_error() -> None:
    fake = FakeMt5()
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    fake.force_ipc = True
    acct = broker.account()
    assert acct.login == 1
    assert fake.init_count == 2
    assert fake.force_ipc is False


def test_reconnect_once_then_raises() -> None:
    fake = FakeMt5()
    fake.dead_after_init = True
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    assert fake.init_count == 1
    try:
        broker.account()
        raise AssertionError("expected RuntimeError after one reconnect")
    except RuntimeError as exc:
        assert "account_info" in str(exc)
    assert fake.init_count == 2


def test_order_send_reconnects_on_none() -> None:
    fake = FakeMt5()
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    fake.disconnected = True
    result = broker.order_send({"action": 1, "type_filling": 0, "volume": 0.01})
    assert result.ok
    assert fake.init_count == 2
    assert len(fake.sends) == 1


# Paper mutates in tests/test_paper_pending.py. Live is a pass-through:
# MODIFY / REMOVE / CLOSE_BY return 10009 (TRADE_RETCODE_DONE) and orders()
# still maps whatever orders_get returns after the send.


def test_adapter_modify_pending() -> None:
    fake = FakeMt5()
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    result = broker.order_send(
        {
            "action": TRADE_ACTION_MODIFY,
            "order": 11,
            "price": 1.079,
            "sl": 1.069,
            "tp": 1.101,
        }
    )
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.ok
    assert fake.sends[-1]["action"] == TRADE_ACTION_MODIFY
    ours = broker.orders(magic=20260909)
    assert len(ours) == 1
    assert ours[0].ticket == 11
    assert ours[0].price == 1.079
    assert ours[0].sl == 1.069
    assert ours[0].tp == 1.101
    all_orders = broker.orders()
    assert len(all_orders) == 2
    assert all_orders[1].ticket == 12
    assert all_orders[1].price == 1.27
    broker.disconnect()


def test_adapter_remove_pending() -> None:
    fake = FakeMt5()
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    result = broker.order_send({"action": TRADE_ACTION_REMOVE, "order": 11})
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.ok
    remaining = broker.orders()
    assert len(remaining) == 1
    assert remaining[0].ticket == 12
    assert remaining[0].side is Side.SELL
    assert remaining[0].type_code == ORDER_TYPE_SELL_LIMIT
    assert broker.orders(magic=20260909) == []
    broker.disconnect()


def test_adapter_close_by() -> None:
    fake = FakeMt5()
    fake.position_rows.append(
        _nt(
            ticket=8,
            symbol="EURUSD",
            type=1,
            volume=0.1,
            price_open=1.11,
            sl=1.12,
            tp=1.09,
            price_current=1.1,
            profit=-10.0,
            swap=0.0,
            magic=20260909,
            comment="y",
            time=2,
            identifier=8,
        )
    )
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    assert len(broker.positions()) == 2
    result = broker.order_send(
        {
            "action": TRADE_ACTION_CLOSE_BY,
            "position": 7,
            "position_by": 8,
        }
    )
    assert result.retcode == TRADE_RETCODE_DONE
    assert result.ok
    assert broker.positions() == []
    mapped = broker.orders()
    assert len(mapped) == 2
    assert mapped[0].ticket == 11
    assert mapped[1].ticket == 12
    broker.disconnect()


def test_adapter_close_by_refused_on_netting() -> None:
    fake = FakeMt5(netting=True)
    fake.position_rows.append(
        _nt(
            ticket=8,
            symbol="EURUSD",
            type=1,
            volume=0.1,
            price_open=1.11,
            sl=1.12,
            tp=1.09,
            price_current=1.1,
            profit=-10.0,
            swap=0.0,
            magic=20260909,
            comment="y",
            time=2,
            identifier=8,
        )
    )
    broker = Mt5Broker(mt5=fake)
    broker.connect()
    assert len(broker.positions()) == 2
    result = broker.order_send(
        {
            "action": TRADE_ACTION_CLOSE_BY,
            "position": 7,
            "position_by": 8,
        }
    )
    assert result.retcode == TRADE_RETCODE_INVALID_ORDER
    assert not result.ok
    assert result.comment == "hedge only"
    assert fake.sends[-1]["action"] == TRADE_ACTION_CLOSE_BY
    remaining = broker.positions()
    assert len(remaining) == 2
    assert {row.ticket for row in remaining} == {7, 8}
    broker.disconnect()
