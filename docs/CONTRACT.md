# Contract

Code that disagrees with this file is wrong.

This bot is a **Telegram desk**. The user trades and asks Grok or Claude
from one chat. MetaTrader 5 is the execution venue. The risk engine is
the only thing that may size or refuse an order. Auto EMA trading is
off until `/auto on`.

## Allowed claims

- Telegram is required for `run`. Slash commands place, close, and
  modify trades. Free text goes to the configured model.
- AI advice never sends an order. A staged suggestion waits for
  `/confirm` (default 120s).
- Every new order is sized so a full stop-out loses at most `risk_pct`
  of equity (default 0.5%). If the broker minimum lot would exceed that,
  the trade is skipped.
- Daily loss of `daily_loss_pct` (default 2%) of start-of-UTC-day equity
  flattens this magic and halts until the next UTC day.
- Drawdown of `max_drawdown_pct` (default 10%) from peak equity flattens
  and stays halted until an operator inspects and restarts.
- `HALT` or `/halt` flattens immediately (positions and working orders).
- Real-money accounts (`trade_mode = 2`) refuse orders unless
  `--i-accept-risk` was passed. Paper is the default mode.
- SL/TP hits and pending-order fills emit Telegram alerts even when
  `/auto` is off. Default notify events include `open`, `close`,
  `pending`, and `recap`.
- A UTC day roll sends a recap notify (`journal.tail`, equity vs
  `day_start`). That is not a trade. `/recap` dumps the same snapshot.
- Secrets live in the environment: `MT5_LOGIN`, `MT5_PASSWORD`,
  `MT5_SERVER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `XAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `AI_PROVIDER`. Journal writes, `loop_error`
  stderr, and Telegram `send` redact BotFather tokens. Named secret
  keys in the journal become `[REDACTED]`. `journal.jsonl`,
  `journal.tg_offset`, and `HALT` are chmod 0600. Process umask 077.

## Forbidden claims

- Consistent positive returns.
- That Grok or Claude is a signal you should follow blindly.
- Paper P/L equals live P/L. Paper fills at bid/ask. Same-bar SL and TP:
  SL wins. Paper pending limit/stop fills on tick (bid/ask vs price) or
  bar (high/low vs price).

## Modes

| Mode | Orders | Data |
| --- | --- | --- |
| `paper` | in-process PaperBroker | synthetic, `--feed-mt5`, or empty |
| `mt5` | terminal `order_send` | live terminal |

## Telegram commands

| Command | Effect |
| --- | --- |
| `/quote [SYMBOL]` | one symbol, or all configured symbols if omitted |
| `/risk` | daily-loss and drawdown room vs caps |
| `/buy` `/sell` SYMBOL `[sl=] [tp=] [limit=PRICE] [stop=PRICE]` | stage market, or a working limit/stop (not both) |
| `/confirm` | market: reprice to the live tick, preview, send. limit/stop: preview at the staged price, send |
| `/cancel` | drop the staged confirm |
| `/cancel TICKET` | cancel a working order |
| `/replace TICKET PRICE` | move a working order's entry; `TRADE_ACTION_MODIFY`. Circuit and risk_pct still refuse |
| `/orders` | list working orders |
| `/close TICKET\|SYMBOL\|all [VOL]` | flatten or partial close |
| `/closeby TICKET OTHER` | hedge-account only. `TRADE_ACTION_CLOSE_BY` offsets two opposite tickets. Same symbol, opposite sides. Remainder 0 or at least `volume_min`. Not a new send. Netting terminals refuse CLOSE_BY. Paper always hedges |
| `/reverse TICKET [sl=] [tp=]` | two market sends: close the ticket, then the opposite side. `/confirm` is the send. Stage/preview exclude that ticket. Mirrors SL/TP distances if omitted. Circuit and risk_pct still refuse; after flatten they can leave you flat |
| `/sl` TICKET PRICE | modify a position or a working order; success only if the broker applied it |
| `/tp` TICKET PRICE `[VOL]` | full TP, or scale-out VOL at PRICE (partial close when hit). Circuit still refuses |
| `/be TICKET` | move SL to entry; never loosen |
| `/trail on\|off\|TICKET` | on: `manage()` existing positions every tick, no EMA entries (default off). TICKET: one-shot. never loosen |
| `/history` | last journal events |
| `/recap` | equity vs UTC `day_start` plus `journal.tail`; also sent on UTC day roll as notify `recap` |
| `/symbols list\|add\|remove [SYMBOL]` | configured book (runtime). Bare `/symbols` lists. Cannot drop the last name, or a name with positions/orders |
| `/ask ...` or free text | Grok or Claude (last 6 turns plus status, /risk, positions, working orders, quotes); JSON may stage market, `limit=`, `stop=`, or close TICKET; never sends |
| `/model grok\|claude` | switch provider |
| `/auto on\|off` | optional EMA regime; fill alerts do not wait for this |
| `/status` `/positions` `/halt` `/resume` | account; `/halt` flattens, drops the confirm, and cancels working orders |

`/confirm` for a market order reprices and re-runs `preview`. A limit or stop keeps the staged price. Halt, daily-loss, and drawdown still refuse. The reply includes `ok` and `retcode`; only `OrderResult.ok` starts with `sent `. A second `/buy` while a confirm is live is refused until `/cancel`. Advice never overwrites a live confirm. Close and SL/TP success replies come from `OrderResult.ok`, not from "the ticket existed". `/sl` `/tp` on a working order uses `TRADE_ACTION_MODIFY` (paper supported) and keeps side geometry (`buy: sl < price < tp`). `/tp TICKET PRICE VOL` is a scale-out: when PRICE is hit, only VOL closes. Remainder keeps its SL. Halt/daily-loss/drawdown still refuse. VOL must snap to lot step; remainder 0 or at least `volume_min`.

Advice JSON fields: `action`, `symbol`, `sl`, `tp`, `limit`, `stop`, `ticket`, `summary`. `limit` and `stop` are XOR. A close action with `ticket` stages that close; `/confirm` is still the only send. Context always includes `/risk`, positions, working orders, and quotes. If the circuit would halt, context says hold/close only and buy/sell is not staged.

Buy limit must be below ask; sell limit above bid; buy stop above ask; sell stop below bid. `limit=` and `stop=` together are refused. `/replace TICKET PRICE` keeps that geometry and existing SL/TP; it does not send a new order.

`/reverse TICKET` stages a close plus the opposite market. `/confirm` is two market sends, not one: first a close of that ticket, then an opposite market deal. Default SL/TP mirror the open trade's distances around the live bid/ask. Risk sizes the new side independently. Staging and the first confirm preview exclude that ticket so `already_in_symbol` does not block; halt, daily-loss, drawdown, and risk_pct still refuse, and the ticket stays open. After the close send succeeds, preview runs again on the live book. Realized P/L can trip the circuit or leave no room for `risk_pct`; the reply is `closed #TICKET; reverse refused: ...` and there is no opposite position. A failed opposite send is `closed #TICKET; send failed ...`. Reverse is for open positions, not working orders.

`/closeby TICKET OTHER` is a flatten, not a new order. Both tickets must be this magic, same symbol, opposite sides. Overlap volume closes; the larger side keeps the remainder. Same ticket, same side, or a leftover below `volume_min` is refused. Live CLOSE_BY is hedge-account only; a netting terminal refuses it (one net position per symbol, no opposite ticket). Paper always hedges: each deal is its own ticket, so close-by works in paper even when a live netting account would not. Paper P/L is not live P/L.

Each loop tick resolves pending fills and SL/TP even when `/auto` is off. Pending fills notify as `FILL/OPEN`; SL/TP hits notify as `CLOSE` with `reason=sl` or `reason=tp`. `/trail on` runs `manage()` on open positions every `step_all` tick and does not enable EMA entries. `/auto on` still owns entries. Trail default is off.

Manual `/buy` `/sell` skip the session window. Auto does not.

`run --loop` retries Telegram HTTP 429 and 5xx with backoff and resumes
`getUpdates` at the same offset. That offset is written next to the
journal (`journal.tg_offset`) after each update is handled or skipped,
so a restart does not replay or drop commands. A dropped MT5 IPC calls
`initialize` again. One failed poll, send, or broker tick is journaled
(`reconnect` or `loop_error`); the process stays up.

A staged `/confirm` is journaled (`confirm_stage`). `start` restores it
if the last of `confirm_stage` / `confirm_cancel` / `confirm_sent` is
still `confirm_stage` and the TTL has not expired.

`doctor` pings Telegram when the token is set, and always runs an
in-process paper `/buy` `/confirm` `/close`. No live terminal required.
`--connect` is the optional MT5 login check (binding, login, `trade_mode`).
Production live is `doctor --connect` then `run --mode mt5`. `trade_mode=2`
still needs `--i-accept-risk`.

## Gate

`pytest` with `--cov-fail-under=80`. CI jobs are named `ci` and `coverage`.
