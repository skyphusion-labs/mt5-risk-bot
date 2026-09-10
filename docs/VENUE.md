# Venue API

The bot is the Python process on this computer.
A venue is an execution provider (paper, MetaTrader 5, later others).

Engine, desk, and risk never send MetaTrader request dicts.
They send `MarketOrder` and `WorkingOrder`.
Each venue adapter maps those types to its own API.

Paper and MT5 implement `mt5_risk_bot.broker.base.Broker`.
A new venue is a new adapter plus a factory branch.
Do not teach the engine MT5 constants.

## Methods

| Method | Meaning |
| --- | --- |
| `market` / `check_market` | Immediate buy or sell |
| `working` / `check_working` | Limit or stop |
| `modify_position` | SL/TP on an open position |
| `modify_working` | Price or SL/TP on a working order |
| `cancel` | Cancel a working order |
| `close_position` | Close volume on a ticket |
| `close_by` | Hedge offset. A venue may refuse. |
| `rates(symbol, timeframe, count)` | Bars. `timeframe` is `"H1"`, not an MT5 int. |

MT5 ints and `order_send` dicts stay inside `broker/mt5_live.py` and
`broker/paper.py` as private translation.

## Config

`account.mode` is `paper` or `mt5` today.
That is a venue name, not a protocol.
A third venue adds a name and an adapter. It does not change Telegram or risk.
