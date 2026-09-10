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
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from mt5_risk_bot import __version__
from mt5_risk_bot.config import BotConfig, load_config
from mt5_risk_bot.engine import Engine, run_backtest
from mt5_risk_bot.journal import InstanceLock, InstanceLockError, redact_text
from mt5_risk_bot.models import Bar
from mt5_risk_bot.synthetic import generate_bars, generate_ranging
from mt5_risk_bot.telegram import TelegramClient, TgCommand, offset_path_for


def _cfg(args: argparse.Namespace) -> BotConfig:
    cfg = load_config(args.config) if args.config else load_config()
    if getattr(args, "i_accept_risk", False):
        cfg.live_accepted = True
    cfg.validate()
    return cfg


def telegram_ping(cfg: BotConfig, *, transport=None) -> str:
    tg = TelegramClient.from_config(
        cfg.telegram,
        transport=transport,
        offset_path=offset_path_for(cfg.journal_path),
    )
    if tg is None or not tg.enabled:
        return "skip"
    try:
        ok = tg.send("mt5-risk-bot doctor ping")
    except (ValueError, RuntimeError, OSError) as exc:
        return f"fail ({exc})"
    return "ok" if ok else "fail"


def paper_round_trip() -> str:
    """In-process /buy /confirm /close. No live terminal."""
    from mt5_risk_bot.broker.paper import PaperBroker

    with TemporaryDirectory() as tmp:
        cfg = BotConfig()
        cfg.session.enabled = False
        cfg.risk.max_spread_atr_frac = 10.0
        cfg.risk.halt_file = str(Path(tmp) / "HALT")
        cfg.journal_path = str(Path(tmp) / "j.jsonl")
        cfg.symbols = ["EURUSD"]
        broker = PaperBroker(balance=10_000)
        broker.seed_bars("EURUSD", generate_bars(120, drift=0.0004, vol=0.0002, seed=3))
        engine = Engine(
            cfg,
            broker,
            halt_dir=tmp,
            now_fn=lambda: datetime(2024, 1, 3, 12, tzinfo=timezone.utc),
        )
        engine.start()
        buy = engine.handle_command(TgCommand("1", 1, "/buy EURUSD", 1))
        if "confirm buy" not in buy:
            engine.stop()
            return f"fail stage: {buy}"
        sent = engine.handle_command(TgCommand("1", 1, "/confirm", 2))
        if not sent.startswith("sent buy"):
            engine.stop()
            return f"fail confirm: {sent}"
        if not engine.broker.positions():
            engine.stop()
            return "fail confirm: no position"
        closed = engine.handle_command(TgCommand("1", 1, "/close all", 3))
        if not closed.startswith("closed"):
            engine.stop()
            return f"fail close: {closed}"
        if engine.broker.positions():
            engine.stop()
            return "fail close: position remains"
        engine.stop()
        return "ok"


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"mt5-risk-bot {__version__}")
    print(f"python {sys.version.split()[0]}  {sys.executable}")
    mt5_ok = False
    try:
        from mt5_risk_bot.broker.mt5_live import load_mt5_module

        mod = load_mt5_module()
        mt5_ok = True
        print(f"mt5 binding: {getattr(mod, '__name__', 'unknown')}")
    except Exception as exc:
        print(f"mt5 binding: unavailable ({exc})")
    cfg = load_config(args.config) if args.config else load_config()
    if args.config:
        print(f"config: mode={cfg.mode} symbols={cfg.symbols} risk_pct={cfg.risk.risk_pct}")
    print("terminal: official MetaTrader5 package is Windows-only.")
    print("macOS: install MetaTrader 5.app from metatrader5.com, then pip install mt5-mac.")
    print("Homebrew has no MetaTrader cask; Python is enough for paper/backtest.")
    print("telegram token:", "SET" if os.environ.get("TELEGRAM_BOT_TOKEN") else "unset")
    print("telegram chat:", "SET" if os.environ.get("TELEGRAM_CHAT_ID") else "unset")
    print("xai key:", "SET" if os.environ.get("XAI_API_KEY") else "unset")
    print("anthropic key:", "SET" if os.environ.get("ANTHROPIC_API_KEY") else "unset")
    print("ai provider:", os.environ.get("AI_PROVIDER", "grok"))
    ping = telegram_ping(cfg)
    print(f"telegram ping: {ping}")
    paper = paper_round_trip()
    print(f"paper round-trip /buy /confirm /close: {paper}")
    rc = 0
    if ping.startswith("fail") or paper != "ok":
        rc = 1
    if args.connect:
        if not mt5_ok:
            print("connect: fail (no mt5 binding)")
            return 1
        cfg = _cfg(args) if args.config else load_config()
        from mt5_risk_bot.broker.mt5_live import Mt5Broker

        broker = Mt5Broker(
            login=cfg.mt5.login,
            password=cfg.mt5.password,
            server=cfg.mt5.server,
            path=cfg.mt5.terminal_path,
            timeout_ms=cfg.mt5.timeout_ms,
        )
        try:
            ensure = getattr(broker, "ensure_connected", None)
            if callable(ensure):
                ensure()
            else:
                broker.connect()
            acct = broker.account()
            print(
                f"connected login={acct.login} server={acct.server} "
                f"equity={acct.equity:.2f} {acct.currency} trade_mode={acct.trade_mode}"
            )
        except (RuntimeError, OSError, ValueError) as exc:
            print(f"connect: fail ({redact_text(str(exc))})")
            rc = 1
        finally:
            try:
                broker.disconnect()
            except (RuntimeError, OSError, ValueError, AttributeError):
                pass
    return rc


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
    try:
        lock = InstanceLock(cfg.journal_path)
        lock.acquire()
    except InstanceLockError:
        print("already running", file=sys.stderr)
        return 2
    try:
        return _cmd_run_locked(args, cfg)
    finally:
        lock.release()


def _cmd_run_locked(args: argparse.Namespace, cfg: BotConfig) -> int:
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

    halt_dir = str(Path(cfg.risk.halt_file).parent) or "."
    tg = TelegramClient.from_config(
        cfg.telegram, offset_path=offset_path_for(cfg.journal_path)
    )
    if tg is None:
        print("telegram is the front door: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return 2
    try:
        lock = InstanceLock(cfg.journal_path)
        lock.acquire()
    except InstanceLockError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    engine = Engine(cfg, broker, halt_dir=halt_dir, telegram=tg)
    try:
        engine.start()
        run_loop(engine, loop=bool(args.loop), keep_on_halt=True)
    except KeyboardInterrupt:
        print("interrupt")
    finally:
        try:
            engine.stop()
        finally:
            lock.release()
    return 0


def run_loop(engine: Engine, *, loop: bool, keep_on_halt: bool = True) -> None:
    """One tick, or until Ctrl-C. A bad tick is journaled; the process stays up."""
    while True:
        try:
            engine.step_all()
        except Exception as exc:
            print(f"loop error: {redact_text(str(exc))}", file=sys.stderr)
            try:
                engine.journal.write("loop_error", error=str(exc)[:200])
            except Exception:
                pass
        if engine.halted and not keep_on_halt:
            print("halted")
            break
        if not loop:
            break


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
        description="Telegram desk for MT5: full trades and Grok/Claude advice. Risk gates every order.",
    )
    p.add_argument("--config", help="path to TOML config")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser(
        "doctor",
        help="telegram ping, paper /buy /confirm /close, optional MT5 login",
    )
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
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
