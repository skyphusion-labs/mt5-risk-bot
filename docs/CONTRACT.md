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
- `HALT` or `/halt` flattens immediately.
- Real-money accounts (`trade_mode = 2`) refuse orders unless
  `--i-accept-risk` was passed.
- Secrets live in the environment: `MT5_LOGIN`, `MT5_PASSWORD`,
  `MT5_SERVER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `XAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `AI_PROVIDER`.

## Forbidden claims

- Consistent positive returns.
- That Grok or Claude is a signal you should follow blindly.
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
| `/quote SYMBOL` | bid/ask |
| `/buy` `/sell` SYMBOL `[sl=] [tp=]` | stage a market order |
| `/confirm` `/cancel` | send or drop the staged order |
| `/close TICKET\|SYMBOL\|all` | flatten |
| `/sl` `/tp` TICKET PRICE | modify |
| `/ask ...` or free text | Grok or Claude, may stage a trade |
| `/model grok\|claude` | switch provider |
| `/auto on\|off` | optional EMA regime |
| `/status` `/positions` `/halt` `/resume` | account |

Manual `/buy` `/sell` skip the session window. Auto does not.

## Gate

`pytest` with `--cov-fail-under=80`. CI jobs are named `ci` and `coverage`.
