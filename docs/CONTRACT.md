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
  `/auto` is off. Default notify events include `open`, `close`, and
  `pending`.
- Secrets live in the environment: `MT5_LOGIN`, `MT5_PASSWORD`,
  `MT5_SERVER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `XAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `AI_PROVIDER`.

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
| `/orders` | list working orders |
| `/close TICKET\|SYMBOL\|all [VOL]` | flatten or partial close |
| `/sl` `/tp` TICKET PRICE | modify; success only if the broker applied it |
| `/be TICKET` | move SL to entry; never loosen |
| `/trail TICKET` | ATR trail / breakeven from the strategy; never loosen |
| `/history` | last journal events |
| `/ask ...` or free text | Grok or Claude (last 6 turns plus status, /risk, positions, working orders, quotes); JSON may stage market, `limit=`, `stop=`, or close TICKET; never sends |
| `/model grok\|claude` | switch provider |
| `/auto on\|off` | optional EMA regime; fill alerts do not wait for this |
| `/status` `/positions` `/halt` `/resume` | account; `/halt` flattens, drops the confirm, and cancels working orders |

`/confirm` for a market order reprices and re-runs `preview`. A limit or stop keeps the staged price. Halt, daily-loss, and drawdown still refuse. The reply includes `ok` and `retcode`; only `OrderResult.ok` starts with `sent `. A second `/buy` while a confirm is live is refused until `/cancel`. Advice never overwrites a live confirm. Close and SL/TP success replies come from `OrderResult.ok`, not from "the ticket existed".

Advice JSON fields: `action`, `symbol`, `sl`, `tp`, `limit`, `stop`, `ticket`, `summary`. `limit` and `stop` are XOR. A close action with `ticket` stages that close; `/confirm` is still the only send. Context always includes `/risk`, positions, working orders, and quotes.

Buy limit must be below ask; sell limit above bid; buy stop above ask; sell stop below bid. `limit=` and `stop=` together are refused.

Each loop tick resolves pending fills and SL/TP even when `/auto` is off. Pending fills notify as `FILL/OPEN`; SL/TP hits notify as `CLOSE` with `reason=sl` or `reason=tp`.

Manual `/buy` `/sell` skip the session window. Auto does not.

## Gate

`pytest` with `--cov-fail-under=80`. CI jobs are named `ci` and `coverage`.
