# Venue API

The bot is the Python process on this computer.
A venue is an execution provider.
Paper, MetaTrader 5, and MetaTrader 4 are the venues today.

Engine, desk, and risk never send MetaTrader request dicts.
They send `MarketOrder` and `WorkingOrder`.
Each venue adapter maps those types to its own API.

Paper, MT5, and MT4 implement `straightedge.broker.base.Broker`.
`account.mode` selects the adapter (`paper`, `mt5`, or `mt4`) in `run`.
`broker_for(cfg)` in `straightedge.broker` returns PaperBroker, Mt5Broker, or Mt4Broker.
A new venue is a new adapter plus a factory branch.
Do not teach the engine MT5 constants.

MT4 has no official Python package.
`Mt4Broker` speaks a line mailbox to `mt4/Experts/Mt4RiskBot.mq4`.
See `docs/MT4.md`.

## Types

`MarketOrder` is an immediate buy or sell.
Fields: `symbol`, `side`, `volume`, `sl`, `tp`, `comment`, `magic`, `deviation`, `ticket`.

`WorkingOrder` is a limit or a stop.
Fields: `symbol`, `side`, `kind`, `volume`, `price`, `sl`, `tp`, `comment`, `magic`, `ticket`.
`kind` is `"limit"` or `"stop"`.

`PendingOrder` is a working order the venue holds.
`kind` is `"limit"` or `"stop"`. Engine never reads MT5 type integers.

`Side` is `buy` or `sell`. MT5 order type integers live in the adapters.

`OrderResult.ok` is true when `retcode` is in `RETCODE_OK`.
Only `OrderResult.ok` is a send.
Engine uses `OrderResult.unchanged` and `OrderResult.invalid_stops`.
It does not import MT5 retcode integers.

`RETCODE_OK` includes `DONE_PARTIAL`, so `ok` means the send was accepted, not
that it filled in full. A caller that needs the whole volume gone compares
`OrderResult.volume` against the volume it asked for. `Engine.flatten` does
exactly that, and `FlattenReport` carries the result. Read `pos.volume` before
the close, never after: `Position` is mutable and `PaperBroker` returns live
references, so a partial close rewrites it in place.

`OrderResult.measured` is false when the venue call produced no result at all.
An adapter that gets nothing back MUST return `OrderResult.unknown(...)`, which
carries `RETCODE_UNKNOWN` (`-1`). It must never synthesize a venue code, and in
particular never `0`: MQL5 `order_check` reports a PASSED check as retcode `0`,
so a synthesized `0` made an unrun check indistinguishable from a passed one and
the engine sent the order (issue #8).

COULD NOT MEASURE is not a verdict. When `measured` is false, neither `ok` nor
`retcode` means anything, so a caller gating on a pre-trade check aborts. The
engine journals that as `order_check_fail` with `reason="not_measured"`, kept
distinct from `reason="broker_refused"`, so an operator can tell "the broker
said no" from "we never asked".

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
MT4 integers stay inside `broker/mt4_live.py` and the Expert.

## Config

`account.mode` is `paper`, `mt5`, or `mt4`.
That is a venue name, not a protocol.
A fourth venue adds a name and an adapter.
It does not change Telegram or risk.

## Fills

The venue holds live positions and working orders.
`journal.jsonl` is the source of truth for fills the bot observed.
A pending fill writes `open` with `fill=true`.
A vanished ticket writes `close` with `fill=true`.
Do not treat the terminal deal history as the bot's fill log.
