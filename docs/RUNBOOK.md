# Runbook

The bot is the Python process on this computer.
The desk is Telegram chat commands.
The agent is the Cloudflare Computer worker.
The gateway is Cloudflare AI Gateway `mt5-risk-bot`.
The circuit is halt, daily-loss, and drawdown gates.

WARNING
Paper is the default.
Nothing here guarantees profit.

NOTE
Put `--config` before the subcommand.
Example: `python -m mt5_risk_bot --config config.toml run --mode paper --loop`.

## Paper first

1. Run doctor.
   `python -m mt5_risk_bot doctor`
2. Stop if doctor is not 0.
3. Run a trend backtest.
   `python -m mt5_risk_bot backtest --market trend --no-session-filter`
4. Run a range backtest.
   `python -m mt5_risk_bot backtest --market range --no-session-filter`

`doctor` is the gate.
It pings Telegram if the token is set.
Then it runs paper `/buy` `/confirm` `/close`.
No live terminal is used.
`doctor` is non-zero if the ping fails or the paper round-trip fails.
Do not start a long run until doctor exits 0.
Do not go live until doctor exits 0.

On the seeded trend generator, equity must finish above start.
On the seeded range generator, the account must not be ruined.
If either check fails on your machine, do not go live.

CSV backtest (unix timestamps in `time`):

```bash
python -m mt5_risk_bot backtest --csv path/to/ohlc.csv --symbol EURUSD --no-session-filter
```

## Seed paper from a live terminal (no orders)

This needs a running terminal and a binding (`MetaTrader5` or `mt5-mac`).

```bash
python -m mt5_risk_bot --config config.toml run --mode paper --feed-mt5
```

Orders stay in the in-process broker.

## Docker paper (fleet)

Paper only. No MetaTrader in the image. Do not run this and a laptop
`--loop` on the same bot token. Two loops fight `getUpdates`.

Host: a fleet box that is not dischord. Example: jello.

1. Copy the repo to the host.
2. Copy `.env` (0600) to the repo root on the host. Do not put secrets in the image.
3. Run `docker compose build`.
4. Run `docker compose up -d`.
5. Check `docker compose logs -f desk`.
6. Send `/help` in Telegram.

Data is `./data` (journal, lock, heartbeat). Stop: `docker compose down`.
Host data dir owner must be uid 10001.

Live MT5 is not this image. See `deploy/LIVE.md`.

## Production long-run (macOS)

Telegram is required.
`run` exits 2 without both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
Only `TELEGRAM_CHAT_ID` is accepted.
Updates from any other chat are ignored.

### Paper loop

No terminal is required.

1. Run doctor.
   `python -m mt5_risk_bot doctor`
2. Stop if doctor is not 0.
3. Start the paper loop.
   `python -m mt5_risk_bot --config config.toml run --mode paper --loop`

4. Keep the bot in a terminal, tmux, or the LaunchAgent in `docs/launchd.plist.example`.
5. Stop it with Ctrl-C or `launchctl bootout`.

### MT5 loop

MetaTrader 5.app must already be running and logged in.
`doctor --connect` is the gate (binding, login, `trade_mode`).
It is non-zero if the binding is missing or login fails.
Do not start live on a traceback.
Demo is `trade_mode=0` and does not need `--i-accept-risk`.

WARNING
Real money (`trade_mode=2`) is refused without `--i-accept-risk` at start
or `/live on I-ACCEPT-RISK` in the locked chat.

1. Run doctor with a login check.
   `python -m mt5_risk_bot --config config.toml doctor --connect`
2. Stop if doctor is not 0.
3. Start the live loop for demo.
   `python -m mt5_risk_bot --config config.toml run --mode mt5 --loop`
4. For a real account, add `--i-accept-risk`, or arm from chat after start.
   `python -m mt5_risk_bot --config config.toml run --mode mt5 --loop --i-accept-risk`
   `/live on I-ACCEPT-RISK`

### MT4 loop

MetaTrader 4 has no official Python package.
Copy `mt4/Experts/Mt4RiskBot.mq4` into `MQL4/Experts`.
Compile it. Attach it to one chart. Enable AutoTrading.
Set `mt4.files_dir` (or `MT4_FILES_DIR`) to Common Files:

```
%APPDATA%\MetaQuotes\Terminal\Common\Files
```

`doctor --connect` is the gate (mailbox ping, `account`, `trade_mode`).
It is non-zero if the Expert is missing, the folder is wrong, or the ping times out.
Do not start live on a traceback.
Demo is `trade_mode=0` and does not need `--i-accept-risk`.

WARNING
Real money (`trade_mode=2`) is refused without `--i-accept-risk` at start
or `/live on I-ACCEPT-RISK` in the locked chat.

1. Set `account.mode = "mt4"` in config, or `export ACCOUNT_MODE=mt4`.
2. Run doctor with a login check.
   `python -m mt5_risk_bot --config config.toml doctor --connect`
3. Stop if doctor is not 0.
4. Start the live loop for demo.
   `python -m mt5_risk_bot --config config.toml run --mode mt4 --loop`
5. For a real account, add `--i-accept-risk`, or arm from chat after start.
   `python -m mt5_risk_bot --config config.toml run --mode mt4 --loop --i-accept-risk`
   `/live on I-ACCEPT-RISK`

See `docs/MT4.md` and `mt4/README.md`.

`--loop` polls until Ctrl-C.
`engine.poll_seconds` is the Telegram `getUpdates` timeout.
The example config sets `poll_seconds = 1`.
Each loop tick waits up to that many seconds for a chat update.
Then `step_all` runs fills, SL/TP, trail, and auto.
If the key is omitted, load uses 15.
Do not add a second sleep. The long poll is the wait.
It retries Telegram 429/5xx with backoff.
It resumes `getUpdates` from `journal.tg_offset` (next to `journal_path`).
A restart does not replay or drop commands.
A dropped terminal calls `initialize` again.
One bad tick is journaled (`reconnect` or `loop_error`).
The bot stays up.
`/halt` flattens positions and working orders.
A `HALT` file does the same.
The bot stays halted.
The bot does not exit.
`/resume` works without a restart.

CAUTION
Two `run --loop` on the same journal cannot run together.
The second exits 2 with `already running` on stderr (`journal.lock`).
Stop the first bot, or set a different `engine.journal_path`.
Do not load the LaunchAgent and also run `--loop` in a terminal.

Each successful tick writes `journal.heartbeat` next to the journal.
The tick must reach the account.
The file is an ISO timestamp, chmod 0600.
A reconnect that fails does not update it.
Before a write that would exceed 10 MiB, the live journal is renamed to `journal.jsonl.1`.
That replaces any previous `.1`.

## Agent advice

The desk can send `/ask` to the agent.
The desk does not need to call xAI or Anthropic directly.
Working memory is the Durable Object SQLite workspace
(`/workspace/notes.md`, `log.md`, `snapshot.md`, `history.json`).
`history.json` is the last journal records from `/ask`.
The agent does not use Cloudflare D1.
Inference is Unified Billing on the gateway.
The bot still does not send trades.

Live agent: `https://mt5-risk-agent.skyphusion.workers.dev/ask`
The gateway: `mt5-risk-bot` on account `fabcb25d9c7eb087110ec474a03e50d2`
Model: `xai/grok-4.6` via REST

```
POST https://api.cloudflare.com/client/v4/accounts/{id}/ai/v1/chat/completions
Authorization: Bearer CF_AIG_TOKEN
cf-aig-gateway-id: mt5-risk-bot
```

WARNING
Do not put `CF_AIG_TOKEN` on `gateway.ai.cloudflare.com` as `Authorization`.
The gateway forwards it to xAI as a provider key.

Agent secrets (never git): `CF_AIG_TOKEN`, `ADVICE_TOKEN`.
Laptop: `agent/.dev.vars` (0600, gitignored).

1. Load the laptop token file.
   `set -a`
2. Source it.
   `source agent/.dev.vars`
3. Stop exporting.
   `set +a`
4. Point the bot at the agent.
   `export AI_PROVIDER=computer`
   `export ADVICE_URL=https://mt5-risk-agent.skyphusion.workers.dev/ask`
5. Run doctor.
   `python -m mt5_risk_bot doctor`

`/model computer` at runtime.
Session is the Telegram chat id (one workspace per chat).
Redeploy: `cd agent && npx wrangler deploy` (needs `CLOUDFLARE_API_TOKEN`).
The agent is still a Cloudflare preview.

## Demo

1. Open a broker demo account.
2. Enable AutoTrading.
3. Export broker secrets. Never commit these.
   `export MT5_LOGIN=...`
   `export MT5_PASSWORD=...`
   `export MT5_SERVER=...`
4. Set `account.mode = "mt5"` in `config.toml`.
5. Run doctor with a login check.
   `python -m mt5_risk_bot --config config.toml doctor --connect`
6. Stop if doctor is not 0.
7. Start the live loop.
   `python -m mt5_risk_bot --config config.toml run --mode mt5 --loop`
8. Confirm `trade_mode=0` in the doctor output.

Leave it running through at least one full session window.
Read `journal.jsonl`.

## Real money

WARNING
The bot refuses `trade_mode=2` without `--i-accept-risk` at start
or `/live on I-ACCEPT-RISK` in the locked chat.

1. Complete the Demo steps.
2. Confirm `doctor --connect` exits 0.
3. Set `risk_pct = 0.002` (0.2%) at first.
4. Start the live loop with `--i-accept-risk`.
   `python -m mt5_risk_bot --config config.toml run --mode mt5 --loop --i-accept-risk`
5. Or start without that flag and arm from chat.
   `/live on I-ACCEPT-RISK`
   The phrase is required.
   `/live on` without it is usage.
   `/live off` disarms.
6. Then `/approve always` if you want sends without `/confirm`.
   On `trade_mode=2`, arm live before `/approve always`.

## Halt

1. Create the halt file, or send `/halt` from the locked chat.
   `touch HALT`
2. Wait for the next loop iteration.
   The bot flattens positions and working orders for the bot's magic (20260909).
   The bot drops the staged confirm.
   The bot stops sending.
   The bot stays up.
3. To resume an operator halt, remove the file and send `/resume`.
   `/resume` only clears the operator file.
4. Daily-loss halt self-clears at the next UTC midnight.
   Drawdown halt does not.
   Inspect and restart for drawdown.
   Daily-loss and max-drawdown cannot be cleared from Telegram.

The `HALT` path is relative to the bot working directory (LaunchAgent `WorkingDirectory`).
Restart if you are clearing drawdown.

`main()` sets umask 077 so files the bot creates are owner-only.

## Confirm

`/confirm` TTL is `telegram.confirm_seconds` (default 120).
Staging writes `confirm_stage` to the journal.
`start` restores that intent if the last of `confirm_stage` / `confirm_cancel` / `confirm_sent` is still `confirm_stage`.
The TTL must not have expired.
After expiry, `/confirm` replies `nothing to confirm`.
Restage with `/buy` `/sell` `/reverse` or advice.
`start` restores `/approve always` from the last of `approve_always` / `approve_off`.
`start` restores `live_accepted` from the last of `live_on` / `live_off`.

MT5 positions and working orders stay in the terminal.
Paper positions and paper working orders die with the bot.

## Chat lock

Only `TELEGRAM_CHAT_ID` is accepted.
Set it in the environment.
Do not put `chat_id` or the token in `config.toml`.
Updates from any other chat are ignored.
The bot still consumes those updates.
Replies and notifies go only to that chat.

## macOS

Homebrew has Python, not MetaTrader.
Install the terminal from metatrader5.com.
Log in once by hand.

1. Install the macOS binding.
   `pip install mt5-mac`
2. Run doctor with a login check.
   `python -m mt5_risk_bot --config config.toml doctor --connect`

If `initialize` fails, launch MetaTrader 5.app yourself.
Wait until it is fully up.
The LaunchAgent starts the bot only.
It does not launch the terminal.

### LaunchAgent

1. Run doctor. It must exit 0.
   `python -m mt5_risk_bot doctor`
2. Copy `docs/launchd.plist.example` to
   `~/Library/LaunchAgents/org.skyphusion.mt5-risk-bot.plist`.
3. Edit `WorkingDirectory`.
4. Edit the venv `python` path.
5. Edit `EnvironmentVariables` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
6. Add `XAI_API_KEY` or `ANTHROPIC_API_KEY` if you use advice.
7. For an MT5 loop, change `--mode paper` to `--mode mt5`. For MT4, `--mode mt4`.
8. For a real account, append `--i-accept-risk`, or arm from chat after start
   with `/live on I-ACCEPT-RISK`.
9. Put `--config` and the path before `run` in `ProgramArguments`.
10. chmod 600 the installed plist. Never commit it.

The committed example keeps `REPLACE_ME`, `KeepAlive`, and `Umask` 63 (077).
Watchdog: `journal.heartbeat` next to `journal_path` under `WorkingDirectory`.

11. Create a log directory under `WorkingDirectory`.
    `mkdir -p logs`
    Or point the log keys somewhere writable.
    `*.log` is gitignored.
12. Load the LaunchAgent.

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.skyphusion.mt5-risk-bot.plist
launchctl print gui/$(id -u)/org.skyphusion.mt5-risk-bot
```

Stop:

```bash
launchctl bootout gui/$(id -u)/org.skyphusion.mt5-risk-bot
```

`KeepAlive` restarts a crash.
`Umask` 63 is 077, matching `main()`.
Watchdog liveness is `journal.heartbeat` next to the journal (ISO ts, chmod 0600).
Stale mtime means the loop is not ticking.
The flock is released when the bot dies.
The new bot can acquire `journal.lock`.
A leftover `journal.lock` file is not a held lock.
The confirm is restored from the live journal if the TTL has not expired.
Halt does not crash the bot.
Do not bootout to halt.

## Desk setup

`run` will not start without `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

1. Talk to BotFather.
2. Copy the token.
3. In Telegram, send a message to the account for that token.
4. Set `TELEGRAM_CHAT_ID` to that chat.
5. Export a model key.
   `export XAI_API_KEY=...` (Grok)
   and/or `export ANTHROPIC_API_KEY=...` (Claude)
6. Send a test ping.
   `python -m mt5_risk_bot telegram --message ping`
7. Run doctor. It must exit 0.
   `python -m mt5_risk_bot doctor`
8. Start the paper, mt5, or mt4 loop above.

Free text is `/ask`.
Context includes `/risk`, positions, working orders, and quotes.
A recommended market, limit, stop, or close-ticket is staged.
`/confirm` is the default send.
`/approve always` sends after risk preview. No `/confirm` each time.
Paper and demo accept `/approve always` at any time.
On `trade_mode=2` without live armed, `/approve always` is refused.
`/approve off` restores staging.
The bot still sizes the order.
The bot can refuse it.
Real-money: `--i-accept-risk` at start, or `/live on I-ACCEPT-RISK` in the locked chat.
The phrase is required.
Then `/approve always` if you want sends without `/confirm`.
`/auto on` is the only way the EMA regime trades on its own.
`/trail on` trails open positions each tick.
It does not turn auto on.
SL/TP hits and pending fills still alert in Telegram when auto is off.

Paper is the default (`account.mode = "paper"`).
Real accounts still need `--i-accept-risk` at start, or `/live on I-ACCEPT-RISK` in the locked chat.

Stage a working order, then confirm:

```
/buy EURUSD limit=1.08000 sl=1.07800 tp=1.08300
/confirm
/orders
/cancel TICKET
/quote
/risk
/trail TICKET
/trail on
/sl TICKET PRICE
/tp TICKET PRICE
/tp TICKET PRICE VOL
/replace TICKET PRICE
/reverse TICKET
/closeby TICKET OTHER
/symbols
/symbols add NZDUSD
/symbols remove NZDUSD
/recap
```

`stop=` is the same shape (`/sell EURUSD stop=... sl=... tp=...`).
Do not set both `limit=` and `stop=`.
Bare `/cancel` drops a staged confirm.
`/cancel TICKET` cancels a working order.

`/halt` writes `HALT`.
It drops the confirm.
It cancels working orders.
It flattens positions.
`/resume` only clears that file.
Daily-loss and max-drawdown cannot be cleared from Telegram.

`/closeby` is hedge-account only.
`/reverse` is two market sends.
The first send closes the ticket.
The second send opens the opposite side.
The circuit can leave you flat after `/confirm` already closed the ticket.
See Live desk limits.

## Live desk limits

WARNING
Nothing here guarantees profit.
Paper P/L is not live P/L.

`/closeby TICKET OTHER` is hedge-account only.
It sends `TRADE_ACTION_CLOSE_BY`.
A netting terminal refuses CLOSE_BY.
You cannot hold two tickets on one symbol.
There is no opposite ticket to close against.
Paper always hedges (a new ticket per deal).
Close-by works in paper even when a live netting account would not.

`/reverse TICKET` is two market sends.
`/reverse` stages.
`/confirm` first closes the ticket.
Then it sends the opposite side.
Staging and the first confirm preview exclude that ticket.
If halt, daily-loss, drawdown, or `risk_pct` refuse then, the ticket stays open.
After the close send succeeds, preview runs again.
Realized P/L can trip the circuit.
It can also leave no room to size the new side.
You are left flat (`closed #TICKET; reverse refused: ...`).
A failed opposite send is the same shape (`closed #TICKET; send failed ...`).

Production live: `doctor --connect` must exit 0 before `run --mode mt5` or `run --mode mt4`.
`trade_mode=2` also needs `--i-accept-risk` at start, or `/live on I-ACCEPT-RISK` in the locked chat.
Demo (`trade_mode=0`) does not.

## Journal

`journal.jsonl` is the source of truth for fills the bot observed.
A pending fill writes `open` with `fill=true`.
A vanished ticket writes `close` with `fill=true`.
The venue holds the live book. It is not the fill log.

JSONL, one event per line: `start`, `open`, `close`, `modify`, `reject`, `halt`, `order_check_fail`, `pending`, `recap`, `reconnect`, `loop_error`, `confirm_stage`, `confirm_cancel`, `confirm_sent`, `approve_always`, `approve_off`, `live_on`, `live_off`, `stop`.
Grep `reject` if it never trades.
`outside_session` and `no_regime` are the usual reasons.
`reconnect` is an MT5 IPC drop then `initialize`.
`loop_error` is a tick that raised.
The bot kept running.
`journal.tg_offset` is the Telegram `getUpdates` cursor (not JSONL).
`journal.lock` is an exclusive flock so two loops cannot share the journal or offset.
`journal.heartbeat` is an ISO timestamp rewritten each successful `step_all`.
Before a write that would exceed 10 MiB, the live file is renamed to `journal.jsonl.1`.
That is one generation.
The previous `.1` is replaced.
`tail` and confirm restore read only the live file.
`journal.jsonl`, `journal.jsonl.1`, `journal.tg_offset`, `journal.lock`, and `journal.heartbeat` are owner-only (chmod 0600).
