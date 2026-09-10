import threading
import time
from pathlib import Path

import pytest

from mt5_risk_bot.broker import broker_for
from mt5_risk_bot.broker.mt4_live import (
    FileBridge,
    Mt4Broker,
    decode,
    encode,
    parse_rows,
)
from mt5_risk_bot.config import BotConfig, Mt4Config, load_config, resolve_mt4_files_dir
from mt5_risk_bot.constants import TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED
from mt5_risk_bot.models import MarketOrder, Side, WorkingOrder


class FakeMt4:
    def __init__(self) -> None:
        self.positions: list[dict] = []
        self.orders: list[dict] = []
        self.n = 0
        self.bid = 1.1000
        self.ask = 1.1002
        self.ping_ok = True

    def call(self, op: str, payload: dict) -> dict:
        if op == "ping":
            return {"ok": self.ping_ok, "error": "" if self.ping_ok else "down"}
        if op == "account":
            return {
                "ok": True,
                "login": 1,
                "balance": 10000.0,
                "equity": 10000.0,
                "margin": 0.0,
                "margin_free": 10000.0,
                "profit": 0.0,
                "currency": "USD",
                "leverage": 100,
                "trade_mode": 0,
                "trade_allowed": 1,
                "trade_expert": 1,
                "name": "demo",
                "server": "Demo",
            }
        if op == "tick":
            return {"ok": True, "bid": self.bid, "ask": self.ask, "time": 1}
        if op == "symbol":
            return {
                "ok": True,
                "digits": 5,
                "point": 0.00001,
                "volume_min": 0.01,
                "volume_max": 100.0,
                "volume_step": 0.01,
                "tick_value": 1.0,
                "tick_size": 0.00001,
                "contract_size": 100000,
                "stops_level": 10,
                "freeze_level": 0,
                "spread": 2,
            }
        if op == "select":
            return {"ok": True}
        if op == "rates":
            return {
                "ok": True,
                "bars": [
                    {"time": 1, "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105, "volume": 1}
                ],
            }
        if op == "positions":
            return {"ok": True, "rows": list(self.positions)}
        if op == "orders":
            return {"ok": True, "rows": list(self.orders)}
        if op == "check_market":
            return {"ok": True, "ticket": 0, "price": self.ask, "volume": payload["volume"]}
        if op == "market":
            self.n += 1
            row = {
                "ticket": self.n,
                "symbol": payload["symbol"],
                "side": payload["side"],
                "volume": payload["volume"],
                "price_open": self.ask if payload["side"] == "buy" else self.bid,
                "sl": payload.get("sl", 0),
                "tp": payload.get("tp", 0),
                "price_current": self.ask,
                "profit": 0.0,
                "magic": payload.get("magic", 0),
                "comment": payload.get("comment", ""),
            }
            self.positions.append(row)
            return {"ok": True, "ticket": self.n, "volume": payload["volume"], "price": row["price_open"]}
        if op == "check_working":
            return {"ok": True, "ticket": 0}
        if op == "working":
            self.n += 1
            row = {
                "ticket": self.n,
                "symbol": payload["symbol"],
                "side": payload["side"],
                "kind": payload["kind"],
                "volume": payload["volume"],
                "price": payload["price"],
                "sl": payload.get("sl", 0),
                "tp": payload.get("tp", 0),
                "magic": payload.get("magic", 0),
                "comment": payload.get("comment", ""),
            }
            self.orders.append(row)
            return {"ok": True, "ticket": self.n}
        if op == "cancel":
            before = len(self.orders)
            self.orders = [o for o in self.orders if o["ticket"] != payload["ticket"]]
            if len(self.orders) == before:
                return {"ok": False, "retcode": 4108, "error": "not_found"}
            return {"ok": True, "ticket": payload["ticket"]}
        if op == "close":
            self.positions = [p for p in self.positions if p["ticket"] != payload["ticket"]]
            return {"ok": True, "ticket": payload["ticket"]}
        if op == "modify_position":
            for p in self.positions:
                if p["ticket"] == payload["ticket"]:
                    p["sl"] = payload["sl"]
                    p["tp"] = payload["tp"]
                    return {"ok": True, "ticket": payload["ticket"]}
            return {"ok": False, "retcode": 4108, "error": "not_found"}
        if op == "modify_working":
            for o in self.orders:
                if o["ticket"] == payload["ticket"]:
                    if "price" in payload:
                        o["price"] = payload["price"]
                    if "sl" in payload:
                        o["sl"] = payload["sl"]
                    if "tp" in payload:
                        o["tp"] = payload["tp"]
                    return {"ok": True, "ticket": payload["ticket"]}
            return {"ok": False, "retcode": 4108, "error": "not_found"}
        if op == "close_by":
            return {"ok": True, "ticket": payload["ticket"]}
        return {"ok": False, "error": op}


def test_encode_decode_roundtrip() -> None:
    text = encode("market", {"symbol": "EURUSD", "volume": 0.1}, 7)
    got = decode(text)
    assert got["id"] == 7
    assert got["op"] == "market"
    assert got["symbol"] == "EURUSD"
    assert got["volume"] == 0.1


def test_parse_rows_from_wire() -> None:
    text = "id=1\nok=1\nn=2\nrow0=1|EURUSD|buy|0.1|1.1|1.09|1.12|1.11|10|9|bot|0|1\n" "row1=2|GBPUSD|sell|0.2|1.2|1.21|1.18|1.19|-4|9||0|2\n"
    data = decode(text)
    rows = parse_rows(data, ("ticket", "symbol", "side", "volume", "price_open", "sl", "tp", "price_current", "profit", "magic", "comment", "swap", "time"))
    assert len(rows) == 2
    assert rows[0]["symbol"] == "EURUSD"
    assert rows[0]["side"] == "buy"
    assert rows[1]["volume"] == 0.2
    assert rows[1]["comment"] == ""


def test_mt4_market_working_cancel() -> None:
    fake = FakeMt4()
    br = Mt4Broker(fake.call)
    br.connect()
    br.ensure_connected()
    acct = br.account()
    assert acct.equity == 10000
    assert acct.margin_free == 10000
    spec = br.symbol("EURUSD")
    assert spec.volume_min == 0.01
    assert spec.trade_contract_size == 100000
    tick = br.tick("EURUSD")
    assert tick.ask > tick.bid
    assert br.select_symbol("EURUSD")
    check = br.check_market(MarketOrder(symbol="EURUSD", side=Side.BUY, volume=0.1, sl=1.09, tp=1.12, magic=1))
    assert check.ok
    assert check.order == 0
    assert br.positions() == []
    sent = br.market(MarketOrder(symbol="EURUSD", side=Side.BUY, volume=0.1, sl=1.09, tp=1.12, magic=1))
    assert sent.ok
    assert sent.retcode == TRADE_RETCODE_DONE
    assert br.positions()
    wo_check = br.check_working(
        WorkingOrder(symbol="EURUSD", side=Side.BUY, kind="limit", volume=0.1, price=1.08, sl=1.07, tp=1.10)
    )
    assert wo_check.ok
    assert wo_check.order == 0
    wo = br.working(
        WorkingOrder(
            symbol="EURUSD",
            side=Side.BUY,
            kind="limit",
            volume=0.1,
            price=1.08,
            sl=1.07,
            tp=1.10,
        )
    )
    assert wo.ok
    assert wo.retcode == TRADE_RETCODE_PLACED
    assert br.orders()[0].kind == "limit"
    moved = br.modify_working(wo.order, price=1.075, sl=1.065, tp=1.11)
    assert moved.ok
    assert br.orders()[0].price == 1.075
    br.cancel(wo.order)
    assert br.orders() == []
    pos = br.positions()[0]
    br.modify_position(pos.ticket, 1.085, 1.13, symbol="EURUSD")
    assert br.positions()[0].sl == 1.085
    closed = br.close_position(pos.ticket, symbol="EURUSD", side="buy", volume=0.1, price=tick.bid)
    assert closed.ok
    assert br.positions() == []
    br.disconnect()


def test_mt4_rates() -> None:
    br = Mt4Broker(FakeMt4().call)
    bars = br.rates("EURUSD", "H1", 10)
    assert len(bars) == 1
    assert bars[0].close == 1.105
    assert bars[0].tick_volume == 1


def test_mt4_close_by_and_reject() -> None:
    fake = FakeMt4()
    br = Mt4Broker(fake.call)
    res = br.close_by(1, 2, "EURUSD")
    assert res.ok
    missing = br.cancel(99)
    assert not missing.ok
    assert missing.retcode != TRADE_RETCODE_DONE


def test_mt4_ping_fail() -> None:
    fake = FakeMt4()
    fake.ping_ok = False
    br = Mt4Broker(fake.call)
    with pytest.raises(RuntimeError, match="down"):
        br.connect()


def test_mt4_market_maps_mt4_error() -> None:
    def call(op: str, payload: dict) -> dict:
        del op, payload
        return {"ok": 0, "retcode": 130, "error": "invalid_stops"}

    res = Mt4Broker(call).market(MarketOrder(symbol="EURUSD", side=Side.BUY, volume=0.1, sl=1.1, tp=1.2))
    assert not res.ok
    assert res.comment == "invalid_stops"


def test_file_bridge_roundtrip(tmp_path: Path) -> None:
    stop = threading.Event()

    def ea() -> None:
        req = tmp_path / "mt4_risk_bot.req"
        res = tmp_path / "mt4_risk_bot.res"
        while not stop.is_set():
            if req.exists():
                try:
                    text = req.read_text(encoding="ascii")
                    req.unlink()
                except OSError:
                    time.sleep(0.01)
                    continue
                data = decode(text)
                if data.get("op") == "ping":
                    body = f"id={data['id']}\nok=1\ntime=1\n"
                elif data.get("op") == "rates":
                    body = f"id={data['id']}\nok=1\nn=1\nrow0=1|1.1|1.11|1.09|1.105|9\n"
                else:
                    body = f"id={data['id']}\nok=0\nerror=unsupported\n"
                res.write_text(body, encoding="ascii")
            time.sleep(0.01)

    t = threading.Thread(target=ea, daemon=True)
    t.start()
    try:
        bridge = FileBridge(tmp_path, timeout_sec=2.0)
        ping = bridge.call("ping", {})
        assert ping["ok"] == 1
        br = Mt4Broker(bridge.call)
        bars = br.rates("EURUSD", "H1", 4)
        assert len(bars) == 1
        assert bars[0].close == 1.105
    finally:
        stop.set()
        t.join(timeout=1.0)


def test_file_bridge_timeout(tmp_path: Path) -> None:
    bridge = FileBridge(tmp_path, timeout_sec=0.15)
    with pytest.raises(RuntimeError, match="timeout"):
        bridge.call("ping", {})


def test_broker_for_mt4_requires_files_dir() -> None:
    cfg = BotConfig(mode="mt4")
    with pytest.raises(RuntimeError, match="files_dir"):
        broker_for(cfg)


def test_broker_for_mt4(tmp_path: Path) -> None:
    cfg = BotConfig(mode="mt4", mt4=Mt4Config(files_dir=str(tmp_path), timeout_ms=200))
    br = broker_for(cfg)
    assert isinstance(br, Mt4Broker)


def test_load_config_mt4(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "c.toml"
    path.write_text(
        '[account]\nmode = "mt4"\n[mt4]\nfiles_dir = "/tmp/mt4"\ntimeout_ms = 3000\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("ACCOUNT_MODE", raising=False)
    monkeypatch.delenv("MT4_FILES_DIR", raising=False)
    cfg = load_config(path)
    assert cfg.mode == "mt4"
    assert cfg.mt4.files_dir == "/tmp/mt4"
    assert cfg.mt4.timeout_ms == 3000


def test_account_mode_env_mt4(monkeypatch) -> None:
    monkeypatch.setenv("ACCOUNT_MODE", "mt4")
    monkeypatch.setenv("MT4_FILES_DIR", "/tmp/common")
    cfg = load_config()
    assert cfg.mode == "mt4"
    assert cfg.mt4.files_dir == "/tmp/common"


def test_validate_rejects_unknown_mode() -> None:
    cfg = BotConfig(mode="ibkr")
    with pytest.raises(ValueError, match="paper, mt5, or mt4"):
        cfg.validate()


def test_resolve_mt4_expands_appdata(monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", r"C:\Users\x\AppData\Roaming")
    got = resolve_mt4_files_dir(
        r"%APPDATA%\MetaQuotes\Terminal\Common\Files", platform="win32"
    )
    norm = got.replace("\\", "/")
    assert "Roaming" in norm
    assert norm.endswith("MetaQuotes/Terminal/Common/Files")


def test_resolve_mt4_windows_default_common_files(monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", r"C:\Users\x\AppData\Roaming")
    got = resolve_mt4_files_dir("", platform="win32")
    norm = got.replace("\\", "/")
    assert norm.endswith("MetaQuotes/Terminal/Common/Files")


def test_resolve_mt4_empty_on_unix() -> None:
    assert resolve_mt4_files_dir("", platform="linux") == ""


def test_file_bridge_retries_permissionerror(tmp_path: Path, monkeypatch) -> None:
    hits = {"n": 0}
    real = Path.unlink

    def flaky(self, *args, **kwargs):
        if self.name == "mt4_risk_bot.res" and hits["n"] < 1:
            hits["n"] += 1
            raise PermissionError(13, "locked")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky)
    stop = threading.Event()

    def ea() -> None:
        req = tmp_path / "mt4_risk_bot.req"
        res = tmp_path / "mt4_risk_bot.res"
        while not stop.is_set():
            if req.exists():
                try:
                    text = req.read_text(encoding="utf-8")
                    req.unlink()
                except OSError:
                    time.sleep(0.01)
                    continue
                data = decode(text)
                res.write_text(f"id={data['id']}\nok=1\n", encoding="utf-8", newline="\n")
            time.sleep(0.01)

    t = threading.Thread(target=ea, daemon=True)
    t.start()
    try:
        bridge = FileBridge(tmp_path, timeout_sec=2.0)
        ping = bridge.call("ping", {})
        assert ping["ok"] == 1
        assert hits["n"] == 1
    finally:
        stop.set()
        t.join(timeout=1.0)
