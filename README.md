# mt5-risk-bot

The bot is the Python process on this computer.
The desk is Telegram chat commands.
The agent is the Cloudflare Computer worker.
The gateway is Cloudflare AI Gateway `mt5-risk-bot`.

You send desk commands from one Telegram chat.
The bot sizes every order and can refuse it.
Advice never sends an order.
`/confirm` is the only send.

WARNING
Nothing here guarantees profit.
Paper is the default.
Auto EMA trading is off until `/auto on`.

## Names

| Word | Meaning |
| --- | --- |
| the bot | the Python process on this computer |
| the desk | Telegram chat commands |
| the agent | the Cloudflare Computer worker |
| the gateway | Cloudflare AI Gateway `mt5-risk-bot` |

## Install and paper run

NOTE
Put `--config` before the subcommand.
Example: `python -m mt5_risk_bot --config config.toml run`.

1. Create a venv.
   `python3 -m venv .venv`
2. Activate the venv.
   `source .venv/bin/activate`
3. Install the bot.
   `pip install -e ".[dev]"`
4. Copy the example config.
   `cp config.example.toml config.toml`
5. Set the Telegram token.
   `export TELEGRAM_BOT_TOKEN=...`
6. Set the locked chat id.
   `export TELEGRAM_CHAT_ID=...`
7. Set a Grok key if you use default advice.
   `export XAI_API_KEY=...`
8. Run tests.
   `pytest`
9. Run doctor.
   `python -m mt5_risk_bot doctor`
10. Start the paper loop only if doctor exits 0.
    `python -m mt5_risk_bot --config config.toml run --mode paper --loop`

Do not start a long run until doctor exits 0.

For Claude advice:

```bash
export ANTHROPIC_API_KEY=...
export AI_PROVIDER=claude
```

For the agent:

```bash
set -a
source agent/.dev.vars
set +a
export AI_PROVIDER=computer
export ADVICE_URL=https://mt5-risk-agent.skyphusion.workers.dev/ask
```

See `agent/README.md` and `docs/RUNBOOK.md`.

## Loop facts

`--loop` retries Telegram HTTP 429 and 5xx.
`--loop` calls `initialize` again after a dropped MT5 IPC.
The Telegram offset is `journal.tg_offset` next to the journal.
One failed tick is journaled as `loop_error`.
The bot stays up.
Two `run --loop` processes cannot share one journal.
The second process prints `already running` and exits 2.
Each successful tick writes `journal.heartbeat`.
The live journal rotates to `journal.jsonl.1` at 10 MiB.

See `docs/RUNBOOK.md`.

macOS LaunchAgent: copy `docs/launchd.plist.example`.
See `docs/RUNBOOK.md` for load steps.

## Live MT5

Live and demo need a running terminal.
The official `MetaTrader5` package is Windows-only.
On macOS, install MetaTrader 5.app from metatrader5.com.
Then run `pip install mt5-mac`.

1. Set broker secrets.
   `export MT5_LOGIN=...`
   `export MT5_PASSWORD=...`
   `export MT5_SERVER=YourBroker-Demo`
2. Run doctor with a login check.
   `python -m mt5_risk_bot --config config.toml doctor --connect`
3. Stop if doctor is not 0.
4. Start the live loop.
   `python -m mt5_risk_bot --config config.toml run --mode mt5 --loop`

WARNING
A real account (`trade_mode=2`) also needs `--i-accept-risk`.

The bot accepts only `TELEGRAM_CHAT_ID`.
The journal, stderr, and chat echoes redact BotFather tokens as `[REDACTED]`.
`/confirm` is restored from the journal if the 120s TTL has not expired.
Halt is `touch HALT` or `/halt`.
The bot stays up after halt.
Journal, offset, lock, heartbeat, and HALT files are chmod 0600.
The bot sets umask 077.
Secrets stay in the environment.
See `SECURITY.md`.

## Desk

`/buy EURUSD` stages a sized market order.
The bot uses an ATR stop if you omit `sl=`.
`/buy EURUSD limit=1.08000 sl=... tp=...` stages a working order.
`stop=` is the same shape.
Do not set both `limit=` and `stop=`.
`/confirm` sends the staged order.
`/orders` lists working orders.
`/cancel TICKET` cancels a working order.
Bare `/cancel` drops a staged confirm.

`/quote` with no symbol lists the book.
`/symbols list|add|remove` edits the book at runtime.
`/risk` shows daily-loss and drawdown room.
`/sl` and `/tp` TICKET work on positions and working orders.
`/replace TICKET PRICE` moves a working order entry.
`/reverse TICKET` stages a close plus the opposite market.
`/confirm` then sends two market orders.
The first send closes the ticket.
The second send opens the opposite side.
The circuit can refuse the second send and leave you flat.
`/closeby TICKET OTHER` offsets two opposite hedges on the same symbol.
It uses `TRADE_ACTION_CLOSE_BY`.
Hedge accounts only.
A netting terminal refuses it.
Paper always hedges.
Paper P/L is not live P/L.
`/tp TICKET PRICE VOL` scales out VOL at PRICE.
The rest stays.
`/trail TICKET` moves SL with the ATR trail.
It never loosens.
`/trail on` does that every tick for open positions.
It does not turn on EMA entries.
Default is off.

SL/TP hits and pending fills still alert when `/auto` is off.
A UTC day roll sends a recap.
`/recap` dumps that recap now.
A recap is not a trade.

Free text is advice.
The model may stage a market, `limit=`, `stop=`, or close-ticket order.
`/confirm` is the only send.
If the circuit would halt, advice is hold or close only.
Send `/help` for the rest.

## Docs

`docs/CONTRACT.md` is the behaviour tests enforce.
`docs/RUNBOOK.md` is paper, live, HALT, confirm-on-restart, lock, heartbeat, journal rotate, and launchd.
`docs/launchd.plist.example` is a user LaunchAgent.
It uses paper `--loop`, `KeepAlive`, and `Umask` 63.
Tokens stay `REPLACE_ME` in the example.

## License

MIT.
