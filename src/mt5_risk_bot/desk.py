"""Telegram desk: full trades and AI advice. Risk still sizes every order."""

from __future__ import annotations

import time
from dataclasses import dataclass

from mt5_risk_bot.llm import Advisor
from mt5_risk_bot.models import Signal, SignalKind
from mt5_risk_bot.telegram import HELP, TgCommand


@dataclass
class Pending:
    signal: Signal
    volume: float
    source: str
    expires_at: float


def parse_kv(args: str) -> tuple[str, dict[str, str], list[str]]:
    parts = args.split()
    symbol = parts[0].upper() if parts else ""
    kv: dict[str, str] = {}
    positional: list[str] = []
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            kv[k.lower()] = v
        else:
            positional.append(p)
    return symbol, kv, positional


class Desk:
    def __init__(self, engine: object, advisor: Advisor | None = None) -> None:
        self.engine = engine
        self.advisor = advisor
        self.pending: Pending | None = None

    def handle(self, cmd: TgCommand) -> str:
        try:
            if not cmd.name:
                return self._ask(cmd.args)
            fn = {
                "start": lambda: HELP,
                "help": lambda: HELP,
                "status": self.engine.status_text,
                "positions": self.engine.positions_text,
                "quote": lambda: self._quote(cmd.args),
                "risk": self.engine.risk_text,
                "trail": lambda: self._trail(cmd.args),
                "buy": lambda: self._trade(SignalKind.BUY, cmd.args, "telegram"),
                "sell": lambda: self._trade(SignalKind.SELL, cmd.args, "telegram"),
                "close": lambda: self._close(cmd.args),
                "sl": lambda: self._stop(cmd.args, "sl"),
                "tp": lambda: self._stop(cmd.args, "tp"),
                "be": lambda: self._be(cmd.args),
                "confirm": self._confirm,
                "cancel": lambda: self._cancel(cmd.args),
                "orders": lambda: self.engine.orders_text(),
                "history": self._history,
                "ask": lambda: self._ask(cmd.args),
                "model": lambda: self._model(cmd.args),
                "auto": lambda: self._auto(cmd.args),
                "halt": self._halt,
                "resume": self._resume,
            }.get(cmd.name)
            if fn is None:
                return "unknown command. /help"
            return fn()
        except (ValueError, RuntimeError) as exc:
            return str(exc)

    def _quote(self, args: str) -> str:
        symbol = args.split()[0].upper() if args.strip() else ""
        if not symbol:
            lines = []
            for name in self.engine.cfg.symbols:
                try:
                    lines.append(self.engine.quote_text(name))
                except RuntimeError:
                    continue
            return "\n".join(lines) if lines else "usage: /quote EURUSD"
        return self.engine.quote_text(symbol)

    def _trade(self, kind: SignalKind, args: str, source: str) -> str:
        symbol, kv, pos = parse_kv(args)
        name = "buy" if kind is SignalKind.BUY else "sell"
        if not symbol:
            return f"usage: /{name} SYMBOL [sl=] [tp=] [limit=PRICE] [stop=PRICE]"
        sl = _opt_float(kv.get("sl") or (pos[0] if pos else None))
        tp = _opt_float(kv.get("tp") or (pos[1] if len(pos) > 1 else None))
        limit = _opt_float(kv.get("limit"))
        stop = _opt_float(kv.get("stop"))
        sig = self.engine.market_signal(kind, symbol, sl=sl, tp=tp, limit=limit, stop=stop)
        return self._stage(sig, source)

    def _stage(self, sig: Signal, source: str) -> str:
        now = time.time()
        if self.pending is not None and now <= self.pending.expires_at:
            p = self.pending
            return (
                f"pending {p.signal.kind.value} {p.signal.symbol}; /cancel first"
            )
        decision = self.engine.preview(sig, manual=True)
        if not decision.allowed:
            return f"refused: {decision.reason}"
        ttl = int(self.engine.cfg.telegram.confirm_seconds)
        self.pending = Pending(sig, decision.volume, source, now + ttl)
        extra = f" {sig.pending_kind}" if sig.pending_kind else ""
        return (
            f"confirm {sig.kind.value} {sig.symbol} vol={decision.volume} "
            f"@ {sig.entry} sl={sig.sl} tp={sig.tp} rr={sig.rr:.2f} "
            f"source={source}{extra}\n/confirm within {ttl}s or /cancel"
        )

    def _confirm(self) -> str:
        pending = self.pending
        if pending is None:
            return "nothing to confirm"
        if time.time() > pending.expires_at:
            self.pending = None
            return "confirm expired"
        if getattr(self.engine, "halted", False):
            self.pending = None
            return "refused: halted"
        sig = pending.signal
        if not sig.pending_kind:
            spec = self.engine.broker.symbol(sig.symbol)
            tick = self.engine.broker.tick(sig.symbol)
            entry = tick.ask if sig.kind is SignalKind.BUY else tick.bid
            sig = sig.reprice(entry, spec)
        decision = self.engine.preview(sig, manual=True)
        if not decision.allowed:
            if decision.halt:
                self.pending = None
            return f"refused: {decision.reason}"
        result = self.engine.submit(sig, decision.volume)
        self.pending = None
        if not result.ok:
            return (
                f"send failed ok={result.ok} retcode={result.retcode} {result.comment}"
            ).strip()
        return (
            f"sent {sig.kind.value} {sig.symbol} vol={decision.volume} "
            f"ok={result.ok} retcode={result.retcode}"
        )

    def _cancel(self, args: str = "") -> str:
        token = args.split()[0] if args.strip() else ""
        if not token:
            if self.pending is None:
                return "nothing to cancel"
            self.pending = None
            return "cancelled"
        if not token.isdigit():
            return "usage: /cancel [TICKET]"
        return self.engine.cancel_order(int(token))

    def _close(self, args: str) -> str:
        parts = args.split()
        if not parts:
            return "usage: /close TICKET|SYMBOL|all [VOL]"
        token = parts[0]
        vol = float(parts[1]) if len(parts) > 1 else None
        if token.lower() == "all":
            n = self.engine.close_all("telegram")
            return f"closed {n}"
        if token.isdigit():
            return self.engine.close_ticket(int(token), "telegram", vol)
        if vol is not None:
            return "usage: /close TICKET|SYMBOL|all [VOL]"
        n = self.engine.close_symbol(token.upper(), "telegram")
        return f"closed {n} {token.upper()}"

    def _be(self, args: str) -> str:
        token = args.split()[0] if args.strip() else ""
        if not token or not token.isdigit():
            return "usage: /be TICKET"
        return self.engine.breakeven(int(token))

    def _trail(self, args: str) -> str:
        token = args.split()[0] if args.strip() else ""
        if not token or not token.isdigit():
            return "usage: /trail TICKET"
        return self.engine.trail(int(token))

    def _history(self) -> str:
        return self.engine.history_text()

    def _stop(self, args: str, which: str) -> str:
        parts = args.split()
        if len(parts) != 2:
            return f"usage: /{which} TICKET PRICE"
        ticket = int(parts[0])
        price = float(parts[1])
        if which == "sl":
            return self.engine.set_sl(ticket, price)
        return self.engine.set_tp(ticket, price)

    def _ask(self, question: str) -> str:
        if not question.strip():
            return "ask a question, or /buy /sell"
        if self.advisor is None:
            return "AI not configured"
        advice = self.advisor.ask(question, self.engine.advice_context())
        lines = [advice.text]
        if advice.summary:
            lines.append(advice.summary)
        if advice.action in {"buy", "sell"} and advice.symbol:
            kind = SignalKind.BUY if advice.action == "buy" else SignalKind.SELL
            try:
                sig = self.engine.market_signal(kind, advice.symbol, sl=advice.sl, tp=advice.tp)
                lines.append(self._stage(sig, "advice"))
            except (ValueError, RuntimeError) as exc:
                lines.append(f"could not stage trade: {exc}")
        elif advice.action == "close" and advice.symbol:
            lines.append(f"to flatten {advice.symbol}: /close {advice.symbol}")
        return "\n".join(x for x in lines if x)

    def _model(self, args: str) -> str:
        if self.advisor is None:
            return "AI not configured"
        name = args.strip().lower()
        if not name:
            return f"provider={self.advisor.cfg.provider}"
        if name not in {"grok", "claude"}:
            return "usage: /model grok|claude"
        self.advisor.cfg.provider = name
        if not self.advisor.cfg.enabled:
            return f"switched to {name} but no key is set"
        return f"provider={name}"

    def _auto(self, args: str) -> str:
        token = args.strip().lower()
        if token in {"on", "1", "true"}:
            self.engine.cfg.strategy.auto = True
            return "auto on"
        if token in {"off", "0", "false"}:
            self.engine.cfg.strategy.auto = False
            return "auto off"
        return f"auto={'on' if self.engine.cfg.strategy.auto else 'off'}"

    def _halt(self) -> str:
        self.pending = None
        self.engine.risk.write_halt_file("telegram")
        self.engine.flatten("telegram")
        acct = self.engine.broker.account()
        self.engine._emit("halt", reason="telegram", equity=acct.equity)
        return "flattened and halted. /resume clears the operator HALT file."

    def _resume(self) -> str:
        leftover = self.engine.risk.clear_operator_halt()
        if leftover:
            return f"HALT file cleared; still halted: {leftover}"
        self.engine.halted = False
        return "operator halt cleared. trading may resume."


def _opt_float(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    return float(v)
