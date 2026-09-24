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

### Both ends sanitise, and neither trusts the other

`|`, CR and LF are the only structure this protocol has, so no value may
contain one. `|` becomes `/`; CR and LF each become a space.

BOTH ends apply that rule to everything they write, because both ends handle
text the other did not produce:

- Python, outbound, in `_wire()`: order comments come from config and could
  carry anything.
- The Expert, inbound, in `Wire()`: `OrderComment()`, `OrderSymbol()`,
  `AccountName()`, `AccountServer()` and `AccountCurrency()` are whatever the
  BROKER put there. Brokers do append annotations to comments (`[sl]`,
  `from #123`), so a pipe arriving from the terminal is ordinary, not exotic.

Sanitising on only one side is not a half-measure, it is the whole defect. A
position row is 13 pipe-separated fields with the comment at index 10, so one
extra pipe moves `swap` onto the comment tail and `time` onto `swap`, and
`positions()` raises out of `float()`. The desk then cannot enumerate its own
book at all.

One asymmetry, on purpose: `_wire()` also forces ASCII (non-ASCII becomes `?`),
and `Wire()` does not, because MQL4 has no cheap equivalent. Non-ASCII broker
text therefore reaches the adapter as the terminal's code page renders it. It
cannot break framing, which is what the rule protects.

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
`retcode` on failure is the MT4 `GetLastError` for the call that failed, or a
code the Expert chose for a check it performed itself (`130` for its own stops
check, `131` for volume, `133` for trade-not-allowed, `4108` for a ticket it
could not find).
Python maps those onto `TRADE_RETCODE_*` so `OrderResult.ok` stays venue-neutral.

`retcode=0` on a failure means the Expert did not report a reason. It is NOT a
broker rejection, and the adapter does not present it as one: it reports
`RETCODE_UNKNOWN`, `OrderResult.measured` is false, and the comment says the
reason was not reported. An Expert older than 1.3.1 produces this on every
failed send and modify, because `GetLastError()` clears the error register when
it is read and the retry helpers read it first. Recompile and reattach
`Mt4RiskBot.mq4` if you see it.

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

## Two-step entry, and what happens when step two fails

A market send is two calls, not one. `OrderSend` opens the position with SL and
TP at 0, then `OrderModify` attaches the stop. That is for ECN brokers that
reject stops on the first send. Between the two calls the position is open and
has no stop. The window is one broker round trip plus up to 5 modify retries at
50 ms each.

MT5 does not have this window. It attaches the stop in the same call as the
entry, so nothing in this section applies to the MT5 adapter.

If the modify fails, the Expert rolls the position back. The rollback is
verified against the book, not against the return value of `OrderClose`: the
Expert re-reads the ticket and only reports success when `OrderCloseTime()`
shows it is gone. Failing to SELECT the ticket is not treated as a reason to
give up on it either; that path rolls back too.

Three outcomes, and the reply says which one happened:

| Outcome | Reply | Meaning |
| --- | --- | --- |
| stop attached | `ok=1` | The position is open and protected. |
| rollback verified | `ok=0`, `survivor_ticket=0` | Nothing is open. A clean failure. |
| rollback NOT verified | `ok=0`, `survivor_ticket=<ticket>`, `error=sl_modify_failed_position_live` | The position is STILL OPEN and has NO STOP. |

The third row is the one that matters. The send failed and something is live.
The adapter puts the ticket on `OrderResult.survivor_ticket`, and the engine
writes a `unmanaged_position` journal event and tells the operator in words.
Deal with it in the terminal: the bot does not size, manage, or stop out a
position it did not record.

`check_working` / `working` behave the same way, with `OrderDelete` instead of
`OrderClose` and `sl_modify_failed_order_live` instead.

### survivor_ticket absent is not survivor_ticket zero

Every failure reply from `market`, `working`, `check_market` and `check_working`
carries `survivor_ticket`. An Expert older than version 1.2.0 does not send the
field at all. The adapter reports that as `survivor_ticket = None`, which is
COULD NOT MEASURE, and the engine writes `survivor_unknown`. It is NOT reported
as zero, because "the Expert did not answer" and "the Expert checked and
nothing survived" are different facts and only one of them is safe.

If you see `survivor_unknown`, recompile and reattach `Mt4RiskBot.mq4`.

### Startup reconciliation

`OnInit` scans the book and prints one line per position that is open with no
stop, then a summary count. Set the `ReconcileMagic` input to limit the scan to
one magic number; `0`, the default, reports every unstopped position.

The Expert REPORTS these and does not adopt them. A position that predates the
current session is not sized, not managed, and not stopped out by this bot. A
human closes or protects it.

## What a symbol spec can and cannot say on MT4

The adapter reads 15 fields. `SymbolReply` can supply 11. The other 4 are not
an oversight in the Expert: MQL4's `MarketInfo` has no identifier for them.

A field that was not measured is recorded in `SymbolSpec.unmeasured` and left at
a value that cannot be mistaken for usable. It is never replaced with a
plausible default, because a plausible default is indistinguishable from a
measurement and nothing downstream can then tell them apart.

| Field | On the wire | Notes |
| --- | --- | --- |
| `digits` | yes | `0` is a real reading for an instrument quoted in whole points. |
| `point` | yes | Zero is not a reading; `MarketInfo` answers 0 for a symbol not in Market Watch. |
| `tick_size` | yes | Same. |
| `tick_value` | yes | Same. See the note below; MT4 has ONE tick value. |
| `contract_size` | yes | Zero is not a reading. |
| `volume_min` / `volume_max` / `volume_step` | yes | 8 decimals, so a 0.001 lot step survives. |
| `stops_level` / `freeze_level` / `spread` | yes | `0` is a real reading and stays one. |
| `trade_mode` | NO | MQL4 has no trade-mode identifier. Recorded unmeasured, left at `0` (disabled), the fail-closed direction. |
| `currency_base` / `currency_profit` | NO | Derived by slicing the symbol name. A naming convention, not a measurement, and recorded as such. |
| `currency_margin` | NO | No MQL4 source at all. |

### MT4 has one tick value, by design

MQL4 exposes exactly one tick-value identifier, `MODE_TICKVALUE`. There is no
loss-leg variant. The MT5 remedy of preferring `trade_tick_value_loss` when the
primary field is unusable **does not transfer**, so do not re-propose it. On MT4
the only honest response to an unusable tick value is to REFUSE.

`1.0` was the old default and it is not conservative. The error is the ratio
`true / 1.0`:

- below 1.0 (a JPY cross, about 0.67): undersized, which is safe;
- above 1.0 (indices, metals, most CFDs; say 2.5): oversized by that ratio, so
  a 100 unit budget becomes a 250 unit loss.

The shipped example config lists `EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, so one
of the four defaults already has a non-unit tick value. This is not an exotic
edge case.

The last-line guard could not catch it: `risk.py` recomputes
`money_per_lot_at_stop` from the same spec, so a wrong number was compared
against a wrong number and passed. A guard that shares its subject's input is
not a guard.

### What refusal looks like

The risk gate refuses with `spec_not_measured:<field>,<field>` before anything
reads the spec, rather than letting sizing return zero and reporting
`size_zero`. Those are different facts: `size_zero` says the budget was too
small, and this says nothing was measured. If you see it, the symbol is
probably not in Market Watch; add it there and restart.

## Timeframes

The wire uses names: `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`, `W1`, `MN1`.
The Expert maps those onto `PERIOD_*`.
Do not send MT5 timeframe integers (`16385` is not `PERIOD_H1`).

## Wire contract

`tests/test_mt4_wire.py` holds a golden transcript for every op, and
`tests/mt4_transcripts.py` derives each one from the Expert's own emitters with
line citations. Those two files are the executable copy of this document. Change
the Expert's reply format and they go red.

Two checks in there are worth knowing about before editing either side:

- The pipe-join order of the position, order and bar rows is asserted against
  the adapter's field tuples. Reorder one field on either side and the suite
  fails by name.
- A field the Expert does not send is recorded as absent, not as zero, so the
  tests can tell a measured value from a defaulted one.

`broker/mt4_live.py` carries a per-file coverage floor declared in
`pyproject.toml` under `[tool.mt5_risk_bot.coverage_floors]`.

## Attach

1. Compile `Mt4RiskBot.mq4` in MetaEditor. The Toolbox Errors tab must read
   `0 error(s), 0 warning(s)`. Record that line in the handover checklist. CI
   cannot do this step: there is no MQL4 compiler on any runner, so a green CI
   run says nothing about whether the Expert builds.
2. Attach it to one chart.
3. Enable AutoTrading. Allow live trading on the Expert.
4. Set `account.mode = "mt4"` and `mt4.files_dir`.
5. `doctor --connect` must print `venue=mt4` and exit 0.

Real money (`trade_mode=2`) still needs `--i-accept-risk` or `/live on I-ACCEPT-RISK`.
Paper P/L is not live P/L.
Nothing here guarantees profit.
