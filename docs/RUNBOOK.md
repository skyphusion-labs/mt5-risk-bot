# Runbook

## Paper first

```bash
python -m mt5_risk_bot backtest --market trend --no-session-filter
python -m mt5_risk_bot backtest --market range --no-session-filter
```

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

## Demo

1. Broker demo account. Enable AutoTrading.
2. `export MT5_LOGIN MT5_PASSWORD MT5_SERVER` (never commit these).
3. `account.mode = "mt5"` in `config.toml`.
4. `python -m mt5_risk_bot doctor --connect --config config.toml`
5. `python -m mt5_risk_bot run --mode mt5 --loop --config config.toml`
6. Confirm `trade_mode=0` in the doctor output.

Leave it running through at least one full session window. Read `journal.jsonl`.

## Real money

Same as demo, plus `--i-accept-risk`. The bot refuses `trade_mode=2` without
that flag. Start with `risk_pct = 0.002` (0.2%) for the first weeks.

## Halt

```bash
touch HALT
```

Next loop iteration flattens this magic number and stops. Remove the file
and restart when you intend to resume. Daily-loss halt self-clears at the
next UTC midnight. Drawdown halt does not; inspect and restart.

## macOS

Homebrew has Python, not MetaTrader. Install the terminal from
metatrader5.com, log in once by hand, then:

```bash
pip install mt5-mac
python -m mt5_risk_bot doctor --connect --config config.toml
```

If `initialize` fails, launch MetaTrader 5.app yourself and wait until it
is fully up.

## Telegram desk

`run` will not start without `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

1. BotFather, copy the token.
2. Message the bot, set `TELEGRAM_CHAT_ID`.
3. `export XAI_API_KEY=...` (Grok) and/or `ANTHROPIC_API_KEY=...` (Claude).
4. `python -m mt5_risk_bot telegram --message ping`
5. `python -m mt5_risk_bot run --mode mt5 --loop --config config.toml`

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
/symbols
/symbols add NZDUSD
/symbols remove NZDUSD
```

`stop=` is the same shape (`/sell EURUSD stop=... sl=... tp=...`). Do not
set both `limit=` and `stop=`. Bare `/cancel` drops a staged confirm;
`/cancel TICKET` cancels a working order.

`/halt` writes `HALT`, drops the confirm, cancels working orders, and
flattens positions. `/resume` only clears that file. Daily-loss and
max-drawdown cannot be cleared from Telegram.

## Journal

JSONL, one event per line: `start`, `open`, `close`, `modify`, `reject`,
`halt`, `order_check_fail`, `pending`, `stop`. Grep `reject` if it never
trades; `outside_session` and `no_regime` are the usual reasons.
