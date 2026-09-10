# Runbook

The bot is the Python process on this computer.
The desk is Telegram chat commands.
The agent is the Cloudflare Computer worker.
The gateway is Cloudflare AI Gateway `mt5-risk-bot`.

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
Doctor is non-zero if the ping fails or the paper round-trip fails.
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

Keep that in a terminal, tmux, or the user LaunchAgent in `docs/launchd.plist.example`.
Ctrl-C or `launchctl bootout` stops it.

### MT5 loop

MetaTrader 5.app must already be running and logged in.
`doctor --connect` is the gate (binding, login, `trade_mode`).
It is non-zero if the binding is missing or login fails.
Do not start live on a traceback.
Demo is `trade_mode=0` and does not need `--i-accept-risk`.

WARNING
Real money (`trade_mode=2`) is refused without `--i-accept-risk`.

1. Run doctor with a login check.
   `python -m mt5_risk_bot --config config.toml doctor --connect`
2. Stop if doctor is not 0.
3. Start the live loop for demo.
   `python -m mt5_risk_bot --config config.toml run --mode mt5 --loop`
4. For a real account only, add `--i-accept-risk`.
   `python -m mt5_risk_bot --config config.toml run --mode mt5 --loop --i-accept-risk`

`--loop` polls until Ctrl-C.
It retries Telegram 429/5xx with backoff.
It resumes `getUpdates` from `journal.tg_offset` (next to `journal_path`).
A restart does not replay or drop commands.
A dropped terminal calls `initialize` again.
One bad tick is journaled (`reconnect` or `loop_error`).
The bot stays up.
`/halt` and a `HALT` file flatten and stay halted.
The bot does not exit, so `/resume` works without a restart.

CAUTION
Two `run --loop` on the same journal cannot run together.
The second exits 2 with `already running` on stderr (`journal.lock`).
Stop the first process, or set a different `engine.journal_path`.
Do not load the LaunchAgent and also run `--loop` in a terminal.

Each successful tick (account reached) writes `journal.heartbeat` next to the journal (ISO timestamp, chmod 0600).
A reconnect that fails does not update it.
Before a write that would exceed 10 MiB, the live journal is renamed to `journal.jsonl.1`.
That replaces any previous `.1`.

## Agent advice (the gateway)

The desk can send `/ask` to the agent instead of calling xAI or Anthropic directly.
Working memory is the Durable Object workspace (`notes.md`, `log.md`, `snapshot.md`).
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
The gateway would forward it to xAI as a provider key.

Worker secrets (never git): `CF_AIG_TOKEN`, `ADVICE_TOKEN`.
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

Same as demo, plus `--i-accept-risk`.
The bot refuses `trade_mode=2` without that flag.
Start with `risk_pct = 0.002` (0.2%) for the first weeks.
`doctor --connect` must still exit 0 first.

## Halt

```bash
touch HALT
```

Or send `/halt` from the locked chat.
The next loop iteration flattens this magic number (positions and working orders).
It drops the staged confirm.
It stops sending.
The bot stays up.
`/resume` only clears the operator file.
Daily-loss halt self-clears at the next UTC midnight.
Drawdown halt does not.
Inspect and restart for drawdown.
Daily-loss and max-drawdown cannot be cleared from Telegram.

The `HALT` path is relative to the process working directory (LaunchAgent `WorkingDirectory`).
Remove the file and send `/resume` when you intend to resume.
Restart if you are clearing drawdown.

`main()` sets umask 077 so files the process creates are owner-only.

## Confirm

`/confirm` TTL is `telegram.confirm_seconds` (default 120).
Staging writes `confirm_stage` to the journal.
`start` restores that intent if the last of `confirm_stage` / `confirm_cancel` / `confirm_sent` is still `confirm_stage`.
The TTL must not have expired.
After expiry, `/confirm` replies `nothing to confirm`.
Restage with `/buy` `/sell` `/reverse` or advice.

MT5 positions and working orders stay in the terminal.
Paper positions and paper working orders die with the process.

## Chat lock

Only `TELEGRAM_CHAT_ID` is accepted.
Set it in the environment.
Do not put `chat_id` or the token in `config.toml`.
Updates from any other chat are ignored (the update is still consumed).
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
The LaunchAgent starts the Python loop only.
It does not launch the terminal.

### LaunchAgent

1. Run doctor. It must exit 0.
   `python -m mt5_risk_bot doctor`
2. Copy `docs/launchd.plist.example` to
   `~/Library/LaunchAgents/org.skyphusion.mt5-risk-bot.plist`.
3. Edit `WorkingDirectory`.
4. Edit the venv `python` path.
5. Edit `EnvironmentVariables` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
6. Add `XAI_API_KEY` or `ANTHROPIC_API_KEY` if you want advice.
7. For an MT5 loop, change `--mode paper` to `--mode mt5`.
8. For a real account only, append `--i-accept-risk`.
9. Put `--config` and the path before `run` in `ProgramArguments`.
10. chmod 600 the installed plist. Never commit it.

The committed example keeps `REPLACE_ME`, `KeepAlive`, and `Umask` 63 (077).
Watchdog: `journal.heartbeat` next to `journal_path` under `WorkingDirectory`.

11. Create a log directory under `WorkingDirectory`.
    `mkdir -p logs`
    Or point the log keys somewhere writable.
    `*.log` is gitignored.
12. Load the agent.

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
The flock is released when the process dies, so the new process can acquire `journal.lock`.
A leftover `journal.lock` file is not a held lock.
The confirm is restored from the live journal if the TTL has not expired.
Halt does not crash the process.
Do not bootout to halt.

## Desk setup

`run` will not start without `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

1. Talk to BotFather.
2. Copy the token.
3. Message the bot.
4. Set `TELEGRAM_CHAT_ID` to that chat.
5. Export a model key.
   `export XAI_API_KEY=...` (Grok)
   and/or `export ANTHROPIC_API_KEY=...` (Claude)
6. Send a test ping.
   `python -m mt5_risk_bot telegram --message ping`
7. Run doctor. It must exit 0.
   `python -m mt5_risk_bot doctor`
8. Start the paper or mt5 loop above.

Free text is `/ask`.
Context includes `/risk`, positions, working orders, and quotes.
A recommended market, limit, stop, or close-ticket is staged.
`/confirm` sends it through the risk engine.
`/auto on` is the only way the EMA regime trades on its own.
`/trail on` trails open positions each tick without turning auto on.
SL/TP hits and pending fills still alert in Telegram when auto is off.

Paper is the default (`account.mode = "paper"`).
Real accounts still need `--i-accept-risk`.

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

`/halt` writes `HALT`, drops the confirm, cancels working orders, and flattens positions.
`/resume` only clears that file.
Daily-loss and max-drawdown cannot be cleared from Telegram.

`/closeby` is hedge-account only.
`/reverse` is two market sends (close then opposite).
The circuit can leave you flat after `/confirm` already closed the ticket.
See Live desk limits.

## Live desk limits

WARNING
Nothing here guarantees profit.
Paper P/L is not live P/L.

`/closeby TICKET OTHER` is hedge-account only.
It sends `TRADE_ACTION_CLOSE_BY`.
A netting terminal refuses CLOSE_BY.
You cannot hold two tickets on one symbol, so there is no opposite ticket to close against.
Paper always hedges (a new ticket per deal).
Close-by works in paper even when a live netting account would not.

`/reverse TICKET` is two market sends.
`/reverse` stages.
`/confirm` first closes the ticket, then sends the opposite side.
Staging and the first confirm preview exclude that ticket.
If halt, daily-loss, drawdown, or `risk_pct` refuse then, the ticket stays open.
After the close send succeeds, preview runs again.
Realized P/L can trip the circuit or leave no room to size the new side.
You are left flat (`closed #TICKET; reverse refused: ...`).
A failed opposite send is the same shape (`closed #TICKET; send failed ...`).

Production live: `doctor --connect` must exit 0 before `run --mode mt5`.
`trade_mode=2` also needs `--i-accept-risk`.
Demo (`trade_mode=0`) does not.

## Journal

JSONL, one event per line: `start`, `open`, `close`, `modify`, `reject`, `halt`, `order_check_fail`, `pending`, `recap`, `reconnect`, `loop_error`, `confirm_stage`, `confirm_cancel`, `confirm_sent`, `stop`.
Grep `reject` if it never trades.
`outside_session` and `no_regime` are the usual reasons.
`reconnect` is an MT5 IPC drop then `initialize`.
`loop_error` is a tick that raised.
The bot kept running.
`journal.tg_offset` is the Telegram `getUpdates` cursor (not JSONL).
`journal.lock` is an exclusive flock so two loops cannot share the journal or offset.
`journal.heartbeat` is an ISO timestamp rewritten each successful `step_all`.
Before a write that would exceed 10 MiB, the live file is renamed to `journal.jsonl.1` (one generation; the previous `.1` is replaced).
`tail` and confirm restore read only the live file.
`journal.jsonl`, `journal.jsonl.1`, `journal.tg_offset`, `journal.lock`, and `journal.heartbeat` are owner-only (chmod 0600).
