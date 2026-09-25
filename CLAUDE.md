# straightedge

Risk-first MT4/MT5 trading desk, operated from Telegram, with an AI advice layer and a risk
engine that sizes and can refuse orders. Public MIT repo (`skyphusion-labs/straightedge`), the
revenue lane: real money, an end user demoing it live. Paper mode is the default; live trading
needs explicit gates (below).

## Names (the repo's own vocabulary, see README)

The bot = the Python process (`src/straightedge/`). The desk = Telegram chat commands. The
agent = the Cloudflare Worker AI advisor (`agent/`, `AI_PROVIDER=computer`, routed through the
AI Gateway `mt5-risk-bot`). `grok` and `claude` are the other two `AI_PROVIDER` values, BYOK
straight to `api.x.ai` / `api.anthropic.com`, no gateway. The circuit = halt, daily-loss, and
drawdown gates.

## Broker layer

`src/straightedge/broker/` is one interface, three implementations: `mt4_live.py`,
`mt5_live.py`, `paper.py`. `docs/VENUE.md` is the provider-agnostic execution API
(`MarketOrder`, `WorkingOrder`) all three implement; `docs/CONTRACT.md` is the behaviour the
test suite enforces.

**MT5** uses the official `MetaTrader5` package (Windows) or `mt5-mac` (macOS via a running
MT5 terminal); needs a live terminal.

**MT4 is a file mailbox, not a network call.** `mt4/Experts/Mt4RiskBot.mq4` and
`mt4_live.py` talk only through `FileOpen` / `FileWriteString` / `FileDelete` on
`mt4_risk_bot.req` / `mt4_risk_bot.res` in MT4's Common Files dir (`docs/MT4.md` is the ICD).
Zero `WebRequest`, zero sockets. That is why the bot currently has to run on the same Windows
box as the MT4 terminal, and it is the open decision in **straightedge#73**: the file mailbox
is the only thing a customer ever installs, so changing its transport (e.g. to a `WebRequest`
call against a hosted endpoint) is a product decision, not a refactor, because every existing
customer would have to reinstall. `mt4_live.py` already carries retry logic for NTFS refusing
to unlink a file the terminal still holds open; that is a filesystem race, not a bug to
silently work around further.

## The per-symbol trap

`SymbolSpec` (`src/straightedge/models.py`) carries `point`, `trade_tick_size`,
`trade_tick_value`, and `trade_contract_size` per symbol, and sizing (`sizing.py`) is driven
entirely from those measured fields, never a hardcoded constant. That matters because
instruments differ by orders of magnitude on exactly these fields: gold (XAUUSD) measured at
`point 0.01`, `contract_size 100` (oz/lot) vs. a typical 5-digit FX pair at `point 0.00001`,
`contract_size 100000`. A constant that is instrument-blind silently mis-sizes by that ratio.
**straightedge#68** measured this for gold and found `deviation_points` (max tolerated
slippage) was one such instrument-blind constant: 20 points is generous slippage on EURUSD and
was $0.20 against gold's ~$0.45 spread, causing silent order rejects. Fix in progress; the
per-symbol override lives at `[risk.symbol_deviation_points]` in `config.example.toml`
(currently `XAUUSD`/`XAGUSD` only). Adding an index CFD (SP500, Nasdaq) needs its own entry
here and in any other instrument-scaled constant; do not assume FX defaults generalize.

**Unmeasured specs refuse, they never default.** If a broker has not streamed a symbol's specs
(e.g. it is not in MT4 Market Watch yet), the adapter records the field as `unmeasured` and
sizing returns 0 lots rather than falling back to a plausible-looking default. Never "fix" a
zero-lot trade by supplying a default in this path; that is the exact defect straightedge#68
found and re-broke (see `broker/mt4_live.py:294` for the history).

## Safety posture

- `risk_pct` is an operator-set percentage of equity per trade (`config.toml [risk]`,
  `risk_pct = 0.005` in the example). Daily-loss and max-drawdown percentages flatten and halt.
- Approval gate: `/confirm` is the default send for every staged order; `/approve always`
  skips it after a risk preview. Both require the desk to have staged the order first; nothing
  reaches the venue unstaged.
- `auto` (EMA regime entries) and `mode = "mt4"/"mt5"` (live) are separate gates, both off by
  default (`account.mode = "paper"`, `desk.auto = false`). A real account additionally needs
  `--i-accept-risk` at start or `/live on I-ACCEPT-RISK` (the exact phrase) in the locked chat.

## Running it

`pip install -e ".[dev]"`, then `pytest` and `python -m straightedge doctor`; do not start a
live loop until `doctor` exits 0. CI (`.github/workflows/ci.yml`) matrixes `ubuntu-latest` /
`windows-latest` x Python 3.12/3.13, all GitHub-hosted. `docs/RUNBOOK.md` covers paper vs.
live, the launchd unit, HALT, confirm-on-restart, and journal rotation; `SECURITY.md` covers
secret handling and file permissions (journal/lock/heartbeat/HALT are chmod 0600, process
umask 077).
