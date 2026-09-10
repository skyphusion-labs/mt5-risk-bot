from types import SimpleNamespace

from mt5_risk_bot.broker.mt5_live import Mt5Broker
from mt5_risk_bot.constants import (
    ORDER_FILLING_IOC,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_SELL_LIMIT,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_INVALID_FILL,
)
from mt5_risk_bot.models import Side


def _nt(**kw):
    return SimpleNamespace(**kw, _asdict=lambda: dict(kw))


class FakeMt5:
    TRADE_RETCODE_DONE = TRADE_RETCODE_DONE
    TRADE_RETCODE_INVALID_FILL = TRADE_RETCODE_INVALID_FILL

    def __init__(self) -> None:
        self.inited = False
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

    def initialize(self, *args, **kwargs) -> bool:
        del args, kwargs
        self.inited = True
        return True

    def login(self, *args, **kwargs) -> bool:
        del args, kwargs
        return True

    def shutdown(self) -> None:
        self.inited = False

    def last_error(self):
        return (1, "err")

    def terminal_info(self):
        return _nt(trade_allowed=True)

    def account_info(self):
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
        return _nt(time=1, bid=1.1, ask=1.1001, last=1.1, volume=1)

    def copy_rates_from_pos(self, name, tf, start, count):
        del name, tf, start
        return [
            _nt(time=i, open=1.1, high=1.11, low=1.09, close=1.105, tick_volume=10, spread=10, real_volume=0)
            for i in range(count)
        ]

    def orders_get(self):
        return self.order_rows

    def positions_get(self):
        return [
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

    def order_check(self, request):
        return _nt(retcode=0, comment="Done", deal=0, order=0, volume=request.get("volume", 0), price=1.1, bid=1.1, ask=1.1)

    def order_send(self, request):
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
    assert broker.orders() == []
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
