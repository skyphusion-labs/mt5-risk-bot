"""Account-level risk gates. Strategy does not size or send; this module does.

A halt (daily loss, max drawdown, kill file) is sticky until the next UTC day
for daily loss, and until the operator clears the halt file / restarts after
drawdown. The engine is expected to flatten when flatten=True.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mt5_risk_bot.config import BotConfig, SessionConfig
from mt5_risk_bot.models import (
    Account,
    EquitySnapshot,
    Position,
    RiskDecision,
    Side,
    Signal,
    SignalKind,
    SymbolSpec,
    Tick,
)
from mt5_risk_bot.sizing import lots_for_risk, money_per_lot_at_stop


def parse_fx(symbol: str) -> tuple[str, str] | None:
    s = symbol.replace(".", "").replace("m", "").replace("M", "")
    # Strip common broker suffixes
    for suf in ("pro", "mini", "m", "c"):
        if s.lower().endswith(suf) and len(s) > 6:
            s = s[: -len(suf)]
    s = "".join(ch for ch in s if ch.isalpha()).upper()
    if len(s) >= 6:
        return s[:3], s[3:6]
    return None


def currency_exposure(positions: list[Position], extra: tuple[str, Side] | None = None) -> dict[str, int]:
    """Net count of positions touching each currency. Buy EURUSD: +EUR, -USD."""
    counts: dict[str, int] = {}

    def apply(symbol: str, side: Side, sign: int = 1) -> None:
        pair = parse_fx(symbol)
        if pair is None:
            return
        base, quote = pair
        if side is Side.BUY:
            counts[base] = counts.get(base, 0) + sign
            counts[quote] = counts.get(quote, 0) - sign
        else:
            counts[base] = counts.get(base, 0) - sign
            counts[quote] = counts.get(quote, 0) + sign

    for p in positions:
        apply(p.symbol, p.side)
    if extra is not None:
        apply(extra[0], extra[1])
    return counts


def _hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def in_session(ts: datetime, session: SessionConfig) -> bool:
    if not session.enabled:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(timezone.utc)
    minutes = ts.hour * 60 + ts.minute
    start = _hhmm_to_min(session.start_utc)
    end = _hhmm_to_min(session.end_utc)
    if start <= end:
        inside = start <= minutes < end
    else:
        inside = minutes >= start or minutes < end
    if not inside:
        return False
    if ts.weekday() == 4:  # Friday
        cutoff = _hhmm_to_min(session.skip_friday_after_utc)
        if minutes >= cutoff:
            return False
    if ts.weekday() >= 5:  # Sat/Sun
        return False
    return True


def day_key(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d")


class RiskManager:
    def __init__(self, cfg: BotConfig, *, halt_dir: str | Path = ".") -> None:
        self.cfg = cfg
        self.halt_dir = Path(halt_dir)
        self.snapshot = EquitySnapshot(
            time=0,
            balance=cfg.initial_balance,
            equity=cfg.initial_balance,
            peak_equity=cfg.initial_balance,
            day_start_equity=cfg.initial_balance,
            day_key="",
        )
        self._halted = False
        self._halt_reason = ""

    def halt_path(self) -> Path:
        p = Path(self.cfg.risk.halt_file)
        return p if p.is_absolute() else self.halt_dir / p

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    @property
    def is_halted(self) -> bool:
        return self._halted or self.halt_path().exists()

    def write_halt_file(self, reason: str = "operator") -> Path:
        path = self.halt_path()
        path.write_text(reason + "\n", encoding="utf-8")
        return path

    def clear_operator_halt(self) -> str:
        """Clear HALT file. Leaves daily_loss / max_drawdown in place.

        Returns remaining halt reason, or empty string if the bot may resume.
        """
        path = self.halt_path()
        if path.exists():
            path.unlink()
        if self._halt_reason == "halt_file":
            self._halted = False
            self._halt_reason = ""
        if self._halted:
            return self._halt_reason
        return ""

    def observe(self, account: Account, now: datetime) -> None:
        key = day_key(now)
        if self.snapshot.day_key != key:
            self.snapshot.day_start_equity = account.equity
            self.snapshot.day_key = key
            if self._halt_reason == "daily_loss":
                self._halted = False
                self._halt_reason = ""
        self.snapshot.time = int(now.timestamp())
        self.snapshot.balance = account.balance
        self.snapshot.equity = account.equity
        if account.equity > self.snapshot.peak_equity:
            self.snapshot.peak_equity = account.equity

    def _halt(self, reason: str, flatten: bool = True) -> RiskDecision:
        self._halted = True
        self._halt_reason = reason
        return RiskDecision(allowed=False, reason=reason, halt=True, flatten=flatten)

    def circuit(self, account: Account, now: datetime) -> RiskDecision:
        """Account-level halt gates. No signal, no sizing."""
        self.observe(account, now)
        r = self.cfg.risk
        if self.halt_path().exists():
            return self._halt("halt_file")
        if self._halted:
            return RiskDecision(allowed=False, reason=self._halt_reason, halt=True, flatten=True)
        if not account.trade_allowed or not account.trade_expert:
            return RiskDecision(allowed=False, reason="trade_not_allowed")
        if self.cfg.mode == "mt5" and account.trade_mode == 2 and not self.cfg.live_accepted:
            return RiskDecision(allowed=False, reason="live_not_accepted")
        daily_loss = self.snapshot.day_start_equity - account.equity
        if daily_loss >= self.snapshot.day_start_equity * r.daily_loss_pct:
            return self._halt("daily_loss")
        dd = self.snapshot.peak_equity - account.equity
        if self.snapshot.peak_equity > 0 and dd >= self.snapshot.peak_equity * r.max_drawdown_pct:
            return self._halt("max_drawdown")
        return RiskDecision(allowed=True, reason="ok")

    def evaluate(
        self,
        *,
        account: Account,
        signal: Signal,
        spec: SymbolSpec,
        tick: Tick,
        positions: list[Position],
        now: datetime,
    ) -> RiskDecision:
        trip = self.circuit(account, now)
        if not trip.allowed:
            return trip
        r = self.cfg.risk

        if signal.kind is SignalKind.FLAT or signal.side is None:
            return RiskDecision(allowed=False, reason="no_signal")

        if not in_session(now, self.cfg.session):
            return RiskDecision(allowed=False, reason="outside_session")

        ours = [p for p in positions if p.magic == r.magic]
        if len(ours) >= r.max_positions:
            return RiskDecision(allowed=False, reason="max_positions")
        if any(p.symbol == signal.symbol for p in ours):
            return RiskDecision(allowed=False, reason="already_in_symbol")

        exposure = currency_exposure(ours, extra=(signal.symbol, signal.side))
        if any(abs(v) > r.max_currency_exposure for v in exposure.values()):
            return RiskDecision(allowed=False, reason="currency_exposure")

        if signal.sl <= 0 or signal.risk_distance <= 0:
            return RiskDecision(allowed=False, reason="sl_required")
        if signal.rr + 1e-9 < r.min_rr:
            return RiskDecision(allowed=False, reason="rr_below_min")

        min_dist = spec.min_stop_distance()
        if signal.risk_distance < min_dist:
            return RiskDecision(allowed=False, reason="stops_level")

        if signal.atr > 0 and tick.spread > r.max_spread_atr_frac * signal.atr:
            return RiskDecision(allowed=False, reason="spread_too_wide")

        if account.equity > 0:
            free_frac = account.margin_free / account.equity if account.equity else 0
            if free_frac < r.min_free_margin_pct and account.margin > 0:
                return RiskDecision(allowed=False, reason="margin_buffer")

        lots = lots_for_risk(
            account.equity,
            r.risk_pct,
            signal.entry,
            signal.sl,
            spec,
            max_risk_multiple=r.max_risk_multiple,
        )
        if lots <= 0:
            return RiskDecision(allowed=False, reason="size_zero")

        worst = money_per_lot_at_stop(signal.entry, signal.sl, spec) * lots
        if worst > account.equity * r.risk_pct * r.max_risk_multiple + 1e-6:
            return RiskDecision(allowed=False, reason="size_exceeds_risk")

        return RiskDecision(allowed=True, reason="ok", volume=lots)
