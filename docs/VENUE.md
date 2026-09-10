# Venue API

The bot is the Python process on this computer.
A venue is an execution provider.
Paper and MetaTrader 5 are the venues today.

Engine, desk, and risk never send MetaTrader request dicts.
They send `MarketOrder` and `WorkingOrder`.
Each venue adapter maps those types to its own API.

Paper and MT5 implement `mt5_risk_bot.broker.base.Broker`.
`account.mode` selects the adapter (`paper` or `mt5`) in `run`.
A new venue is a new adapter plus a factory branch.
Do not teach the engine MT5 constants.

## Types

`MarketOrder` is an immediate buy or sell.
Fields: `symbol`, `side`, `volume`, `sl`, `tp`, `comment`, `magic`, `deviation`, `ticket`.

`WorkingOrder` is a limit or a stop.
Fields: `symbol`, `side`, `kind`, `volume`, `price`, `sl`, `tp`, `comment`, `magic`, `ticket`.
`kind` is `"limit"` or `"stop"`.

`OrderResult.ok` is true when `retcode` is in `RETCODE_OK`.
Only `OrderResult.ok` is a send.

## Methods

| Method | Meaning |
| --- | --- |
| `connect` / `disconnect` | Open or close the venue. |
| `account` | Balance, equity, `trade_mode`. |
| `symbol` | Spec for one name. |
| `tick` | Bid and ask. |
| `rates(symbol, timeframe, count)` | Bars. `timeframe` is `"H1"`, not an MT5 integer. |
| `positions` / `orders` | Open positions or working orders. Optional magic. |
| `select_symbol` | Add a name to the book. |
| `check_market` / `market` | Immediate buy or sell. |
| `check_working` / `working` | Limit or stop. |
| `modify_position` | SL/TP on an open position. |
| `modify_working` | Price or SL/TP on a working order. |
| `cancel` | Cancel a working order. |
| `close_position` | Close volume on a ticket. |
| `close_by` | Hedge offset. A venue may refuse. |

MT5 integers and `order_send` dicts stay inside `broker/mt5_live.py` and
`broker/paper.py` as private translation.
The MT5 adapter maps timeframe names with `timeframe_code`.

## Config

`account.mode` is `paper` or `mt5` today.
That is a venue name, not a protocol.
A third venue adds a name and an adapter.
It does not change Telegram or risk.

## Fills

The venue holds live positions and working orders.
`journal.jsonl` is the source of truth for fills the bot observed.
A pending fill writes `open` with `fill=true`.
A vanished ticket writes `close` with `fill=true`.
Do not treat the terminal deal history as the bot's fill log.
