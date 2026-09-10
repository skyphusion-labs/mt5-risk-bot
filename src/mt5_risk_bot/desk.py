"""Telegram desk: full trades and AI advice. Risk still sizes every order."""

from __future__ import annotations

import time
from dataclasses import dataclass

from mt5_risk_bot.journal import redact_text
from mt5_risk_bot.llm import Advice, Advisor
from mt5_risk_bot.models import Signal, SignalKind
from mt5_risk_bot.telegram import HELP, TgCommand


@dataclass
class Pending:
    signal: Signal | None
    volume: float
    source: str
    expires_at: float
    close_ticket: int | None = None

    def label(self) -> str:
        if self.close_ticket is not None and self.signal is None:
            return f"close #{self.close_ticket}"
        if self.signal is None:
            return "order"
        extra = f" {self.signal.pending_kind}" if self.signal.pending_kind else ""
        if self.close_ticket is not None:
            return (
                f"reverse #{self.close_ticket} {self.signal.kind.value} "
                f"{self.signal.symbol}{extra}"
            )
        return f"{self.signal.kind.value} {self.signal.symbol}{extra}"


def pending_from_record(rec: dict) -> Pending | None:
    if not isinstance(rec, dict):
        return None
    try:
        expires_at = float(rec.get("expires_at") or 0)
        volume = float(rec.get("volume") or 0)
        source = str(rec.get("source") or "")
        raw_ticket = rec.get("close_ticket")
        close_ticket = int(raw_ticket) if raw_ticket not in (None, "") else None
        sig_raw = rec.get("signal")
        signal = None
        if isinstance(sig_raw, dict) and sig_raw.get("kind") in {"buy", "sell"}:
            signal = Signal(
                kind=SignalKind(sig_raw["kind"]),
                symbol=str(sig_raw["symbol"]),
                entry=float(sig_raw["entry"]),
                sl=float(sig_raw["sl"]),
                tp=float(sig_raw["tp"]),
                atr=float(sig_raw.get("atr") or 0),
                reason=str(sig_raw.get("reason") or ""),
                fast_ema=float(sig_raw.get("fast_ema") or 0),
                slow_ema=float(sig_raw.get("slow_ema") or 0),
                adx=float(sig_raw.get("adx") or 0),
                pending_kind=str(sig_raw.get("pending_kind") or ""),
            )
        if signal is None and close_ticket is None:
            return None
        return Pending(signal, volume, source, expires_at, close_ticket=close_ticket)
    except (TypeError, ValueError, KeyError):
        return None


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
        self.approve_always = False

    def restore_from_journal(self, journal: object, now: float | None = None) -> None:
        last_fn = getattr(journal, "last_event", None)
        if not callable(last_fn):
            return
        rec = last_fn("confirm_stage", "confirm_cancel", "confirm_sent")
        mode = last_fn("approve_always", "approve_off")
        if isinstance(mode, dict) and mode.get("event") == "approve_always":
            self.approve_always = True
        if not isinstance(rec, dict) or rec.get("event") != "confirm_stage":
            return
        stamp = time.time() if now is None else now
        try:
            expires_at = float(rec.get("expires_at") or 0)
        except (TypeError, ValueError):
            return
        if stamp > expires_at:
            return
        pending = pending_from_record(rec)
        if pending is not None:
            self.pending = pending

    def _write_confirm(self, event: str, pending: Pending | None = None) -> None:
        fields: dict = {}
        if pending is not None:
            fields = {
                "volume": pending.volume,
                "source": pending.source,
                "expires_at": pending.expires_at,
                "close_ticket": pending.close_ticket,
            }
            if pending.signal is not None:
                fields["signal"] = pending.signal
        emit = getattr(self.engine, "_emit", None)
        if callable(emit):
            emit(event, **fields)
            return
        journal = getattr(self.engine, "journal", None)
        write = getattr(journal, "write", None)
        if callable(write):
            write(event, **fields)

    def _set_pending(self, pending: Pending) -> None:
        self.pending = pending
        self._write_confirm("confirm_stage", pending)

    def _clear_pending(self, event: str) -> None:
        pending = self.pending
        self.pending = None
        if pending is not None:
            self._write_confirm(event, pending)

    def handle(self, cmd: TgCommand) -> str:
        try:
            if not cmd.name:
                return self._ask(cmd.args, session=str(cmd.chat_id))
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
                "reverse": lambda: self._reverse(cmd.args),
                "close": lambda: self._close(cmd.args),
                "closeby": lambda: self._closeby(cmd.args),
                "sl": lambda: self._stop(cmd.args, "sl"),
                "tp": lambda: self._stop(cmd.args, "tp"),
                "be": lambda: self._be(cmd.args),
                "confirm": self._confirm,
                "approve": lambda: self._approve(cmd.args),
                "cancel": lambda: self._cancel(cmd.args),
                "orders": lambda: self.engine.orders_text(),
                "replace": lambda: self._replace(cmd.args),
                "history": self._history,
                "recap": self.engine.recap_text,
                "symbols": lambda: self._symbols(cmd.args),
                "ask": lambda: self._ask(cmd.args, session=str(cmd.chat_id)),
                "model": lambda: self._model(cmd.args),
                "auto": lambda: self._auto(cmd.args),
                "halt": self._halt,
                "resume": self._resume,
            }.get(cmd.name)
            if fn is None:
                return "unknown command. /help"
            return fn()
        except (ValueError, RuntimeError) as exc:
            return redact_text(str(exc))

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
            return f"pending {self.pending.label()}; /cancel first"
        decision = self.engine.preview(sig, manual=True)
        if not decision.allowed:
            return f"refused: {decision.reason}"
        ttl = int(self.engine.cfg.telegram.confirm_seconds)
        self._set_pending(Pending(sig, decision.volume, source, now + ttl))
        extra = f" {sig.pending_kind}" if sig.pending_kind else ""
        if self.approve_always:
            return self._confirm()
        return (
            f"confirm {sig.kind.value} {sig.symbol} vol={decision.volume} "
            f"@ {sig.entry} sl={sig.sl} tp={sig.tp} rr={sig.rr:.2f} "
            f"source={source}{extra}\n/confirm within {ttl}s or /cancel"
        )

    def _stage_close(self, advice: Advice) -> str:
        now = time.time()
        if self.pending is not None and now <= self.pending.expires_at:
            return f"pending {self.pending.label()}; /cancel first"
        ticket = advice.ticket
        if ticket is None:
            if advice.symbol:
                return f"to flatten {advice.symbol}: /close {advice.symbol}"
            return "close needs ticket"
        pos = self.engine._pos(ticket)
        if pos is None:
            return "no such ticket"
        ttl = int(self.engine.cfg.telegram.confirm_seconds)
        self._set_pending(Pending(None, pos.volume, "advice", now + ttl, close_ticket=ticket))
        if self.approve_always:
            return self._confirm()
        return (
            f"confirm close #{ticket} {pos.symbol} vol={pos.volume} "
            f"source=advice\n/confirm within {ttl}s or /cancel"
        )

    def _confirm(self) -> str:
        pending = self.pending
        if pending is None:
            return "nothing to confirm"
        if time.time() > pending.expires_at:
            self._clear_pending("confirm_cancel")
            return "confirm expired"
        if getattr(self.engine, "halted", False):
            self._clear_pending("confirm_cancel")
            return "refused: halted"
        if pending.close_ticket is not None and pending.signal is None:
            ticket = pending.close_ticket
            reply = self.engine.close_ticket(ticket, pending.source)
            if reply.startswith("closed"):
                self._clear_pending("confirm_sent")
                return f"sent close #{ticket}"
            self._clear_pending("confirm_cancel")
            return reply
        if pending.close_ticket is not None and pending.signal is not None:
            return self._confirm_reverse(pending)
        sig = pending.signal
        if sig is None:
            self._clear_pending("confirm_cancel")
            return "nothing to confirm"
        if not sig.pending_kind:
            spec = self.engine.broker.symbol(sig.symbol)
            tick = self.engine.broker.tick(sig.symbol)
            entry = tick.ask if sig.kind is SignalKind.BUY else tick.bid
            sig = sig.reprice(entry, spec)
        decision = self.engine.preview(sig, manual=True)
        if not decision.allowed:
            if decision.halt:
                self._clear_pending("confirm_cancel")
            return f"refused: {decision.reason}"
        result = self.engine.submit(sig, decision.volume)
        if not result.ok:
            self._clear_pending("confirm_cancel")
            return (
                f"send failed ok={result.ok} retcode={result.retcode} {result.comment}"
            ).strip()
        self._clear_pending("confirm_sent")
        return (
            f"sent {sig.kind.value} {sig.symbol} vol={decision.volume} "
            f"ok={result.ok} retcode={result.retcode}"
        )

    def _cancel(self, args: str = "") -> str:
        token = args.split()[0] if args.strip() else ""
        if not token:
            if self.pending is None:
                return "nothing to cancel"
            self._clear_pending("confirm_cancel")
            return "cancelled"
        if not token.isdigit():
            return "usage: /cancel [TICKET]"
        return self.engine.cancel_order(int(token))

    def _reverse(self, args: str) -> str:
        parts = args.split()
        if not parts or not parts[0].isdigit():
            return "usage: /reverse TICKET [sl=] [tp=]"
        ticket = int(parts[0])
        _, kv, _ = parse_kv(args)
        sl = _opt_float(kv.get("sl"))
        tp = _opt_float(kv.get("tp"))
        now = time.time()
        if self.pending is not None and now <= self.pending.expires_at:
            return f"pending {self.pending.label()}; /cancel first"
        sig = self.engine.reverse_signal(ticket, sl=sl, tp=tp)
        decision = self.engine.preview(sig, manual=True, exclude_ticket=ticket)
        if not decision.allowed:
            return f"refused: {decision.reason}"
        ttl = int(self.engine.cfg.telegram.confirm_seconds)
        self._set_pending(Pending(sig, decision.volume, "telegram", now + ttl, close_ticket=ticket))
        if self.approve_always:
            return self._confirm()
        return (
            f"confirm reverse #{ticket} {sig.kind.value} {sig.symbol} vol={decision.volume} "
            f"@ {sig.entry} sl={sig.sl} tp={sig.tp} rr={sig.rr:.2f} "
            f"source=telegram\n/confirm within {ttl}s or /cancel"
        )

    def _confirm_reverse(self, pending: Pending) -> str:
        ticket = pending.close_ticket
        sig = pending.signal
        if ticket is None or sig is None:
            self._clear_pending("confirm_cancel")
            return "nothing to confirm"
        pos = self.engine._pos(ticket)
        if pos is None:
            self._clear_pending("confirm_cancel")
            return "no such ticket"
        spec = self.engine.broker.symbol(sig.symbol)
        tick = self.engine.broker.tick(sig.symbol)
        entry = tick.ask if sig.kind is SignalKind.BUY else tick.bid
        sig = sig.reprice(entry, spec)
        decision = self.engine.preview(sig, manual=True, exclude_ticket=ticket)
        if not decision.allowed:
            if decision.halt:
                self._clear_pending("confirm_cancel")
            return f"refused: {decision.reason}"
        closed = self.engine.close_ticket(ticket, pending.source)
        if not closed.startswith("closed"):
            self._clear_pending("confirm_cancel")
            return closed
        decision = self.engine.preview(sig, manual=True)
        if not decision.allowed:
            self._clear_pending("confirm_cancel")
            return f"closed #{ticket}; reverse refused: {decision.reason}"
        result = self.engine.submit(sig, decision.volume)
        if not result.ok:
            self._clear_pending("confirm_cancel")
            return (
                f"closed #{ticket}; send failed ok={result.ok} "
                f"retcode={result.retcode} {result.comment}"
            ).strip()
        self._clear_pending("confirm_sent")
        return (
            f"sent reverse #{ticket} {sig.kind.value} {sig.symbol} vol={decision.volume} "
            f"ok={result.ok} retcode={result.retcode}"
        )

    def _closeby(self, args: str) -> str:
        parts = args.split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            return "usage: /closeby TICKET OTHER"
        return self.engine.close_by(int(parts[0]), int(parts[1]))

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
        if not token:
            return f"trail={'on' if self.engine.cfg.strategy.trail else 'off'}"
        low = token.lower()
        if low in {"on", "true"}:
            self.engine.cfg.strategy.trail = True
            return "trail on"
        if low in {"off", "false"}:
            self.engine.cfg.strategy.trail = False
            return "trail off"
        if not token.isdigit():
            return "usage: /trail on|off|TICKET"
        return self.engine.trail(int(token))

    def _history(self) -> str:
        return self.engine.history_text()

    def _symbols(self, args: str) -> str:
        parts = args.split()
        action = parts[0].lower() if parts else "list"
        name = parts[1] if len(parts) > 1 else ""
        if action == "list":
            return self.engine.symbols_text()
        if action == "add":
            if not name:
                return "usage: /symbols add SYMBOL"
            return self.engine.add_symbol(name)
        if action == "remove":
            if not name:
                return "usage: /symbols remove SYMBOL"
            return self.engine.remove_symbol(name)
        return "usage: /symbols list|add|remove [SYMBOL]"

    def _replace(self, args: str) -> str:
        parts = args.split()
        if len(parts) != 2:
            return "usage: /replace TICKET PRICE"
        return self.engine.replace_pending(int(parts[0]), float(parts[1]))

    def _stop(self, args: str, which: str) -> str:
        parts = args.split()
        if which == "sl":
            if len(parts) != 2:
                return "usage: /sl TICKET PRICE"
            return self.engine.set_sl(int(parts[0]), float(parts[1]))
        if len(parts) not in {2, 3}:
            return "usage: /tp TICKET PRICE [VOL]"
        vol = float(parts[2]) if len(parts) == 3 else None
        return self.engine.set_tp(int(parts[0]), float(parts[1]), vol)

    def _ask(self, question: str, session: str = "") -> str:
        if not question.strip():
            return "ask a question, or /buy /sell"
        if self.advisor is None:
            return "AI not configured"
        advice = self.advisor.ask(question, self.engine.advice_context(), session=session)
        lines = [advice.text]
        if advice.summary:
            lines.append(advice.summary)
        if advice.action in {"buy", "sell"} and advice.symbol:
            reason = self.engine.advice_circuit_reason()
            if reason:
                lines.append(
                    f"not staging {advice.action}: circuit {reason}; hold or close only"
                )
            else:
                kind = SignalKind.BUY if advice.action == "buy" else SignalKind.SELL
                try:
                    sig = self.engine.market_signal(
                        kind,
                        advice.symbol,
                        sl=advice.sl,
                        tp=advice.tp,
                        limit=advice.limit,
                        stop=advice.stop,
                    )
                    lines.append(self._stage(sig, "advice"))
                except (ValueError, RuntimeError) as exc:
                    lines.append(f"could not stage trade: {redact_text(str(exc))}")
        elif advice.action == "close":
            lines.append(self._stage_close(advice))
        return "\n".join(x for x in lines if x)

    def _model(self, args: str) -> str:
        if self.advisor is None:
            return "AI not configured"
        name = args.strip().lower()
        if not name:
            return f"provider={self.advisor.cfg.provider}"
        if name not in {"grok", "claude", "computer"}:
            return "usage: /model grok|claude|computer"
        self.advisor.cfg.provider = name
        if not self.advisor.cfg.enabled:
            return f"switched to {name} but no key is set"
        return f"provider={name}"

    def _live_needs_flag(self) -> bool:
        cfg = getattr(self.engine, "cfg", None)
        if cfg is None or getattr(cfg, "mode", "paper") != "mt5":
            return False
        if getattr(cfg, "live_accepted", False):
            return False
        broker = getattr(self.engine, "broker", None)
        if broker is None:
            return False
        try:
            acct = broker.account()
        except Exception:
            return False
        return int(getattr(acct, "trade_mode", 0) or 0) == 2

    def _approve(self, args: str) -> str:
        token = args.strip().lower()
        if token in {"always", "on"}:
            if self._live_needs_flag():
                return (
                    "real-money: start the bot with --i-accept-risk then "
                    "/approve always"
                )
            self.approve_always = True
            self._write_confirm("approve_always")
            return (
                "approve always. risk still sizes and can refuse. "
                "/approve off to stage again"
            )
        if token in {"off"}:
            self.approve_always = False
            self._write_confirm("approve_off")
            return "approve off. /confirm required"
        return f"approve={'always' if self.approve_always else 'off'}"

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
        self._clear_pending("confirm_cancel")
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
