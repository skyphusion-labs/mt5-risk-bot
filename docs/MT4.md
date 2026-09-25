# MT4 mailbox ICD

MetaTrader 4 has no official Python package.
The bot does not load a DLL into the terminal.
The owned interface is a file mailbox in Common Files.

The Expert is `mt4/Experts/Mt4RiskBot.mq4`.
Python is `straightedge.broker.mt4_live`.
Engine still sends `MarketOrder` and `WorkingOrder` only.

## Two transports, one ICD

Everything below describes what travels. **Who carries it is now a choice**, and
`docs/TRANSPORT.md` is the decision record (#73).

| `mt4.mailbox_url` | Transport | The desk runs |
| --- | --- | --- |
| unset | the file mailbox described below | on the MetaTrader 4 host |
| set | HTTPS to `straightedge mt4-shim` on the MT4 host | anywhere |

The Expert is **byte-for-byte the same file either way**: it issues no
`WebRequest` and knows nothing about the network. The shim runs beside the
terminal, owns this mailbox, and turns one authenticated `POST /mt4/call` into
one round trip through it. The body on the wire is the same `key=value` block
this document specifies, carried opaquely, so every line below still applies.

## Files

Directory: `mt4.files_dir` or `MT4_FILES_DIR`.
That path is Terminal Common Files, not the data folder for one install.

Windows default if the key is empty:

```
%APPDATA%\MetaQuotes\Terminal\Common\Files
```

`%APPDATA%` in a configured path expands. `journal.lock` uses `msvcrt.locking`
there. With no `mt4.mailbox_url`, the bot and the terminal must run on the same
Windows host; with one, only the shim does.

| File | Writer | Reader |
| --- | --- | --- |
| `mt4_risk_bot.req` | Python | Expert |
| `mt4_risk_bot.res` | Expert | Python |

Python writes `.req.tmp` and replaces it onto `.req`.
The Expert CLAIMS `.req` by renaming it to `mt4_risk_bot.req.claim.<chart id>`
before it reads a byte, and reads the body from the claimed path.
The Expert writes `.res` in one open/write/flush/close.
Python accepts `.res` only when `id` matches the request.

The mailbox is global under Common Files.
Do not run two bots against one mailbox.

### One Expert, enforced

"Attach to one chart" used to be a rule the operator had to remember, on the
order-execution path. It is now what the software enforces, in two layers.

**The claim.** `Process()` never reads the shared name. It takes a terminal-wide
mutex (`GlobalVariableSetOnCondition`, the one primitive MQL4 documents as
atomic, and documents for exactly this: "a mutex at interaction of several
Expert Advisors working simultaneously within one client terminal"), then renames
`mt4_risk_bot.req` to `mt4_risk_bot.req.claim.<chart id>` and reads the body from
there. A second Expert reaching the same point finds the source gone, logs
`claim lost`, and executes nothing. Before this, the Expert read the whole body
from the shared name and deleted it AFTERWARDS, so two instances both got the
full request and both called `OrderSend`: one requested trade, two positions,
double the sized risk, and only one of the two in the journal, which corrupts
every drawdown and exposure figure derived from it.

**The singleton.** `OnInit()` takes a second terminal-wide lock and returns
`INIT_FAILED` when another instance holds it, so a duplicate attach fails loudly
instead of running:

```
mt4riskbot REFUSING TO START: another straightedge Expert is already running in
this terminal and owns the mt4_risk_bot mailbox (holder last seen 0s ago).
Attach this Expert to exactly ONE chart. Two instances would both send the same
order, so this one is stopping.
```

So if you do attach a second chart: the second Expert does not start, the line
above appears in the Experts log, the first Expert keeps trading, and no order is
duplicated. The holder refreshes its lock on every timer tick and every market
tick and releases it in `OnDeinit`, so a crashed instance frees it after
`SingletonStaleSeconds` (default 15) and a legitimate restart is never blocked.
Both locks are temporary globals, which MT4 deletes at terminal shutdown, so a
terminal crash cannot leave one behind on disk.

**Scope, stated plainly.** MQL4's atomic guarantee is per TERMINAL. Two separate
MT4 terminals on one host share Common Files and would contend for the same
mailbox; there the rename is the only barrier, and MQL4 does not document
`FileMove` as atomic. Run one terminal against one mailbox.

**Orphans.** A crash between the rename and the reply leaves
`mt4_risk_bot.req.claim.<chart id>` behind. That file is not the mailbox, so it
blocks nothing and no other instance waits on it; the same chart overwrites it on
its next claim (`FILE_REWRITE`). The orphaned request is deliberately NOT
replayed. The adapter times out and reports no result, which is the safe answer;
replaying a claimed order after a restart is how you get back the duplicate this
change removed.

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
comment=straightedge
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
| `rates` | Bars, oldest first. `timeframe` is `H1`, not an MT5 integer. Also selects the symbol, asks the terminal to fetch the series if it is short, and reports `bars_total`, `selected` and `history_error`. See History below. |
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

## History is per symbol AND timeframe

MT4 keeps a separate price series for every symbol/timeframe pair, and it builds
one only when something asks for it. A terminal with H4 charts open and a desk
configured for H1 therefore has H1 history for nothing. Measured on the rig:

```
EURUSD  bars=0    ATR=nan
USDJPY  bars=0    ATR=nan
XAUUSD  bars=200  ATR=17.2188
```

XAUUSD was the only symbol with an H1 chart. Nothing failed: `step_symbol` asked
for bars, got none, and returned, so two configured symbols were untradeable and
nothing said so.

**The operator opens no charts.** The desk asks for every configured symbol's
series at startup, which is what makes the terminal request it from the server,
and waits up to ten attempts one second apart. The ask is the fix.

`RatesReply` is what makes that possible, and it does three things the earlier
version did not:

- It calls `SymbolSelect` itself. A symbol absent from Market Watch has no series
  to serve, and `rates` previously depended on `Engine.start()` having selected
  it first, which is call order rather than a guarantee.
- When it holds fewer bars than were asked for, it touches the series (`iClose`
  on bar 0) before deciding there is nothing to send. Under the old code
  `iBars() == 0` set the row count to zero, so the loop never ran and no
  price-series function was reached at all; `iBars` alone is not the documented
  download trigger.
- It reports the state of the series on every reply, healthy or not:

| field | Meaning |
| --- | --- |
| `bars_total` | Bars the terminal holds for this symbol/timeframe, which can exceed `n`. |
| `selected` | `1` if `SymbolSelect` succeeded. `0` means the symbol is not in Market Watch. |
| `history_error` | `GetLastError()` after the touch. `4066` `ERR_HISTORY_WILL_UPDATED` means the download is in flight; `4073` `ERR_NO_HISTORY_DATA` means the terminal has none and is not fetching; `0` means it reported no error. |

Without `history_error`, `ok=1 n=0` meant three different things at once
(downloading now, not served under this name, genuinely empty) and the desk could
only read the reassuring one. It now tells "wait" apart from "the symbol name is
wrong", which is the difference between a nine-second startup pause and a run
that must not begin.

There is no `Sleep` in the handler and there must not be. `Process()` is a
single-threaded mailbox behind `gBusy` and the adapter's bridge times out at
5 seconds, so a wait in there would stall every other op. The wait lives in
`straightedge/history.py`.

An Expert older than this emits `n=` and the rows only. The adapter reports the
three fields as `None` in that case, never as zeros, so "the Expert cannot
answer" stays distinct from "the Expert answered zero". Recompile and reattach
`Mt4RiskBot.mq4` to get the fields.

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

## Startup waits for the Expert. Steady state does not.

MT4 is a GUI application. A desk started from a boot-triggered Windows
scheduled task therefore races the terminal's own launch: MT4 has to start, load,
log in to the broker and reach the Expert's first timer tick before anything can
answer on the mailbox.

Measured on a live host after its first reboot since setup: MT4 came back
healthy, the Expert was attached, the mailbox path was right, and the desk was
dead. `Engine.start()` sent ONE `ping` on the 5 second per-command timeout, the
ping timed out, the process exited, and nothing retried. Every visible
indicator read healthy and the bot was gone. An operator with no shell on that
host has no way to notice it or fix it.

`Engine.start()` now calls `Mt4Broker.startup_connect()`, which retries the
ping with a growing gap until the Expert answers or the budget runs out.

| | Budget | Set by | Used by |
| --- | --- | --- | --- |
| Startup | `mt4.startup_wait_sec`, default 180s | `startup_connect()` | `Engine.start()`, once |
| Steady state | `mt4.timeout_ms`, default 5000 | `FileBridge.timeout` | every other command |

**The two numbers are deliberately not one number.** `Engine.step_all()` calls
`ensure_connected()` on every step and `Engine._reconnect_broker()` calls
`connect()` on the trading path, and both of those keep the short budget. A
cold-boot-sized budget leaking into either of them would turn a transient blip
into a multi-minute stall while the desk holds live positions, which is worse
than the startup bug it would be fixing. `doctor --connect` also keeps the
short budget, because interactive diagnosis should fail fast.

**It is bounded, and the bound is stated.** Wall clock worst case is the budget
plus one `timeout_ms`: a new ping is only started while time remains, but a
ping already in flight is allowed to finish. An unbounded wait would convert a
wrong `files_dir`, a detached Expert or an absent MT4 into a process that hangs
forever looking busy, which is not an improvement on a crash.

**It says what it is doing.** Every attempt prints to stdout, flushed per line,
so a redirected desk log shows the wait as it happens rather than as one burst
afterwards:

```
mt4: waiting up to 180s for the Expert to answer on the mailbox
mt4: no reply yet, attempt 1 at 5.0s of 180s (mt4 bridge timeout); retrying in 1.0s
mt4: no reply yet, attempt 2 at 11.0s of 180s (mt4 bridge timeout); retrying in 2.0s
mt4: Expert answered on attempt 6 after 48.3s
```

Giving up names the elapsed time and what to check:

```
mt4 bridge never answered: 21 ping(s) over 180.4s, budget 180s, last error:
mt4 bridge timeout. Check that MetaTrader 4 is running, that
mt4/Experts/Mt4RiskBot.mq4 is attached to exactly one chart with AutoTrading
enabled, and that mt4.files_dir is the Terminal Common Files folder.
```

**A reply of `ok=0` is NOT retried.** That is a live Expert stating a
diagnosis, and waiting cannot change it. The partition is a type, not a
message: `FileBridge` raises `BridgeTimeout` (a `RuntimeError` subclass) when
nothing answered, and only that and `OSError` are retried.

**What this does not cover.** An in-process wait fixes the cold-boot race. It
does not cover MT4 taking longer than the budget, or MT4 dying later, because a
process that has exited cannot retry anything. The scheduled task should also
restart the desk on failure; the wait reduces how often that is needed, it does
not replace it.

**That scheduled task is now written down, and so is the half a restart cannot
fix.** `docs/RUNBOOK.md`, "Unattended (Windows scheduled task)", creates the
task and creates a second one beside it for `straightedge watch`, which reads
`journal.heartbeat` and is the first thing in this repo that ever did. The
reason the two tasks are not one is the reason this paragraph exists: a restart
brings the PROCESS back, and it brings it back DISARMED, because live arming is
per process and fc34 forbids putting `--i-accept-risk` anywhere a supervisor can
re-run it. So the failure that costs an unattended week is not the crash, which
heals; it is the desk sitting there ticking and refusing to trade with nobody
told. `watch` reports that as its own state, `ALIVE NOT TRADING
(live_not_accepted)`, with its own exit code, and a human re-arms from the chat.

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
`pyproject.toml` under `[tool.straightedge.coverage_floors]`.

## Attach

1. Compile `Mt4RiskBot.mq4` in MetaEditor. The Toolbox Errors tab must read
   `0 error(s), 0 warning(s)`. Record that line in the handover checklist. CI
   cannot do this step: there is no MQL4 compiler on any runner, so a green CI
   run says nothing about whether the Expert builds.
2. Attach it to one chart. A second attach refuses to initialise and prints
   `REFUSING TO START` in the Experts log; see "One Expert, enforced".
3. Enable AutoTrading. Allow live trading on the Expert.
4. Set `account.mode = "mt4"` and `mt4.files_dir`.
5. `doctor --connect` must print `venue=mt4` and exit 0.

For a desk that is NOT on this host, steps 1 to 3 are unchanged, and step 4
becomes: set `MT4_MAILBOX_TOKEN` on both machines, run
`straightedge mt4-shim` here, and set `mt4.mailbox_url` on the desk. See
`docs/TRANSPORT.md`.

Real money (`trade_mode=2`) still needs `--i-accept-risk` or `/live on I-ACCEPT-RISK`.
Paper P/L is not live P/L.
Nothing here guarantees profit.
