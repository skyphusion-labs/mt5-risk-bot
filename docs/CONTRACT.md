# Contract

Code that disagrees with this file is wrong.

This bot is a Telegram desk. You trade and ask from one chat.
MetaTrader 5 is the execution venue. The risk engine is the only
thing that may size or refuse an order. Auto EMA trading is off
until `/auto on`.

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
  `/auto` is off.
- A UTC day roll sends a recap notify. That is not a trade. `/recap`
  dumps the same snapshot.
- Secrets live in the environment. `AI_PROVIDER=computer` posts to the
  Computer worker. That worker bills through AI Gateway. Journal writes,
  `loop_error` stderr, and Telegram `send` redact BotFather tokens.
  Journal files and `HALT` are chmod 0600. Process umask 077.

## Forbidden claims

- Consistent positive returns.
- That Grok or Claude is a signal you must follow.
- Paper P/L equals live P/L. Paper fills at bid/ask. Same-bar SL and TP:
  SL wins.

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
| `/confirm` | send the staged order through risk |
| `/cancel` | drop the staged confirm |
| `/cancel TICKET` | cancel a working order |
| `/replace TICKET PRICE` | move a working order entry |
| `/orders` | list working orders |
| `/close TICKET\|SYMBOL\|all [VOL]` | flatten or partial close |
| `/closeby TICKET OTHER` | hedge-account only; netting terminals refuse |
| `/reverse TICKET [sl=] [tp=]` | close then opposite market; `/confirm` is the send |
| `/sl` TICKET PRICE | modify a position or a working order |
| `/tp` TICKET PRICE `[VOL]` | full TP, or scale-out VOL at PRICE |
| `/be TICKET` | move SL to entry; never loosen |
| `/trail on\|off\|TICKET` | trail existing positions; no EMA entries |
| `/history` | last journal events |
| `/recap` | equity vs UTC day start |
| `/symbols list\|add\|remove [SYMBOL]` | configured book |
| `/ask ...` or free text | advise; may stage; never send |
| `/model grok\|claude\|computer` | switch provider |
| `/auto on\|off` | EMA regime |
| `/status` `/positions` `/halt` `/resume` | account |

`/confirm` for a market order reprices and re-runs `preview`. A limit or
stop keeps the staged price. Halt, daily-loss, and drawdown still refuse.
Only `OrderResult.ok` starts with `sent `. A second `/buy` while a confirm
is live is refused until `/cancel`. Advice never overwrites a live confirm.

Advice JSON fields: `action`, `symbol`, `sl`, `tp`, `limit`, `stop`,
`ticket`, `summary`. `limit` and `stop` are XOR. Local memory is 40 turns
in `journal.advice.json`. Computer memory is the Durable Object workspace.
Session is the Telegram chat id.

`/closeby` is hedge-account only. `/reverse` is two market sends. After
the close, the circuit can refuse the new side. Then you are flat.

## Risk engine

`RiskManager.evaluate` is the only sizer. It is not optional.

## Tests

`tests/` must stay green. `pytest` addopts include `--cov-fail-under=80`.
