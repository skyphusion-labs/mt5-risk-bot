# mt5-risk-bot

Telegram desk for MetaTrader 5. You trade from chat. Grok or Claude
advises in the same chat. The risk engine sizes and can refuse. No
profit guarantee.

Auto EMA trading is **off** until you send `/auto on`.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export XAI_API_KEY=...          # Grok (default)
# export ANTHROPIC_API_KEY=...  # Claude
# export AI_PROVIDER=claude
pytest
python -m mt5_risk_bot doctor   # gate: telegram ping + paper /buy /confirm /close
python -m mt5_risk_bot run --mode paper --loop --config config.toml
```

`doctor` must exit 0 before a long-run or any live start. macOS LaunchAgent:
copy `docs/launchd.plist.example` (paper `--loop`; see `docs/RUNBOOK.md`).

`--loop` retries Telegram 429/5xx and re-`initialize`s a dropped MT5
IPC. `getUpdates` offset is `journal.tg_offset` next to the journal.
One bad tick is journaled; the process stays up. See `docs/RUNBOOK.md`.

Live/demo needs a running terminal. Official `MetaTrader5` is Windows-only.
On macOS: MetaTrader 5.app from metatrader5.com plus `pip install mt5-mac`.

```bash
export MT5_LOGIN=...
export MT5_PASSWORD=...
export MT5_SERVER=YourBroker-Demo
python -m mt5_risk_bot doctor --connect --config config.toml
python -m mt5_risk_bot run --mode mt5 --loop --config config.toml
```

Real accounts (`trade_mode=2`) also need `--i-accept-risk`.
`TELEGRAM_CHAT_ID` is the only accepted chat. Journal, stderr, and
chat echoes redact BotFather tokens (`[REDACTED]`). `/confirm` is restored
from the journal if the 120s TTL has not expired. Halt is `touch HALT`
or `/halt` (process stays up).

## Chat

`/buy EURUSD` stages a sized market order (ATR stop if you omit `sl=`).
`/buy EURUSD limit=1.08000 sl=... tp=...` (or `stop=`) stages a working
order. `/confirm` sends it. `/orders` lists working orders; `/cancel TICKET`
drops one. Bare `/cancel` drops a staged confirm.

`/quote` with no symbol lists the book. `/symbols list|add|remove`
edits that book at runtime. `/risk` shows daily-loss and
drawdown room. `/sl` `/tp` TICKET work on positions and working orders.
`/replace TICKET PRICE` moves a working order's entry.
`/reverse TICKET` stages a flip. `/confirm` is two market sends: close
the ticket, then the opposite side, sized by the risk engine. If the
circuit refuses after the close, you are left flat.
`/closeby TICKET OTHER` offsets two opposite hedges on the same
symbol (`TRADE_ACTION_CLOSE_BY`). Hedge accounts only; a netting
terminal refuses it. Paper always hedges. Remainder stays if volumes
differ. Paper P/L is not live P/L.
`/tp TICKET PRICE VOL` scales out VOL at PRICE; the rest stays.
`/trail TICKET` moves SL using the ATR trail and never loosens.
`/trail on` does that every tick for open positions and does not enable
EMA entries. Default off.

SL/TP and pending fills still alert in Telegram when `/auto` is off.
A UTC day roll sends a recap (equity vs day start, last journal lines).
`/recap` dumps that now. Not a trade.
Paper is the default. Live real accounts need `--i-accept-risk`.

Free text is advice. The model may stage a market, `limit=`, `stop=`, or
close-ticket order. `/confirm` is the only send. If the circuit would
halt, advice is hold/close only. `/help` for the rest.

## Docs

`docs/CONTRACT.md` is the behaviour tests enforce.
`docs/RUNBOOK.md` is paper, live, HALT, confirm-on-restart, and launchd.
`docs/launchd.plist.example` is a user LaunchAgent (paper `--loop`).

## License

MIT.
