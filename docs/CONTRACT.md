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
| Live from chat | Real-money sends need `live_accepted`. Set it with `--i-accept-risk` at start, or `/live on I-ACCEPT-RISK` in the locked chat. The phrase is required. `/live on` without it is usage. `/live off` clears it. Last of `live_on` / `live_off` restores on start. Same fuse as `--i-accept-risk`. Risk still sizes and can refuse. |
| Size | Every new order is sized so a full stop-out loses at most `risk_pct` of equity (default 0.5%). |
| Min lot | If the broker minimum lot would exceed that, the trade is skipped. |
| Daily loss | Daily loss of `daily_loss_pct` (default 2%) of start-of-UTC-day equity flattens positions for the bot's magic and halts until the next UTC day. |
| Drawdown | Drawdown of `max_drawdown_pct` (default 10%) from peak equity flattens and stays halted until an operator inspects and restarts. |
| Halt | `HALT` or `/halt` flattens immediately (positions and working orders). |
| Real money | Real-money accounts (`trade_mode = 2`) refuse orders unless `--i-accept-risk` was passed at start or `/live on I-ACCEPT-RISK` was sent in the locked chat. |
| Fills SSOT | `journal.jsonl` is the source of truth for fills the bot observed. Pending fills write `open` with `fill=true`. Vanished tickets write `close` with `fill=true`. The venue holds the live book. It is not the fill log. |
| Paper default | Paper is the default mode. |
| Alerts | SL/TP hits and pending-order fills emit Telegram alerts even when `/auto` is off. |
| Notify | Default notify events include `open`, `close`, `pending`, and `recap`. |
| Recap | A UTC day roll sends a recap notify (`journal.tail`, equity vs `day_start`). That is not a trade. `/recap` dumps the same snapshot. |
| Secrets | Secrets live in the environment: `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `XAI_API_KEY`, `ANTHROPIC_API_KEY`, `AI_PROVIDER`, `ADVICE_URL`, `ADVICE_TOKEN`. |
| Agent billing | `AI_PROVIDER=computer` posts to the agent. The agent bills through the gateway (`CF_AIG_TOKEN`), not a provider key. |
| Redact | Journal writes, `loop_error` stderr, and Telegram `send` redact BotFather tokens. Named secret keys in the journal become `[REDACTED]`. |
| File mode | `journal.jsonl`, `journal.jsonl.1`, `journal.tg_offset`, `journal.lock`, `journal.heartbeat`, and `HALT` are chmod 0600. The bot sets umask 077. |
| Sizer | `RiskManager.evaluate` is the only sizer. It is not optional. |

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
| `/auto on\|off` | Optional EMA regime. Fill alerts do not wait for this. |
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
`run` takes an exclusive flock on `journal.lock` (same stem as `journal_path`).
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
`start` restores `live_accepted` if the last of `live_on` / `live_off` is `live_on`.

## Doctor

`doctor` pings Telegram when the token is set.
It always runs an in-process paper `/buy` `/confirm` `/close`.
No live terminal is required.
`--connect` is the optional venue login check.
MT5: binding, login, `trade_mode`. Non-zero if the binding is missing or login fails.
MT4: mailbox ping plus `account` (`account.mode=mt4`, Expert attached, `mt4.files_dir` set).
A traceback is not a pass.
Production live is `doctor --connect` then `run --mode mt5` or `run --mode mt4`.
`trade_mode=2` still needs `--i-accept-risk` at start, or `/live on I-ACCEPT-RISK` in the locked chat.

NOTE
Put `--config` before the subcommand.

## Gate

`pytest` with `--cov-fail-under=80`.
CI jobs are named `ci` and `coverage`.
`tests/` must stay green.
