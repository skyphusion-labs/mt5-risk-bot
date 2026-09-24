# Contract

Code that disagrees with this file is wrong.

The bot is the Python process on this computer.
The desk is Telegram chat commands.
The agent is the Cloudflare Computer worker.
The gateway is Cloudflare AI Gateway `mt5-risk-bot`.
The circuit is halt, daily-loss, and drawdown gates.
The risk engine is the sizer in the bot (`RiskManager.evaluate`).

You trade and ask for advice from one chat.
Paper, MetaTrader 5, or MetaTrader 4 is the execution venue.
The risk engine is the only thing that can size or refuse an order.
The venue API is `Broker` (`MarketOrder`, `WorkingOrder`). Engine does not send MT5 request dicts.
Auto EMA trading is off until `/auto on`.

## Allowed claims

| Claim | Fact |
| --- | --- |
| Desk required | Telegram is required for `run`. |
| Desk trades | Slash commands place, close, and modify trades. |
| Free text | Free text goes to the configured model. |
| Advice send | Default: advice is staged. `/confirm` sends. `/approve always`: after risk preview, send. Risk can still refuse. |
| Confirm | A staged suggestion waits for `/confirm` (default 120s). `/approve always` skips that wait after a successful risk preview. |
| Approve | `/approve always` sends after risk preview. No `/confirm`. Default off. `/approve off` restores staging. Circuit and `risk_pct` still refuse. Halt still refuses. Paper and demo accept `/approve always` at any time. On `trade_mode=2` without live armed, `/approve always` is refused until `/live on I-ACCEPT-RISK`. Last of `approve_always` / `approve_off` in `journal.jsonl` restores on start. |
| Live from chat | Real-money sends need `live_accepted`. Set it with `--i-accept-risk` at start, or `/live on I-ACCEPT-RISK` in the locked chat. The phrase is required. `/live on` without it is usage. `/live off` clears it. Arming is PER PROCESS and is never restored from the journal: a restart always starts disarmed, and a `live_on` record writes `live_not_restored` instead. Same fuse as `--i-accept-risk`. Risk still sizes and can refuse. |
| Size | Every new order is sized so a full stop-out loses at most `risk_pct` of equity (default 0.5%). |
| Min lot | If the broker minimum lot would exceed that, the trade is skipped. |
| Halt room | A sized order is also measured against what the account may still lose before the daily-loss or drawdown halt. A volume whose full stop-out would carry the account through either halt is refused as `size_exceeds_risk`, before the halt trips. Those budgets come from `journal.equity.json`, which the sizer never reads, so this gate can refuse a size the sizer was content with. A per-trade risk above `daily_loss_pct` refuses every entry. |
| Daily loss | Daily loss of `daily_loss_pct` (default 2%) of start-of-UTC-day equity flattens positions for the bot's magic and halts until the next UTC day. A restart does not clear it. `day_start_equity` is restored from `journal.equity.json` when the UTC day is the same. |
| Drawdown | Drawdown of `max_drawdown_pct` (default 10%) from peak equity flattens and stays halted. `peak_equity` is restored from `journal.equity.json`, so a restart does not clear it. To reset the peak, stop the bot and delete that file. |
| Halt | `HALT` or `/halt` flattens immediately (positions and working orders). |
| Risk state | `journal.equity.json` holds `day_key`, `day_start_equity`, and `peak_equity` next to the journal. It is the INPUT the gates recompute from, never a stored verdict. The write is atomic (temp file, then rename). |
| State unreadable | A snapshot that is corrupt, truncated, or from a newer version halts with reason `state_unreadable`. The file is not changed. This is COULD NOT MEASURE, not a clean start. Inspect it, then delete it to start clean; that also resets the peak. |
| State unwritable | A snapshot that cannot be written halts with reason `state_unwritable`. The next restart would lose the loss budget, so the bot refuses to trade. |
| Flatten proof | A flatten counts what it closed. It reports `requested`, `confirmed_closed`, `closed_elsewhere`, and the survivor tickets, to `journal.jsonl` as `flatten`. A close is confirmed only when the filled volume covers the whole position, so a partial fill (`DONE_PARTIAL`) is residual risk, never a close. An unreadable book is COULD NOT MEASURE, which counts as an incomplete sweep, not a clean one. |
| Flatten failure | A sweep that leaves risk open writes `flatten_incomplete` and alerts `FLATTEN INCOMPLETE: n still open` with the tickets. `notify_events` cannot silence that alert. Survivors are never marked already-seen, so they alert again. The halt still holds: a failed flatten stops new entries. |
| Real money | Real-money accounts (`trade_mode = 2`) refuse orders unless `--i-accept-risk` was passed at start or `/live on I-ACCEPT-RISK` was sent in the locked chat. |
| Fills SSOT | `journal.jsonl` is the source of truth for fills the bot observed. Pending fills write `open` with `fill=true`. Vanished tickets write `close` with `fill=true`. The venue holds the live book. It is not the fill log. |
| Paper default | Paper is the default mode. |
| Alerts | SL/TP hits and pending-order fills emit Telegram alerts even when `/auto` is off. |
| Notify | Default notify events include `open`, `close`, `pending`, and `recap`. `flatten_incomplete` is always sent, whatever `notify_events` says. |
| Recap | A UTC day roll sends a recap notify (`journal.tail`, equity vs `day_start`). That is not a trade. `/recap` dumps the same snapshot. |
| Secrets | Secrets live in the environment: `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `XAI_API_KEY`, `ANTHROPIC_API_KEY`, `AI_PROVIDER`, `ADVICE_URL`, `ADVICE_TOKEN`. |
| Agent billing | `AI_PROVIDER=computer` posts to the agent. The agent bills through the gateway (`CF_AIG_TOKEN`), not a provider key. |
| Redact | Journal writes, `loop_error` stderr, and Telegram `send` redact BotFather tokens. Named secret keys in the journal become `[REDACTED]`. |
| File mode | `journal.jsonl`, `journal.jsonl.1`, `journal.tg_offset`, `journal.equity.json`, `journal.lock`, `journal.heartbeat`, and `HALT` are chmod 0600 on Unix. The bot sets umask 077. Windows has no POSIX mode bits; the lock is still exclusive. |
| Sender lock | `TELEGRAM_ALLOW_SENDERS` (or `telegram.allow_senders`) lists the sender ids that may command the desk. Every command is checked, read-only included. A sender that cannot be read is refused. A negative (shared) chat id with an empty list refuses to start. A refusal is journaled as `command_rejected` and is not answered. |
| Sizer | `RiskManager.evaluate` is the only sizer. It is not optional. |
| Handover posture | `telegram.allow_approve_always` and `telegram.allow_auto` (env: `TELEGRAM_ALLOW_APPROVE_ALWAYS`, `TELEGRAM_ALLOW_AUTO`) gate `/approve always` and `/auto on`. Both default true: unset, behaviour is unchanged. Set false, the command is refused with a named reason (`approve_always_disabled`, `auto_disabled`), journaled as `reject` (`source=telegram`), and never answered in chat. A value that is present but not a clean boolean is read as false, never as the default: a bad env var or a config typo can only remove the capability, never grant it. `/approve off` and `/auto off` are never refused. `config.handover.toml` sets both false. The default stays true on purpose (flipping it would silently change every existing deployment); the gap that leaves is closed by observability, not by a stricter default: `doctor` and `run` print the posture, and every `start` journal record carries `approve_always_allowed` / `auto_allowed`, so a session's posture is readable both live and after the fact. |
| Refusal event naming | `command_rejected` (PR #40) answers WHO: a sender that is not on the allow-list. `reject` (PR #49, and the handover posture above) answers WHAT: a specific command that this deployment's policy or risk state does not permit, regardless of who sent it. Do not merge the two event names or re-litigate this split per issue; a refusal is either an identity question or a policy question, never both at once. |
| Refusal record | Every gate that refuses writes `reject` to `journal.jsonl` with the NAMED `reason`, plus `source` (`auto`, `telegram`, `advice`) and `stage` (which gate, on which leg). The auto, desk, and advice paths share that one event name. A refusal is journaled and is never broadcast to the chat that triggered it. With no journal configured it still prints to stderr. |
| Advice record | An advice turn writes `advice_turn`: `provider`, `session`, `action`, `symbol`, `sl`, `tp`, `limit`, `stop`, `ticket`, `staged`. The question and the reply are never journaled. A suggestion the circuit refuses to stage writes `advice_circuit_block` with the circuit `reason`. |
| Unmeasured is not refused | An advice action that could not be turned into an order at all writes `advice_stage_failed` with `measured=false`, never `reject`. COULD NOT MEASURE stays distinct from REFUSED. |
| Auto arming | `/auto on` and `/auto off` write `auto_on` and `auto_off`, the audit trail `/live` and `/approve` already had. |

## Forbidden claims

| Claim | Fact |
| --- | --- |
| Returns | Do not claim consistent positive returns. |
| Blind follow | Do not claim that Grok or Claude is a signal you must follow blindly. |
| Paper equals live | Paper P/L is not live P/L. |
| Paper fill | Paper fills at bid/ask. |
| Same bar | Same-bar SL and TP: SL wins. |
| Paper pending | Paper pending limit/stop fills on tick (bid/ask vs price) or bar (high/low vs price). |

## Modes

| Mode | Orders | Data |
| --- | --- | --- |
| `paper` | in-process PaperBroker | synthetic, `--feed-mt5`, or empty |
| `mt5` | terminal `order_send` | live terminal |
| `mt4` | Expert mailbox (`docs/MT4.md`) | live terminal + `Mt4RiskBot.mq4` |

## Telegram commands

| Command | Effect |
| --- | --- |
| `/quote [SYMBOL]` | Show one symbol. Omit SYMBOL to show all configured symbols. |
| `/risk` | Show daily-loss and drawdown room vs caps. |
| `/buy` `/sell` SYMBOL `[sl=] [tp=] [limit=PRICE] [stop=PRICE]` | Stage a market order, or a working limit/stop. Do not set both limit and stop. |
| `/confirm` | Market: reprice to the live tick, preview, send. Limit/stop: preview at the staged price, send. |
| `/approve always\|off` | always: after risk preview, send. No `/confirm`. Default off. |
| `/live on I-ACCEPT-RISK\|off` | Arm or disarm real-money sends from chat. Phrase required. Same fuse as `--i-accept-risk`. |
| `/cancel` | Drop the staged confirm. |
| `/cancel TICKET` | Cancel a working order. |
| `/replace TICKET PRICE` | Move a working order entry. Uses `TRADE_ACTION_MODIFY`. The circuit and `risk_pct` still refuse. |
| `/orders` | List working orders. |
| `/close TICKET\|SYMBOL\|all [VOL]` | Flatten or partial close. |
| `/closeby TICKET OTHER` | Hedge-account only. `TRADE_ACTION_CLOSE_BY` offsets two opposite tickets. Same symbol, opposite sides. Remainder 0 or at least `volume_min`. Not a new send. Netting terminals refuse CLOSE_BY. Paper always hedges. |
| `/reverse TICKET [sl=] [tp=]` | Two market sends: close the ticket, then the opposite side. `/confirm` is the send. Stage and preview exclude that ticket. Mirrors SL/TP distances if omitted. The circuit and `risk_pct` still refuse. After flatten they can leave you flat. |
| `/sl` TICKET PRICE | Modify a position or a working order. Success only if the broker applied it. |
| `/tp` TICKET PRICE `[VOL]` | Full TP, or scale-out VOL at PRICE (partial close when hit). The circuit still refuses. |
| `/be TICKET` | Move SL to entry. Never loosen. |
| `/trail on\|off\|TICKET` | on: `manage()` existing positions every tick. No EMA entries. Default off. TICKET: one-shot. Never loosen. |
| `/history` | Last journal events. |
| `/recap` | Equity vs UTC `day_start` plus `journal.tail`. Also sent on UTC day roll as notify `recap`. |
| `/symbols list\|add\|remove [SYMBOL]` | Configured book (runtime). Bare `/symbols` lists. Cannot drop the last name, or a name with positions/orders. |
| `/ask ...` or free text | Grok, Claude, or the agent. Local: last 40 turns in `journal.advice.json`. `AI_PROVIDER=computer`: Durable Object SQLite workspace (`/workspace/notes.md`, `log.md`, `snapshot.md`, `history.json` from `journal.tail`) plus Computer tools. Session is the Telegram chat id. JSON can stage. Default send is `/confirm`. `/approve always` sends after risk preview. Not Cloudflare D1. |
| `/model grok\|claude\|computer` | Switch provider. |
| `/auto on\|off` | Optional EMA regime. Fill alerts do not wait for this. Journaled as `auto_on` / `auto_off`. |
| `/status` `/positions` `/halt` `/resume` | Account. `/halt` flattens, drops the confirm, and cancels working orders. |

## Confirm and send

`/confirm` for a market order reprices and re-runs `preview`.
A limit or stop keeps the staged price.
Halt, daily-loss, and drawdown still refuse.
The reply includes `ok` and `retcode`.
Only `OrderResult.ok` starts with `sent `.
A second `/buy` while a confirm is live is refused until `/cancel`.
Advice never overwrites a live confirm.
Close and SL/TP success replies come from `OrderResult.ok`.
They do not come from "the ticket existed".
`/sl` `/tp` on a working order uses `TRADE_ACTION_MODIFY` (paper supported).
Side geometry is kept (`buy: sl < price < tp`).
`/tp TICKET PRICE VOL` is a scale-out.
When PRICE is hit, only VOL closes.
Remainder keeps its SL.
Halt, daily-loss, and drawdown still refuse.
VOL must snap to lot step.
Remainder must be 0 or at least `volume_min`.

## Advice

Advice JSON fields: `action`, `symbol`, `sl`, `tp`, `limit`, `stop`, `ticket`, `summary`.
`limit` and `stop` are XOR.
A close action with `ticket` stages that close.
Default send is `/confirm`. `/approve always` sends after risk preview.
`/approve always` is available in paper and demo without a live fuse.
On `trade_mode=2`, arm live first (`--i-accept-risk` or `/live on I-ACCEPT-RISK`).
Context always includes `/risk`, positions, working orders, and quotes.
If the next order would trip the circuit, context says hold/close only.
Buy/sell is not staged.

Buy limit must be below ask.
Sell limit must be above bid.
Buy stop must be above ask.
Sell stop must be below bid.
`limit=` and `stop=` together are refused.
`/replace TICKET PRICE` keeps that geometry and existing SL/TP.
It does not send a new order.

## Reverse

`/reverse TICKET` stages a close plus the opposite market.
`/confirm` is two market sends, not one.
First it closes that ticket.
Then it sends an opposite market deal.
Default SL/TP mirror the open trade's distances around the live bid/ask.
Risk sizes the new side independently.
Staging and the first confirm preview exclude that ticket so `already_in_symbol` does not block.
Halt, daily-loss, drawdown, and `risk_pct` still refuse.
The ticket stays open if they refuse before the close send.
After the close send succeeds, preview runs again on the live book.
Realized P/L can trip the circuit.
It can also leave no room for `risk_pct`.
The reply is `closed #TICKET; reverse refused: ...`.
There is no opposite position then.
A failed opposite send is `closed #TICKET; send failed ...`.
Reverse is for open positions, not working orders.

## Close-by

`/closeby TICKET OTHER` is a flatten, not a new order.
Both tickets must use the bot's magic (20260909).
They must be the same symbol and opposite sides.
Overlap volume closes.
The larger side keeps the remainder.
Same ticket, same side, or a leftover below `volume_min` is refused.
Live CLOSE_BY is hedge-account only.
A netting terminal refuses it (one net position per symbol, no opposite ticket).
Paper always hedges: each deal is its own ticket.
Close-by works in paper even when a live netting account would not.
Paper P/L is not live P/L.

## Loop, fills, trail, session

Each loop tick resolves pending fills and SL/TP even when `/auto` is off.
Pending fills notify as `FILL/OPEN`.
SL/TP hits notify as `CLOSE` with `reason=sl` or `reason=tp`.
`/trail on` runs `manage()` on open positions every `step_all` tick.
It does not enable EMA entries.
`/auto on` still owns entries.
Trail default is off.

Manual `/buy` `/sell` skip the session window.
Auto does not.

## Loop survival

`run --loop` retries Telegram HTTP 429 and 5xx with backoff.
It resumes `getUpdates` at the same offset.
That offset is written next to the journal (`journal.tg_offset`).
The write happens after each update is handled or skipped.
A restart does not replay or drop commands.
A dropped MT5 IPC calls `initialize` again.
One failed poll, send, or broker tick is journaled (`reconnect` or `loop_error`).
The bot stays up.
Two `run --loop` processes cannot share a journal.
`run` takes an exclusive lock on `journal.lock` (same stem as `journal_path`).
Unix: `flock`. Windows: `msvcrt.locking`. Same fail (`already running`, exit 2).
A second `run --loop` prints `already running` to stderr and exits non-zero.
The lock is released on exit or crash.
Each `step_all` that reaches `account` writes `journal.heartbeat`.
The file is an ISO timestamp, chmod 0600, atomic replace.
A failed reconnect does not.
Before a journal write that would exceed 10 MiB, the live file is renamed to `<name>.1`.
That replaces any previous `.1`.
The new live file is chmod 0600.
`tail` and `last_event` (confirm restore) read only the live file.

## Confirm restore

A staged `/confirm` is journaled (`confirm_stage`).
`start` restores it if the last of `confirm_stage` / `confirm_cancel` / `confirm_sent` is still `confirm_stage`.
The TTL must not have expired.
`start` restores `/approve always` if the last of `approve_always` / `approve_off` is `approve_always`.
`start` does NOT restore `live_accepted`. Arming is per process. A `live_on` record makes `start` write `live_not_restored`, and `/live` says so. Re-arm with `/live on I-ACCEPT-RISK`.
`start` restores `day_key`, `day_start_equity`, and `peak_equity` from `journal.equity.json`.

## Doctor

`doctor` pings Telegram when the token is set.
It always runs an in-process paper `/buy` `/confirm` `/close`.
No live terminal is required.
`--connect` is the optional venue login check.
MT5: binding, login, `trade_mode`. Non-zero if the binding is missing or login fails.
MT4: mailbox ping plus `account` (`account.mode=mt4`, Expert attached).
`mt4.files_dir` / `MT4_FILES_DIR` is Common Files. On Windows, empty means
`%APPDATA%\\MetaQuotes\\Terminal\\Common\\Files`.
A traceback is not a pass.
Production live is `doctor --connect` then `run --mode mt5` or `run --mode mt4`.
`trade_mode=2` still needs `--i-accept-risk` at start, or `/live on I-ACCEPT-RISK` in the locked chat.

NOTE
Put `--config` before the subcommand.

## Gate

`pytest` with `--cov-fail-under=80`.
Required check names are `ci`, `coverage`, `CodeQL`. `ci` is an aggregator:
it needs the python matrix (`ci-matrix`, ubuntu-latest and windows-latest x
3.12/3.13) plus `agent-typecheck` and `agent-test`, and fails if any of them
did not succeed -- a matrix or agent job failing has teeth at the merge gate
without the org ruleset listing every leg by name.
`tests/` must stay green.
`agent/` must stay green: `npm run typecheck` and `npm test` in `agent/`.
`agent/` tests run in workerd via `@cloudflare/vitest-pool-workers`, not node.
The AI Gateway is the only hop the `agent/` suite replaces; it does so at the
outbound-request boundary, so nothing in `agent/src/` is stubbed.
