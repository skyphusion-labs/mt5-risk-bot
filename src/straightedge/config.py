"""Load TOML config. Secrets never live here; MT5 login comes from env."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from straightedge.constants import TIMEFRAME_BY_NAME, TIMEFRAME_H1


@dataclass
class RiskConfig:
    risk_pct: float = 0.005
    daily_loss_pct: float = 0.02
    max_drawdown_pct: float = 0.10
    max_positions: int = 3
    max_currency_exposure: int = 2
    min_rr: float = 1.5
    max_spread_atr_frac: float = 0.15
    min_free_margin_pct: float = 0.50
    magic: int = 20260909
    halt_file: str = "HALT"
    max_risk_multiple: float = 1.0
    deviation_points: int = 20
    #: Opening sends allowed per UTC day, across auto, telegram and advice.
    #: 0 disables the cap. Counts OPENS only: a close must never be capped,
    #: because a control that can stop you reducing exposure is not a risk
    #: control. `daily_loss_pct` only fires after the money is gone; this is
    #: the one that bounds churn before it.
    max_trades_per_day: int = 0


@dataclass
class StrategyConfig:
    auto: bool = False
    trail: bool = False
    timeframe: str = "H1"
    fast_ema: int = 21
    slow_ema: int = 55
    adx_period: int = 14
    adx_min: float = 20.0
    atr_period: int = 14
    atr_stop_mult: float = 1.5
    atr_tp_mult: float = 2.5
    breakeven_r: float = 1.0
    trail_r: float = 1.5
    trail_atr_mult: float = 1.2

    @property
    def timeframe_id(self) -> int:
        key = self.timeframe.upper()
        if key not in TIMEFRAME_BY_NAME:
            raise ValueError(f"unknown timeframe {self.timeframe!r}")
        return TIMEFRAME_BY_NAME.get(key, TIMEFRAME_H1)


@dataclass
class SessionConfig:
    enabled: bool = True
    start_utc: str = "07:00"
    end_utc: str = "17:00"
    skip_friday_after_utc: str = "16:00"


def _expand_win_vars(s: str) -> str:
    """Expand %VAR% (Windows) after $VAR. Safe on Unix so configs travel."""
    s = os.path.expandvars(s)

    def repl(m: re.Match[str]) -> str:
        return os.environ.get(m.group(1), m.group(0))

    return re.sub(r"%([^%]+)%", repl, s)


def resolve_mt4_files_dir(raw: str, *, platform: str | None = None) -> str:
    """Expand %APPDATA% / ~. On Windows, empty means Common Files."""
    s = _expand_win_vars(os.path.expanduser((raw or "").strip().strip('"')))
    if s:
        return str(Path(s))
    plat = platform if platform is not None else sys.platform
    if plat == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return str(Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files")
    return ""


def resolve_state_path(raw: str, *, base_dir: Path) -> str:
    """Anchor a relative journal_path/halt_file to an explicit base (fc34).

    `base_dir` is the config file's own directory when --config was given,
    else the process working directory -- both documented, explicit bases.
    What this refuses is the alternative: a relative path resolving
    implicitly against wherever the process happens to be started from.
    Under Windows Task Scheduler that working directory is not the repo,
    so the journal, the instance lock, and the emergency HALT file must
    not depend on it. An absolute path (already pinned by the operator)
    passes through unchanged.
    """
    p = Path(raw)
    return str(p if p.is_absolute() else base_dir / p)


@dataclass
class Mt4Config:
    files_dir: str = ""
    timeout_ms: int = 5000


@dataclass
class Mt5Config:
    terminal_path: str = ""
    timeout_ms: int = 60000
    login: int = 0
    password: str = ""
    server: str = ""


DEFAULT_TG_EVENTS = (
    "start",
    "stop",
    "open",
    "close",
    "halt",
    "order_check_fail",
    "pending",
    "recap",
    # Residual exposure after a failed flatten. Also in telegram.ALWAYS_NOTIFY_EVENTS,
    # so removing it from a config file does not silence it.
    "flatten_incomplete",
)


def _parse_bool_flag(raw: object, *, default: bool) -> bool:
    """Fail-closed boolean for a capability switch.

    An operator setting an env var is always handed a string, and Python's
    bool("false") is True: the classic footgun that would silently re-arm a
    capability an operator just tried to turn off. A value that is PRESENT
    but not cleanly true/false is treated as False, never as `default`, so a
    typo or a bad env var can only ever remove capability, never grant it.
    A value that is fully absent (None) falls back to `default`, which is
    what preserves an existing deployment's behaviour on upgrade: it has
    never heard of this key, so nothing about it changes.
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return False

def is_shared_chat_id(chat_id: str) -> bool:
    """True when the id is a Telegram group, supergroup or channel.

    Telegram numbers private chats positively and every shared chat
    negatively. A non-numeric id is not treated as shared.
    """
    try:
        return int(str(chat_id).strip()) < 0
    except (TypeError, ValueError):
        return False


def parse_allow_senders(raw: object) -> tuple[int, ...]:
    """Normalise allow_senders from a TOML list or a comma-separated env var."""
    if raw is None:
        return ()
    items = str(raw).split(",") if isinstance(raw, str) else list(raw)  # type: ignore[call-overload]
    out: list[int] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        try:
            value = int(text)
        except ValueError:
            raise ValueError(
                "telegram.allow_senders must be numeric Telegram sender ids"
            ) from None
        if value <= 0:
            raise ValueError("telegram.allow_senders ids must be positive")
        if value not in out:
            out.append(value)
    return tuple(out)


@dataclass
class TelegramConfig:
    token: str = ""
    chat_id: str = ""
    notify_events: tuple[str, ...] = DEFAULT_TG_EVENTS
    confirm_seconds: int = 120
    # Handover posture (#25). Both default True: an operator who has never
    # heard of this key gets today's behaviour unchanged. The shipped
    # handover template sets both false. Fail-closed parsing lives in
    # _parse_bool_flag, not here: a dataclass default cannot see a garbled
    # config value, only the loader can.
    allow_approve_always: bool = True
    allow_auto: bool = True
    allow_senders: tuple[int, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)


@dataclass
class AdviceConfig:
    provider: str = "grok"  # grok | claude | computer
    #: Advice turns allowed per UTC day. 0 disables the cap.
    #: This is a COST control, not only a risk one: hosted inference is billed
    #: per turn and a turn costs money whether or not it ends in an order, so
    #: the send cap above cannot see this spend at all.
    max_turns_per_day: int = 0
    grok_model: str = "grok-4"
    claude_model: str = "claude-sonnet-5"
    grok_key: str = ""
    claude_key: str = ""
    grok_url: str = "https://api.x.ai/v1/chat/completions"
    claude_url: str = "https://api.anthropic.com/v1/messages"
    computer_url: str = ""
    computer_token: str = ""
    computer_model: str = "xai/grok-4.6"

    @property
    def enabled(self) -> bool:
        if self.provider == "claude":
            return bool(self.claude_key)
        if self.provider == "computer":
            return bool(self.computer_url and self.computer_token)
        return bool(self.grok_key)


@dataclass
class BotConfig:
    mode: str = "paper"  # paper | mt5 | mt4
    initial_balance: float = 10_000.0
    symbols: list[str] = field(default_factory=lambda: ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"])
    #: What the MODEL is allowed to open, which is not the same question as
    #: what the desk scans. `symbols` is a SCAN list: it drives /quote and the
    #: auto scan, and an operator who scans three pairs may still want to act
    #: on a fourth BY HAND. So a human command is not constrained by this, and
    #: an advice-staged symbol is.
    #: EMPTY means "use `symbols`". It never means "allow anything": an empty
    #: whitelist that permits everything is the defect, not the default.
    advice_symbols: list[str] = field(default_factory=list)

    def advice_allows(self, symbol: str) -> bool:
        """Whether the model may OPEN this symbol. Closes are never gated."""
        allowed = self.advice_symbols or self.symbols
        return symbol.upper() in {s.upper() for s in allowed}
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    mt5: Mt5Config = field(default_factory=Mt5Config)
    mt4: Mt4Config = field(default_factory=Mt4Config)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    advice: AdviceConfig = field(default_factory=AdviceConfig)
    poll_seconds: int = 15
    comment: str = "straightedge"
    journal_path: str = "journal.jsonl"
    live_accepted: bool = False

    def validate(self) -> None:
        if self.mode not in {"paper", "mt5", "mt4"}:
            raise ValueError("account.mode must be paper, mt5, or mt4")
        r = self.risk
        if not (0 < r.risk_pct <= 0.05):
            raise ValueError("risk_pct must be in (0, 0.05]")
        if not (0 < r.daily_loss_pct <= 0.20):
            raise ValueError("daily_loss_pct must be in (0, 0.20]")
        if not (0 < r.max_drawdown_pct <= 0.50):
            raise ValueError("max_drawdown_pct must be in (0, 0.50]")
        if r.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        if r.max_trades_per_day < 0:
            raise ValueError("max_trades_per_day must be >= 0 (0 disables)")
        if self.advice.max_turns_per_day < 0:
            raise ValueError("advice.max_turns_per_day must be >= 0 (0 disables)")
        s = self.strategy
        if s.fast_ema >= s.slow_ema:
            raise ValueError("fast_ema must be < slow_ema")
        if s.atr_stop_mult <= 0 or s.atr_tp_mult <= 0:
            raise ValueError("ATR multiples must be > 0")
        if s.atr_tp_mult / s.atr_stop_mult < r.min_rr - 1e-9:
            raise ValueError("atr_tp_mult / atr_stop_mult must be >= min_rr")
        self.strategy.timeframe_id  # raises if unknown
        if self.advice.provider not in {"grok", "claude", "computer"}:
            raise ValueError("advice.provider must be grok, claude, or computer")
        if not self.symbols:
            raise ValueError("at least one symbol required")
        if self.telegram.confirm_seconds <= 0:
            raise ValueError("confirm_seconds must be > 0")
        if is_shared_chat_id(self.telegram.chat_id) and not self.telegram.allow_senders:
            raise ValueError(
                "telegram.chat_id is a shared chat: set telegram.allow_senders "
                "(or TELEGRAM_ALLOW_SENDERS) to the operator sender ids"
            )


def _section(data: dict, name: str) -> dict:
    raw = data.get(name, {})
    return raw if isinstance(raw, dict) else {}


def _hhmm(s: str) -> str:
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"time must be HH:MM, got {s!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"time out of range: {s!r}")
    return f"{h:02d}:{m:02d}"


def load_config(path: str | Path | None = None) -> BotConfig:
    data: dict = {}
    # Explicit, documented base for resolve_state_path(): the config
    # file's own directory when one was given, else the process working
    # directory. Both are named; neither is "wherever we happened to
    # start" by accident.
    base_dir = Path(path).resolve().parent if path is not None else Path.cwd()
    if path is not None:
        raw = Path(path).read_bytes()
        parsed = tomllib.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("config root must be a table")
        data = parsed

    account = _section(data, "account")
    risk_s = _section(data, "risk")
    strat_s = _section(data, "strategy")
    sess_s = _section(data, "session")
    mt5_s = _section(data, "mt5")
    mt4_s = _section(data, "mt4")
    tg_s = _section(data, "telegram")
    advice_s = _section(data, "advice")
    engine_s = _section(data, "engine")
    symbols_s = data.get("symbols", {})
    advice_names = list(data.get("advice", {}).get("symbols", []) or [])

    names = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
    if isinstance(symbols_s, dict) and "names" in symbols_s:
        names = list(symbols_s["names"])
    elif isinstance(data.get("symbols"), list):
        names = list(data["symbols"])

    login = int(os.environ.get("MT5_LOGIN", mt5_s.get("login", 0) or 0) or 0)
    password = os.environ.get("MT5_PASSWORD", str(mt5_s.get("password", "") or ""))
    server = os.environ.get("MT5_SERVER", str(mt5_s.get("server", "") or ""))
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", str(tg_s.get("token", "") or ""))
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", str(tg_s.get("chat_id", "") or ""))
    tg_allow_approve_always = _parse_bool_flag(
        os.environ.get("TELEGRAM_ALLOW_APPROVE_ALWAYS", tg_s.get("allow_approve_always")),
        default=True,
    )
    tg_allow_auto = _parse_bool_flag(
        os.environ.get("TELEGRAM_ALLOW_AUTO", tg_s.get("allow_auto")),
        default=True,
    )
    grok_key = os.environ.get("XAI_API_KEY", str(advice_s.get("grok_key", "") or ""))
    claude_key = os.environ.get("ANTHROPIC_API_KEY", str(advice_s.get("claude_key", "") or ""))
    computer_url = os.environ.get("ADVICE_URL", str(advice_s.get("computer_url", "") or ""))
    computer_token = os.environ.get("ADVICE_TOKEN", str(advice_s.get("computer_token", "") or ""))
    provider = os.environ.get("AI_PROVIDER", str(advice_s.get("provider", "grok") or "grok")).lower()
    tg_allow = parse_allow_senders(
        os.environ.get("TELEGRAM_ALLOW_SENDERS", tg_s.get("allow_senders", ()))
    )
    events_raw = tg_s.get("notify_events", list(DEFAULT_TG_EVENTS))
    if isinstance(events_raw, str):
        events = tuple(x.strip() for x in events_raw.split(",") if x.strip())
    else:
        events = tuple(str(x) for x in events_raw)

    cfg = BotConfig(
        mode=os.environ.get("ACCOUNT_MODE", str(account.get("mode", "paper"))),
        initial_balance=float(account.get("initial_balance", 10_000.0)),
        symbols=names,
        advice_symbols=[str(x).upper() for x in advice_names],
        poll_seconds=int(os.environ.get("POLL_SECONDS", engine_s.get("poll_seconds", 15))),
        comment=str(engine_s.get("comment", "straightedge")),
        journal_path=resolve_state_path(
            str(engine_s.get("journal_path", "journal.jsonl")), base_dir=base_dir
        ),
        risk=RiskConfig(
            risk_pct=float(risk_s.get("risk_pct", 0.005)),
            daily_loss_pct=float(risk_s.get("daily_loss_pct", 0.02)),
            max_drawdown_pct=float(risk_s.get("max_drawdown_pct", 0.10)),
            max_positions=int(risk_s.get("max_positions", 3)),
            max_currency_exposure=int(risk_s.get("max_currency_exposure", 2)),
            min_rr=float(risk_s.get("min_rr", 1.5)),
            max_spread_atr_frac=float(risk_s.get("max_spread_atr_frac", 0.15)),
            min_free_margin_pct=float(risk_s.get("min_free_margin_pct", 0.50)),
            magic=int(risk_s.get("magic", 20260909)),
            halt_file=resolve_state_path(
                str(risk_s.get("halt_file", "HALT")), base_dir=base_dir
            ),
            max_risk_multiple=float(risk_s.get("max_risk_multiple", 1.0)),
            deviation_points=int(risk_s.get("deviation_points", 20)),
            max_trades_per_day=int(risk_s.get("max_trades_per_day", 0)),
        ),
        strategy=StrategyConfig(
            auto=bool(strat_s.get("auto", False)),
            trail=bool(strat_s.get("trail", False)),
            timeframe=str(strat_s.get("timeframe", "H1")),
            fast_ema=int(strat_s.get("fast_ema", 21)),
            slow_ema=int(strat_s.get("slow_ema", 55)),
            adx_period=int(strat_s.get("adx_period", 14)),
            adx_min=float(strat_s.get("adx_min", 20.0)),
            atr_period=int(strat_s.get("atr_period", 14)),
            atr_stop_mult=float(strat_s.get("atr_stop_mult", 1.5)),
            atr_tp_mult=float(strat_s.get("atr_tp_mult", 2.5)),
            breakeven_r=float(strat_s.get("breakeven_r", 1.0)),
            trail_r=float(strat_s.get("trail_r", 1.5)),
            trail_atr_mult=float(strat_s.get("trail_atr_mult", 1.2)),
        ),
        session=SessionConfig(
            enabled=bool(sess_s.get("enabled", True)),
            start_utc=_hhmm(str(sess_s.get("start_utc", "07:00"))),
            end_utc=_hhmm(str(sess_s.get("end_utc", "17:00"))),
            skip_friday_after_utc=_hhmm(str(sess_s.get("skip_friday_after_utc", "16:00"))),
        ),
        mt5=Mt5Config(
            terminal_path=str(mt5_s.get("terminal_path", "")),
            timeout_ms=int(mt5_s.get("timeout_ms", 60000)),
            login=login,
            password=password,
            server=server,
        ),
        mt4=Mt4Config(
            files_dir=resolve_mt4_files_dir(
                os.environ.get("MT4_FILES_DIR", str(mt4_s.get("files_dir", "") or ""))
            ),
            timeout_ms=int(mt4_s.get("timeout_ms", 5000)),
        ),
        telegram=TelegramConfig(
            token=tg_token,
            chat_id=tg_chat,
            notify_events=events or DEFAULT_TG_EVENTS,
            confirm_seconds=int(tg_s.get("confirm_seconds", 120)),
            allow_approve_always=tg_allow_approve_always,
            allow_auto=tg_allow_auto,
            allow_senders=tg_allow,
        ),
        advice=AdviceConfig(
            provider=provider if provider in {"grok", "claude", "computer"} else "grok",
            max_turns_per_day=int(advice_s.get("max_turns_per_day", 0)),
            grok_model=str(advice_s.get("grok_model", "grok-4")),
            claude_model=str(advice_s.get("claude_model", "claude-sonnet-5")),
            grok_key=grok_key,
            claude_key=claude_key,
            grok_url=str(advice_s.get("grok_url", "https://api.x.ai/v1/chat/completions")),
            claude_url=str(advice_s.get("claude_url", "https://api.anthropic.com/v1/messages")),
            computer_url=computer_url,
            computer_token=computer_token,
            computer_model=str(advice_s.get("computer_model", "xai/grok-4.6")),
        ),
    )
    cfg.validate()
    return cfg


def _validate(cfg: BotConfig) -> None:
    cfg.validate()
