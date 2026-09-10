# MT4 mailbox ICD

MetaTrader 4 has no official Python package.
The bot does not load a DLL into the terminal.
The owned interface is a file mailbox in Common Files.

The Expert is `mt4/Experts/Mt4RiskBot.mq4`.
Python is `mt5_risk_bot.broker.mt4_live`.
Engine still sends `MarketOrder` and `WorkingOrder` only.

## Files

Directory: `mt4.files_dir` or `MT4_FILES_DIR`.
That path is Terminal Common Files, not the data folder for one install.

Windows default if the key is empty:

```
%APPDATA%\MetaQuotes\Terminal\Common\Files
```

`%APPDATA%` in a configured path expands. The bot and the terminal must run
on the same Windows host. `journal.lock` uses `msvcrt.locking` there.

| File | Writer | Reader |
| --- | --- | --- |
| `mt4_risk_bot.req` | Python | Expert |
| `mt4_risk_bot.res` | Expert | Python |

Python writes `.req.tmp` and replaces it onto `.req`.
The Expert deletes `.req` after a full read.
The Expert writes `.res` in one open/write/flush/close.
Python accepts `.res` only when `id` matches the request.

One Expert on one chart is enough.
The mailbox is global under Common Files.
Do not run two bots against one mailbox.

## Line format

ASCII. LF. One `key=value` per line.
No nested JSON.
Lists are `n=` plus `row0=`, `row1=`, ...
Row fields are `|` separated.
Comments are clipped to 31 characters (MT4 `OrderSend` limit).
`|` and newlines in values become spaces or `/`.

Request:

```
id=1
op=market
symbol=EURUSD
side=buy
volume=0.1
sl=1.09000
tp=1.12000
comment=mt5-risk-bot
magic=20260909
deviation=20
```

Success reply:

```
id=1
ok=1
ticket=123456
volume=0.10
price=1.10020
```

Failure reply:

```
id=1
ok=0
retcode=130
error=invalid_stops
```

`ok` is `1` or `0`.
`retcode` on failure is the MT4 `GetLastError` when known.
Python maps those onto `TRADE_RETCODE_*` so `OrderResult.ok` stays venue-neutral.

## Ops

| op | Meaning |
| --- | --- |
| `ping` | Expert is attached and the mailbox is live. |
| `account` | Balance, equity, `trade_mode` (`0` demo / `2` real), `trade_allowed`. |
| `symbol` | Spec for one name. |
| `tick` | Bid and ask. |
| `rates` | Bars. `timeframe` is `H1`, not an MT5 integer. Oldest first. |
| `select` | `SymbolSelect`. |
| `positions` / `orders` | Open positions or working orders. `magic=0` means all. |
| `check_market` / `market` | Immediate buy or sell. Check does not send. |
| `check_working` / `working` | Limit or stop. Check does not send. |
| `modify_position` | SL/TP on an open position. |
| `modify_working` | Price or SL/TP on a working order. |
| `cancel` | `OrderDelete`. |
| `close` | `OrderClose` (partial volume allowed). |
| `close_by` | `OrderCloseBy`. Hedge accounts only. |

MT4 has no `OrderCheck`. `check_*` is the Expert validating volume and stops.

Market send uses `OrderSend` with SL/TP at 0, then `OrderModify`.
If modify fails, the Expert closes the ticket and returns failure.
That is for ECN brokers that reject stops on the first send.

## Timeframes

The wire uses names: `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`, `W1`, `MN1`.
The Expert maps those onto `PERIOD_*`.
Do not send MT5 timeframe integers (`16385` is not `PERIOD_H1`).

## Attach

1. Compile `Mt4RiskBot.mq4` in MetaEditor.
2. Attach it to one chart.
3. Enable AutoTrading. Allow live trading on the Expert.
4. Set `account.mode = "mt4"` and `mt4.files_dir`.
5. `doctor --connect` must print `venue=mt4` and exit 0.

Real money (`trade_mode=2`) still needs `--i-accept-risk` or `/live on I-ACCEPT-RISK`.
Paper P/L is not live P/L.
Nothing here guarantees profit.
