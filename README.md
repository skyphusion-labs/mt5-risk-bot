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
python -m mt5_risk_bot doctor
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

`/quote` with no symbol lists the book. `/risk` shows daily-loss and
drawdown room. `/trail TICKET` moves SL using the ATR trail and never
loosens.

SL/TP and pending fills still alert in Telegram when `/auto` is off.
Paper is the default. Live real accounts need `--i-accept-risk`.

Free text is advice; if the model recommends a trade, that is staged too.
`/help` for the rest.

## Docs

`docs/CONTRACT.md` is the behaviour tests enforce.

## License

MIT.
