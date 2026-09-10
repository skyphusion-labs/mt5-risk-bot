# Runbook

Paper is the default. Nothing here guarantees profit.

## Paper first

```bash
python -m mt5_risk_bot doctor
python -m mt5_risk_bot backtest --market trend --no-session-filter
python -m mt5_risk_bot backtest --market range --no-session-filter
```

`doctor` is the gate. It pings Telegram if the token is set, then paper
`/buy` `/confirm` `/close`. No live terminal. Non-zero if the ping fails
or the paper round-trip fails. Do not start a long-run, and do not go
live, until it exits 0.

Trend should finish above start on the seeded generator. Range should not
ruin the account. If either assertion fails on your machine, do not go live.

CSV backtest (unix timestamps in `time`):

```bash
python -m mt5_risk_bot backtest --csv path/to/ohlc.csv --symbol EURUSD --no-session-filter
```

## Seed paper from a live terminal (no orders)

Requires a running MT5 and a binding (`MetaTrader5` or `mt5-mac`).

```bash
python -m mt5_risk_bot run --mode paper --feed-mt5 --config config.toml
```

Orders stay in the in-process broker.

## Production long-run (macOS)

Telegram is required. `run` exits 2 without both `TELEGRAM_BOT_TOKEN`
and `TELEGRAM_CHAT_ID`. Only `TELEGRAM_CHAT_ID` is accepted; updates from
any other chat are ignored.

Paper loop (default; no terminal):

```bash
python -m mt5_risk_bot doctor
python -m mt5_risk_bot run --mode paper --loop --config config.toml
```

Keep that in a terminal, tmux, or the user LaunchAgent in
`docs/launchd.plist.example`. Ctrl-C or `launchctl bootout` stops it.

MT5 loop: MetaTrader 5.app must already be running and logged in. `doctor
--connect` is the gate (binding, login, `trade_mode`). Non-zero if the
binding is missing or login fails; do not start live on a traceback.
Demo is `trade_mode=0` and does not need `--i-accept-risk`. Real money
(`trade_mode=2`) is refused without `--i-accept-risk`.

```bash
python -m mt5_risk_bot doctor --connect --config config.toml
python -m mt5_risk_bot run --mode mt5 --loop --config config.toml
# real account only:
python -m mt5_risk_bot run --mode mt5 --loop --config config.toml --i-accept-risk
```

`--loop` polls until Ctrl-C. It retries Telegram 429/5xx with backoff and
resumes `getUpdates` from `journal.tg_offset` (next to `journal_path`).
A restart does not replay or drop commands. A dropped terminal calls
`initialize` again. One bad tick is journaled (`reconnect` or
`loop_error`); the process stays up. `/halt` and a `HALT` file flatten
and stay halted; the process does not exit, so `/resume` works without a
restart. Two `run --loop` on the same journal cannot run together:
the second exits 2 with `already running` on stderr (`journal.lock`).
Stop the first process, or set a different `engine.journal_path`.
Do not load the LaunchAgent and also run `--loop` in a terminal.
Each successful tick (account reached) writes `journal.heartbeat`
next to the journal (ISO timestamp, chmod 0600). A reconnect that
fails does not update it. Before a write that would exceed 10 MiB,
the live journal is renamed to `journal.jsonl.1` (replacing any
previous `.1`).

## Demo

1. Broker demo account. Enable AutoTrading.
2. `export MT5_LOGIN MT5_PASSWORD MT5_SERVER` (never commit these).
3. `account.mode = "mt5"` in `config.toml`.
4. `python -m mt5_risk_bot doctor --connect --config config.toml` (exit 0;
   non-zero if the binding is missing or login fails).
5. `python -m mt5_risk_bot run --mode mt5 --loop --config config.toml`
6. Confirm `trade_mode=0` in the doctor output.

Leave it running through at least one full session window. Read `journal.jsonl`.

## Real money

Same as demo, plus `--i-accept-risk`. The bot refuses `trade_mode=2` without
that flag. Start with `risk_pct = 0.002` (0.2%) for the first weeks. `doctor
--connect` must still exit 0 first.

## Halt

```bash
touch HALT
```

or send `/halt` from the locked chat. Next loop iteration flattens this
magic number (positions and working orders), drops the staged confirm,
and stops sending. The process stays up. `/resume` only clears the
operator file. Daily-loss halt self-clears at the next UTC midnight.
Drawdown halt does not; inspect and restart. Daily-loss and max-drawdown
cannot be cleared from Telegram.

The `HALT` path is relative to the process working directory (LaunchAgent
`WorkingDirectory`). Remove the file and `/resume` (or restart, if you
are clearing drawdown) when you intend to resume.

`main()` sets umask 077 so files the process creates are owner-only.

## Confirm

`/confirm` TTL is `telegram.confirm_seconds` (default 120). Staging
writes `confirm_stage` to the journal. `start` restores that intent if
the last of `confirm_stage` / `confirm_cancel` / `confirm_sent` is still
`confirm_stage` and the TTL has not expired. After expiry, `/confirm`
replies `nothing to confirm`. Restage with `/buy` `/sell` `/reverse` or
advice.

MT5 positions and working orders stay in the terminal. Paper positions
and paper working orders die with the process.

## Chat lock

Only `TELEGRAM_CHAT_ID` is accepted. Set it in the environment. Do not
put `chat_id` or the token in `config.toml`. Updates from any other
chat are ignored (the update is still consumed). Replies and notifies
go only to that chat.

## macOS

Homebrew has Python, not MetaTrader. Install the terminal from
metatrader5.com, log in once by hand, then:

```bash
pip install mt5-mac
python -m mt5_risk_bot doctor --connect --config config.toml
```

If `initialize` fails, launch MetaTrader 5.app yourself and wait until it
is fully up. The LaunchAgent starts the Python loop only; it does not
launch the terminal.

### LaunchAgent

1. `python -m mt5_risk_bot doctor` must exit 0.
2. Copy `docs/launchd.plist.example` to
   `~/Library/LaunchAgents/org.skyphusion.mt5-risk-bot.plist`.
3. Edit `WorkingDirectory`, the venv `python` path, and
   `EnvironmentVariables` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
   Add `XAI_API_KEY` or `ANTHROPIC_API_KEY` if you want advice.
   For an MT5 loop, change `--mode paper` to `--mode mt5` and, for a
   real account only, append `--i-accept-risk`. chmod 600 the installed
   plist. Never commit it. The committed example keeps `REPLACE_ME`
   and `KeepAlive`.
4. `mkdir -p logs` under `WorkingDirectory` (or point the log keys
   somewhere writable). `*.log` is gitignored.
5. Load:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.skyphusion.mt5-risk-bot.plist
launchctl print gui/$(id -u)/org.skyphusion.mt5-risk-bot
```

Stop:

```bash
launchctl bootout gui/$(id -u)/org.skyphusion.mt5-risk-bot
```

`KeepAlive` restarts a crash. The flock is released when the process
dies, so the new process can acquire `journal.lock`. A leftover
`journal.lock` file is not a held lock. The confirm is restored from
the live journal if the TTL has not expired. Halt does not crash the
process; do not bootout to halt.

## Telegram desk

`run` will not start without `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

1. BotFather, copy the token.
2. Message the bot, set `TELEGRAM_CHAT_ID` to that chat.
3. `export XAI_API_KEY=...` (Grok) and/or `ANTHROPIC_API_KEY=...` (Claude).
4. `python -m mt5_risk_bot telegram --message ping`
5. `python -m mt5_risk_bot doctor` (exit 0), then the paper or mt5 loop above.

Free text is `/ask`. Context includes `/risk`, positions, working orders,
and quotes. A recommended market, limit, stop, or close-ticket is staged;
`/confirm` sends it through the risk engine. `/auto on` is the only way
the EMA regime trades on its own. `/trail on` trails open positions
each tick without turning auto on. SL/TP hits and pending fills still
alert in Telegram when auto is off.

Paper is the default (`account.mode = "paper"`). Real accounts still need
`--i-accept-risk`.

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

`stop=` is the same shape (`/sell EURUSD stop=... sl=... tp=...`). Do not
set both `limit=` and `stop=`. Bare `/cancel` drops a staged confirm;
`/cancel TICKET` cancels a working order.

`/halt` writes `HALT`, drops the confirm, cancels working orders, and
flattens positions. `/resume` only clears that file. Daily-loss and
max-drawdown cannot be cleared from Telegram.

`/closeby` is hedge-account only. `/reverse` is two market sends (close
then opposite); the circuit can leave you flat after `/confirm` already
closed the ticket. See Live desk limits.

## Live desk limits

Nothing here guarantees profit. Paper P/L is not live P/L.

`/closeby TICKET OTHER` is hedge-account only. It sends
`TRADE_ACTION_CLOSE_BY`. A netting terminal refuses CLOSE_BY: you cannot
hold two tickets on one symbol, so there is no opposite ticket to close
against. Paper always hedges (a new ticket per deal), so close-by works
in paper even when a live netting account would not.

`/reverse TICKET` is two market sends. `/reverse` stages; `/confirm` first
closes the ticket, then sends the opposite side. Staging and the first
confirm preview exclude that ticket; if halt, daily-loss, drawdown, or
risk_pct refuse then, the ticket stays open. After the close send
succeeds, preview runs again. Realized P/L can trip the circuit or leave
no room to size the new side; you are left flat (`closed #TICKET; reverse
refused: ...`). A failed opposite send is the same shape (`closed #TICKET;
send failed ...`).

Production live: `doctor --connect` must exit 0 before `run --mode mt5`.
`trade_mode=2` also needs `--i-accept-risk`. Demo (`trade_mode=0`) does not.

## Journal

JSONL, one event per line: `start`, `open`, `close`, `modify`, `reject`,
`halt`, `order_check_fail`, `pending`, `recap`, `reconnect`, `loop_error`,
`confirm_stage`, `confirm_cancel`, `confirm_sent`, `stop`. Grep `reject`
if it never trades; `outside_session` and `no_regime` are the usual
reasons. `reconnect` is an MT5 IPC drop then `initialize`. `loop_error`
is a tick that raised; the process kept running. `journal.tg_offset` is
the Telegram `getUpdates` cursor (not JSONL). `journal.lock` is an
exclusive flock so two loops cannot share the journal or offset.
`journal.heartbeat` is an ISO timestamp rewritten each successful
`step_all`. Before a write that would exceed 10 MiB, the live file
is renamed to `journal.jsonl.1` (one generation; the previous `.1`
is replaced). `tail` and confirm restore read only the live file.
`journal.jsonl`, `journal.jsonl.1`, `journal.tg_offset`,
`journal.lock`, and `journal.heartbeat` are owner-only (chmod 0600).
