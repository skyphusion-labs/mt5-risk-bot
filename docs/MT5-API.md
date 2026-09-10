# MetaTrader 5 API (what this bot uses)

Pulled from the official MQL5 Python Integration reference
(https://www.mql5.com/en/docs/integration/python_metatrader5)
and the trade-server return-code table.
The bot mirrors these constants in `mt5_risk_bot/constants.py`.
The paper broker and the live adapter share one vocabulary.

The bot is the Python process on this computer.

## Binding

`pip install MetaTrader5` (PyPI `metatrader5`, current train 5.0.x).
The package is an IPC client against a running Windows terminal.
It is not a REST API.
It is not available as a native macOS wheel.

macOS options:

1. Install `mt5-mac` (PyPI 0.3.0).
   It talks JSON to Python-for-Windows inside the Wine runtime bundled in MetaTrader 5.app.
   Same function names.
2. Use a Windows VPS running the official package.
   That VPS may expose REST (`mt5api`, `mt5-bridge`).
   This bot does not speak those HTTP APIs.
3. Use paper or backtest.
   No terminal is required.

`initialize()` locates or launches `terminal64.exe`.
Optional named args: `login`, `password`, `server`, `timeout` (default 60000 ms), `portable`.
`shutdown()` drops the connection.
`login(login, password, server)` switches account after initialize.

AlgoTrading must be enabled in the terminal.
Otherwise you get `TRADE_RETCODE_CLIENT_DISABLES_AT` = 10027.
`terminal_info().trade_allowed` is the check.

## Function map

Python functions that wrap MQL5:

| Python | MQL5 |
| --- | --- |
| `account_info` | AccountInfoInteger / Double / String |
| `terminal_info` | TerminalInfo* |
| `symbols_get` / `symbol_info` / `symbol_info_tick` | SymbolInfo* |
| `symbol_select` | SymbolSelect |
| `copy_rates_from` / `_from_pos` / `_range` | CopyRates |
| `copy_ticks_from` / `_range` | CopyTicks |
| `order_calc_margin` / `order_calc_profit` | OrderCalcMargin / Profit |
| `order_check` | OrderCheck |
| `order_send` | OrderSend |
| `orders_get` / `positions_get` | OrdersTotal+Get / PositionsTotal+Get |
| `history_orders_get` / `history_deals_get` | History* |
| `market_book_add` / `_get` / `_release` | MarketBook* |

Python-only: `initialize`, `login`, `shutdown`, `version`, `last_error`.

## account_info fields used here

`login`, `trade_mode` (0 demo, 1 contest, 2 real), `leverage`, `trade_allowed`,
`trade_expert`, `fifo_close`, `balance`, `equity`, `margin`, `margin_free`,
`profit`, `margin_level`, `server`, `currency`, `company`.

## Rates / ticks

`copy_rates_from_pos(symbol, timeframe, start_pos, count)` returns bars
`(time, open, high, low, close, tick_volume, spread, real_volume)`.
Timeframes: M1=1, M5=5, M15=15, M30=30, H1=16385, H4=16388, D1=16408.

`symbol_info_tick` returns `bid`, `ask`, `last`, `time`, `volume`.

## Trade request (`order_send` / `order_check`)

Dict mapped onto `MqlTradeRequest`:

| Field | Role |
| --- | --- |
| `action` | TRADE_ACTION_DEAL=1, PENDING=5, SLTP=6, MODIFY=7, REMOVE=8, CLOSE_BY=10 |
| `magic` | EA id. This bot uses 20260909. Positions are filtered by it. |
| `symbol` | Instrument |
| `volume` | Lots. Must snap to `volume_min` / `volume_step` / `volume_max`. |
| `type` | ORDER_TYPE_BUY=0, SELL=1, plus pending types 2-7 |
| `price` | Required for instant/request execution. Optional for market execution. |
| `sl` / `tp` | Absolute prices. This bot requires SL. |
| `deviation` | Max slippage in points |
| `type_filling` | FOK=0, IOC=1, RETURN=2. Must match `SYMBOL_FILLING_MODE` bits |
| `type_time` | GTC=0, DAY=1, SPECIFIED=2 |
| `comment` | Keep short. Terminals truncate around 31 chars. |
| `position` | Ticket when closing or changing SL/TP |
| `position_by` | Opposite ticket for `TRADE_ACTION_CLOSE_BY` |

`order_check` validates funds and request shape.
Its success retcode is **0**, not 10009.
`order_send` success is 10009 (`TRADE_RETCODE_DONE`) or 10010 (partial) or 10008 (placed, pending).

Close a position: `TRADE_ACTION_DEAL` with the opposite `type` and `position=<ticket>`.
Netting vs hedging: on netting, an opposite deal reduces the single position.
On hedging you must pass the ticket.

Change position SL/TP: `TRADE_ACTION_SLTP` with `position`, `sl`, `tp`.
Change a pending order: `TRADE_ACTION_MODIFY` with `order`, `price`, `sl`, `tp`.
Close two opposite hedges: `TRADE_ACTION_CLOSE_BY` with `position` and `position_by`.
Hedge accounts only.
A netting terminal refuses CLOSE_BY (one net position per symbol; there is no opposite ticket).
The paper broker always hedges (a new ticket per deal) so close-by works in paper.
That is not a claim that paper P/L equals live.

## Filling mode (10030)

`SYMBOL_FILLING_MODE` is a bitfield: FOK=1, IOC=2.
If neither bit is set, RETURN is the market/exchange default.
Hardcoding RETURN is the usual cause of `TRADE_RETCODE_INVALID_FILL` (10030).
This bot picks FOK if allowed, else IOC, else RETURN.
It retries the other two on 10030.

FOK = all-or-nothing (size stays equal to the risk calc).
IOC = fill what you can (size can shrink; the bot still sends the computed lot).

## Stops (10016)

`SYMBOL_TRADE_STOPS_LEVEL` is the minimum SL/TP distance in points from the close price.
`SYMBOL_TRADE_FREEZE_LEVEL` blocks modify when price is that close to SL/TP.
Both are enforced before send.

## Lot math

```
ticks = abs(entry - sl) / trade_tick_size
money_per_lot = ticks * trade_tick_value
lots = floor((equity * risk_pct / money_per_lot) / volume_step) * volume_step
```

If the broker minimum lot would risk more than `risk_pct`, the trade is skipped.
Never round up.

For USD-quoted FX, `trade_tick_value` is about `contract_size * tick_size` in account currency.
For USDJPY it scales with price (`contract_size * tick_size / price` when the profit currency is not USD).
The live adapter reads `trade_tick_value` from the terminal so it stays correct.
The paper broker uses a static approximation.

## Return codes the bot cares about

| Code | Constant | Meaning |
| --- | --- | --- |
| 10004 | REQUOTE | Price moved. Retry with a fresh tick. |
| 10009 | DONE | Filled |
| 10010 | DONE_PARTIAL | Partial fill |
| 10014 | INVALID_VOLUME | Lot not on the step grid |
| 10016 | INVALID_STOPS | SL/TP inside stops_level |
| 10017 | TRADE_DISABLED | Symbol or account |
| 10018 | MARKET_CLOSED | Session |
| 10019 | NO_MONEY | Margin |
| 10024 | TOO_MANY_REQUESTS | Slow down |
| 10026 / 10027 | SERVER/CLIENT_DISABLES_AT | AutoTrading off |
| 10030 | INVALID_FILL | Wrong type_filling |
| 10031 | CONNECTION | Terminal offline |
| 10040 | LIMIT_POSITIONS | Server cap |
| 10045 | FIFO_CLOSE | US FIFO accounts |

## Market publication checks (still valid for any EA)

From "The checks a trading robot must pass before publication in the Market":

- Never `OrderSend` when margin is insufficient. Check first.
- Never place SL/TP inside `SYMBOL_TRADE_STOPS_LEVEL`.
- Never modify inside `SYMBOL_TRADE_FREEZE_LEVEL`.
- Handle hedging vs netting.
- Log retcodes.

This bot does those on both paper and live paths.

## What the Python package is not

It is not a hosted REST service.
It cannot run inside Cloudflare Workers.
It does not backtest.
MetaTrader's Strategy Tester is MQL5-only.
The paper broker here is a separate, conservative simulator.
Same-bar SL and TP: SL wins.
