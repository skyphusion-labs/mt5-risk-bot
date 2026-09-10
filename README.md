# mt5-risk-bot

Risk-first MetaTrader 5 bot. Paper by default. No profit guarantee.

You trade from Telegram. The risk engine sizes every order. It can refuse.
Advice never sends. `/confirm` sends.

Auto EMA trading is off until `/auto on`.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export XAI_API_KEY=...
pytest
python -m mt5_risk_bot doctor
python -m mt5_risk_bot run --mode paper --loop --config config.toml
```

WARNING: Do not start a long-run until `doctor` exits 0.

Live and demo need a running terminal. Official `MetaTrader5` is Windows-only.
On macOS install MetaTrader 5.app from metatrader5.com. Then `pip install mt5-mac`.
Homebrew does not ship the terminal.

```bash
export MT5_LOGIN=...
export MT5_PASSWORD=...
export MT5_SERVER=YourBroker-Demo
python -m mt5_risk_bot doctor --connect --config config.toml
python -m mt5_risk_bot run --mode mt5 --loop --config config.toml
```

Real accounts (`trade_mode=2`) also need `--i-accept-risk`.

## Advice

Default: Grok via `XAI_API_KEY`. Claude via `ANTHROPIC_API_KEY` and
`AI_PROVIDER=claude`.

Computer (workspace memory, AI Gateway Unified Billing):

```bash
set -a && source agent/.dev.vars && set +a
export AI_PROVIDER=computer
export ADVICE_URL=https://mt5-risk-agent.skyphusion.workers.dev/ask
```

Do not commit `ADVICE_TOKEN`. See `agent/README.md`.

## Docs

| File | What |
| --- | --- |
| `docs/CONTRACT.md` | Behaviour the tests enforce |
| `docs/MT5-API.md` | Python API this bot uses |
| `docs/RUNBOOK.md` | Operate, halt, demo, live |
| `agent/README.md` | Computer worker and AI Gateway |
| `SECURITY.md` | Secrets and redaction |

## License

MIT.
