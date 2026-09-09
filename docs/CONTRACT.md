# Contract

Code that disagrees with this file is wrong.

## Allowed claims

- Every new order is sized so a full stop-out loses at most `risk_pct` of
  equity (default 0.5%). If the broker minimum lot would exceed that, the
  trade is skipped.
- Daily loss of `daily_loss_pct` (default 2%) of start-of-UTC-day equity
  flattens this magic and halts until the next UTC day.
- Drawdown of `max_drawdown_pct` (default 10%) from peak equity flattens
  and stays halted until an operator inspects and restarts.
- `HALT` or Telegram `/halt` flattens immediately.
- Real-money accounts (`trade_mode = 2`) refuse orders unless
  `--i-accept-risk` was passed.
- Secrets live in the environment: `MT5_LOGIN`, `MT5_PASSWORD`,
  `MT5_SERVER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Forbidden claims

- Consistent positive returns.
- Paper P/L equals live P/L. Paper fills at bid/ask. Same-bar SL and TP:
  SL wins.

## Modes

| Mode | Orders | Data |
| --- | --- | --- |
| `paper` | in-process PaperBroker | synthetic, CSV, or `--feed-mt5` |
| `mt5` | terminal `order_send` | live terminal |

## Telegram

Disabled unless both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set.
Commands accepted only from that chat id.

| Command | Effect |
| --- | --- |
| `/status` | equity, halt, peak |
| `/positions` | this magic |
| `/halt` | write HALT, flatten |
| `/resume` | unlink HALT; cannot clear daily_loss or max_drawdown |

Alerts: start, stop, open, close, halt, order_check_fail.

## Gate

`pytest` with `--cov-fail-under=80`. CI jobs are named `ci` and `coverage`
to match the org ruleset on `main`.

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m mt5_risk_bot doctor
python -m mt5_risk_bot backtest --market trend --no-session-filter
```
