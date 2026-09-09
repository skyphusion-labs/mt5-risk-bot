# mt5-risk-bot

Risk-first MetaTrader 5 bot. Paper by default. No profit guarantee.

Position size is `equity * risk_pct / stop distance` (0.5% default). Daily-loss
and max-drawdown circuits flatten this magic and halt. A `HALT` file or
Telegram `/halt` does the same immediately.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml
pytest
python -m mt5_risk_bot doctor
python -m mt5_risk_bot backtest --market trend --no-session-filter
```

Live/demo needs a running terminal. Official `MetaTrader5` is Windows-only.
On macOS: MetaTrader 5.app from metatrader5.com plus `pip install mt5-mac`.
Homebrew does not ship the terminal.

```bash
export MT5_LOGIN=...
export MT5_PASSWORD=...
export MT5_SERVER=YourBroker-Demo
python -m mt5_risk_bot run --mode mt5 --loop --config config.toml
```

Real accounts (`trade_mode=2`) also need `--i-accept-risk`.

## Telegram

```bash
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python -m mt5_risk_bot telegram --message ping
```

Commands (that chat only): `/status` `/positions` `/halt` `/resume` `/help`.

## Docs

| File | What |
| --- | --- |
| `docs/CONTRACT.md` | Behaviour the tests enforce |
| `docs/MT5-API.md` | Python API notes this bot uses |
| `docs/RUNBOOK.md` | Operate, halt, demo, live |

## License

MIT.
