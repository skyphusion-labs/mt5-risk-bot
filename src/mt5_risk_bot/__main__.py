"""CLI: paper, backtest, live, doctor.

Live real-money trading requires --i-accept-risk. Demo accounts do not.
Nothing here guarantees profit.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

from mt5_risk_bot import __version__
from mt5_risk_bot.config import BotConfig, load_config
from mt5_risk_bot.engine import Engine, run_backtest
from mt5_risk_bot.models import Bar
from mt5_risk_bot.synthetic import generate_bars, generate_ranging
from mt5_risk_bot.telegram import TelegramClient


def _cfg(args: argparse.Namespace) -> BotConfig:
    cfg = load_config(args.config) if args.config else load_config()
    if getattr(args, "i_accept_risk", False):
        cfg.live_accepted = True
    return cfg


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"mt5-risk-bot {__version__}")
    print(f"python {sys.version.split()[0]}  {sys.executable}")
    mt5_ok = False
    mt5_name = "none"
    try:
        from mt5_risk_bot.broker.mt5_live import load_mt5_module

        mod = load_mt5_module()
        mt5_ok = True
        mt5_name = getattr(mod, "__name__", "unknown")
        print(f"mt5 binding: {mt5_name}")
    except Exception as exc:
        print(f"mt5 binding: unavailable ({exc})")
    if args.config:
        cfg = load_config(args.config)
        print(f"config: mode={cfg.mode} symbols={cfg.symbols} risk_pct={cfg.risk.risk_pct}")
    print("terminal: official MetaTrader5 package is Windows-only.")
    print("macOS: install MetaTrader 5.app from metatrader5.com, then pip install mt5-mac.")
    print("Homebrew has no MetaTrader cask; Python is enough for paper/backtest.")
    print("telegram token:", "SET" if os.environ.get("TELEGRAM_BOT_TOKEN") else "unset")
    print("telegram chat:", "SET" if os.environ.get("TELEGRAM_CHAT_ID") else "unset")
    if args.connect and mt5_ok:
        cfg = _cfg(args) if args.config else load_config()
        from mt5_risk_bot.broker.mt5_live import Mt5Broker

        broker = Mt5Broker(
            login=cfg.mt5.login,
            password=cfg.mt5.password,
            server=cfg.mt5.server,
            path=cfg.mt5.terminal_path,
            timeout_ms=cfg.mt5.timeout_ms,
        )
        broker.connect()
        acct = broker.account()
        print(
            f"connected login={acct.login} server={acct.server} "
            f"equity={acct.equity:.2f} {acct.currency} trade_mode={acct.trade_mode}"
        )
        broker.disconnect()
    return 0


def _load_csv(path: Path) -> list[Bar]:
    bars: list[Bar] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            bars.append(
                Bar(
                    time=int(float(row["time"])),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    tick_volume=int(float(row.get("tick_volume") or 0)),
                    spread=int(float(row.get("spread") or 0)),
                )
            )
    return bars


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if args.no_session_filter:
        cfg.session.enabled = False
    series: dict[str, list[Bar]] = {}
    if args.csv:
        p = Path(args.csv)
        name = args.symbol or cfg.symbols[0]
        series[name] = _load_csv(p)
        cfg.symbols = [name]
    else:
        kind = args.market
        for i, name in enumerate(cfg.symbols):
            if kind == "range":
                series[name] = generate_ranging(args.bars, seed=10 + i)
            else:
                drift = 0.00025 if kind == "trend" else 0.0
                series[name] = generate_bars(args.bars, drift=drift, seed=1 + i)
    journal = args.journal or cfg.journal_path
    result = run_backtest(cfg, series, journal_path=journal)
    start = cfg.initial_balance
    ret = (result["equity"] - start) / start
    print(json.dumps({"start": start, "return": round(ret, 6), **result, "journal": journal}, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if args.mode:
        cfg.mode = args.mode
    if cfg.mode == "mt5":
        from mt5_risk_bot.broker.mt5_live import Mt5Broker

        broker = Mt5Broker(
            login=cfg.mt5.login,
            password=cfg.mt5.password,
            server=cfg.mt5.server,
            path=cfg.mt5.terminal_path,
            timeout_ms=cfg.mt5.timeout_ms,
        )
    else:
        from mt5_risk_bot.broker.paper import PaperBroker

        broker = PaperBroker(balance=cfg.initial_balance)
        if args.synthetic:
            from mt5_risk_bot.engine import run_backtest as _bt
            from mt5_risk_bot.synthetic import generate_bars as _gb

            series = {
                name: _gb(800, drift=0.0002, seed=3 + i) for i, name in enumerate(cfg.symbols)
            }
            cfg.session.enabled = False
            result = _bt(cfg, series, journal_path=cfg.journal_path)
            print(json.dumps({"mode": "paper-synthetic", **result}, indent=2))
            return 0
        elif args.feed_mt5:
            from mt5_risk_bot.broker.mt5_live import Mt5Broker

            live = Mt5Broker(
                login=cfg.mt5.login,
                password=cfg.mt5.password,
                server=cfg.mt5.server,
                path=cfg.mt5.terminal_path,
                timeout_ms=cfg.mt5.timeout_ms,
            )
            live.connect()
            for name in cfg.symbols:
                live.select_symbol(name)
                rates = live.rates(name, cfg.strategy.timeframe_id, 400)
                broker.seed_bars(name, rates)
            live.disconnect()
            print("paper broker seeded from MT5 history; orders stay local")
        else:
            print("paper mode with no data: pass --synthetic or --feed-mt5, or use backtest")
            return 2

    halt_dir = str(Path(cfg.risk.halt_file).parent) or "."
    tg = TelegramClient.from_config(cfg.telegram)
    engine = Engine(cfg, broker, halt_dir=halt_dir, telegram=tg)
    engine.start()
    keep_on_halt = tg is not None
    try:
        while True:
            engine.step_all()
            if engine.halted and not keep_on_halt:
                print("halted")
                break
            if not args.loop:
                break
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        print("interrupt")
    finally:
        engine.stop()
    return 0


def cmd_telegram(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    tg = TelegramClient.from_config(cfg.telegram)
    if tg is None:
        print("telegram disabled: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return 2
    ok = tg.send(args.message or "mt5-risk-bot ping")
    print("sent" if ok else "send failed")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mt5-risk-bot",
        description="Risk-first MT5 trading bot. Paper by default. No profit guarantee.",
    )
    p.add_argument("--config", help="path to TOML config")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="check Python, MT5 binding, optional login")
    d.add_argument("--connect", action="store_true")
    d.set_defaults(func=cmd_doctor)

    b = sub.add_parser("backtest", help="run on synthetic or CSV bars")
    b.add_argument("--bars", type=int, default=1500)
    b.add_argument("--market", choices=("trend", "range", "flat"), default="trend")
    b.add_argument("--csv", help="OHLC CSV with columns time,open,high,low,close")
    b.add_argument("--symbol", help="symbol name for CSV")
    b.add_argument("--journal", help="journal jsonl path")
    b.add_argument("--no-session-filter", action="store_true")
    b.set_defaults(func=cmd_backtest)

    r = sub.add_parser("run", help="paper or live loop")
    r.add_argument("--mode", choices=("paper", "mt5"))
    r.add_argument("--loop", action="store_true", help="poll until halt or Ctrl-C")
    r.add_argument("--synthetic", action="store_true", help="seed paper broker with generated bars")
    r.add_argument("--feed-mt5", action="store_true", help="seed paper broker from a live terminal")
    r.add_argument(
        "--i-accept-risk",
        action="store_true",
        help="required to send orders on a real (trade_mode=2) account",
    )
    r.set_defaults(func=cmd_run)

    t = sub.add_parser("telegram", help="send a test message to the configured chat")
    t.add_argument("--message", default="mt5-risk-bot ping")
    t.set_defaults(func=cmd_telegram)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
