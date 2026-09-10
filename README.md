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
python -m mt5_risk_bot doctor   # telegram ping + paper /buy /confirm /close
python -m mt5_risk_bot run --mode paper --loop --config config.toml
```

Live/demo needs a running terminal. Official `MetaTrader5` is Windows-only.
On macOS: MetaTrader 5.app from metatrader5.com plus `pip install mt5-mac`.

```bash
export MT5_LOGIN=...
export MT5_PASSWORD=...
export MT5_SERVER=YourBroker-Demo
python -m mt5_risk_bot run --mode mt5 --loop --config config.toml
```

Real accounts (`trade_mode=2`) also need `--i-accept-risk`.

## Chat

`/buy EURUSD` stages a sized market order (ATR stop if you omit `sl=`).
`/buy EURUSD limit=1.08000 sl=... tp=...` (or `stop=`) stages a working
order. `/confirm` sends it. `/orders` lists working orders; `/cancel TICKET`
drops one. Bare `/cancel` drops a staged confirm.

`/quote` with no symbol lists the book. `/symbols list|add|remove`
edits that book at runtime. `/risk` shows daily-loss and
drawdown room. `/sl` `/tp` TICKET work on positions and working orders.
`/tp TICKET PRICE VOL` scales out VOL at PRICE; the rest stays.
`/trail TICKET` moves SL using the ATR trail and never loosens.
`/trail on` does that every tick for open positions and does not enable
EMA entries. Default off.

SL/TP and pending fills still alert in Telegram when `/auto` is off.
A UTC day roll sends a recap (equity vs day start, last journal lines).
`/recap` dumps that now. Not a trade.
Paper is the default. Live real accounts need `--i-accept-risk`.

Free text is advice. The model may stage a market, `limit=`, `stop=`, or
close-ticket order. `/confirm` is the only send. `/help` for the rest.

## Docs

`docs/CONTRACT.md` is the behaviour tests enforce.

## License

MIT.
