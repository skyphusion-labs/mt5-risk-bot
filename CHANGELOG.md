# Changelog

NOTE: Operator docs from 1.0.0 use 8th-grade Simplified Technical English.
Do not treat older changelog wording as the operator contract.
See README.md and docs/CONTRACT.md.

## Unreleased

Advice-staged symbols are whitelisted, and there are daily caps on sends and on advice turns (issue #13).

### The model no longer picks the instrument unchecked

- `cfg.symbols` is a SCAN list: it drives `/quote` and the auto scan. `market_signal` accepts anything the broker knows, so with `/approve always` the model chose the instrument and no gate checked it.
- A symbol the MODEL picked is now checked against `advice.symbols`, falling back to `symbols.names` when that is empty. Empty never means "allow anything". A miss is `reject` with reason `symbol_not_allowed`.
- The whitelist is a separate knob from the scan list on purpose: an operator who scans three pairs may still want to act on a fourth BY HAND. A human `/buy` is therefore NOT gated by it. The control exists because the model chose the symbol, not because the symbol is unusual.
- It gates OPENS only. An advice `close` on an unlisted symbol is still allowed: a control that can stop you reducing exposure is not a risk control.
- Precedence is explicit: the whitelist runs before the order is built, so an unlisted symbol reports `symbol_not_allowed` rather than `advice_stage_failed`. A rule genuinely did say no, and it avoids spending broker calls on an order that was never permitted.

### Two daily caps, because they bound two different things

- `risk.max_trades_per_day` counts OPENING sends across auto, telegram and advice, and refuses with `max_trades_per_day`. Churn was previously bounded only by `max_positions` plus `daily_loss_pct`, and `daily_loss_pct` fires after the money is gone. Counted at the send, never at the decision: a preview, a refused confirm and an expired stage all call `evaluate()` and none of them is a trade. Closes never count and are never capped.
- `advice.max_turns_per_day` counts advice turns and refuses BEFORE the provider is called. This is a COST control as much as a risk one: hosted inference is billed per turn, and a turn costs money whether or not it ends in an order, so the send cap cannot see that spend at all. A check placed after the call would cost exactly what it is meant to save.
- Both counters are durable beside the journal, in the same snapshot as the loss budget, for the same reason: a cap a crash loop can clear is not a cap. They reset only on a new UTC day.
- Both default to `0`, which disables them, so no existing config changes behaviour.

### Snapshot schema

- `journal.equity.json` is version 2, carrying `trades_today` and `advice_turns_today`. Version 1 still loads, with the counters restored as 0: a reader that rejected the version it wrote yesterday would fail closed on every existing install, which is a self-inflicted outage rather than a safety property. An unknown version is still refused.

### Slippage tolerance is per symbol, and a deviation the spread cannot fit is refused

Measured by Conrad on the live MT4 rig, 2026-09-24. `[risk] deviation_points` was a single global, default 20, and it is the maximum tolerated slippage in POINTS. A point is instrument-specific, which is the defect: on a 5-digit EURUSD 20 points is 2 pips, and on XAUUSD it is 20 cents against a measured spread of 45 points ($0.45), on an instrument whose ATR(14) H1 was $17.22. Every gold send was therefore offering the venue less than half of one spread of tolerance. `OrderSend` rejects that intermittently and nothing in the log named the cause, so what reached the operator was "trades randomly do not go through". End to end the number ran `engine.py` -> `mt4_live.py` -> `Mt4RiskBot.mq4`, which uses the value passed and falls back to its own `input int Slippage = 30` only when that value is <= 0.

- `[risk.symbol_deviation_points]` overrides `deviation_points` per symbol. Resolution is symbol-specific first, then the global default, and the answer carries its own provenance: every `open` and `close` record in `journal.jsonl` now has `deviation` and `deviation_source` (`symbol` or `default`), because 20 points read in a journal cannot tell an operator whether their override was consulted, mis-keyed, or never written. Closes resolve per symbol too and are never gated on it: a control that can stop you reducing exposure is not a risk control.
- Matching is case-insensitive and otherwise exact. A venue that calls gold `XAUUSD.m` must be keyed `XAUUSD.m`, because stripping a decoration means guessing which decorations name the same instrument. A near-miss falls back to the global default, which is a failure the map alone cannot catch, which is why the map is the smaller half of this change.
- **The gate.** A new entry whose effective deviation is below `min_deviation_spread_multiple` (default 1.0) times the LIVE spread is refused as `deviation_below_spread` before the order is sent. A misconfiguration that produced intermittent venue rejections is now one named refusal at our own gate. The reason names the symbol, the effective deviation, its source, the measured spread, the floor enforced, and the config key and value to set: it reaches the operator verbatim (`refused: <reason>`), and a refusal without the number to set is one an operator switches off rather than acts on.
- It refuses; it does NOT raise the deviation. Silently overriding an operator's risk figure would leave a number in force that is neither what they set nor anything they can read.
- **The default multiple is 1.0, and a larger one was tried first and is wrong.** 1.0 is the only multiple that needs no measurement: a market order crosses the bid/ask gap, so a tolerance below the spread can only be rejected. At 3.0 this gate refused 88 tests in this suite, every one of them encoding the paper broker's own EURUSD: a 10-point spread against the 20-point default, a 2x ratio that is ordinary retail FX and fills routinely. The evidence bounds the real boundary between 0.44x (gold, measured rejecting) and 2.0x (working) and does not locate it, so a default above 1.0 would encode a guess as though it had been measured, and would refuse trades on the strength of it. The multiple is configurable for an operator who wants margin, and `validate()` refuses anything below 1.0: there is no value that switches the gate off.
- The headroom the refusal RECOMMENDS is a separate constant (`DEVIATION_HEADROOM_MULTIPLE`, 3.0) from the floor it ENFORCES, because they are different kinds of claim. The floor is arithmetic and may refuse a trade. The headroom is judgement, it exists so an operator who writes the exact floor is not refused again by the first tick that widens the spread by one point, and judgement is not allowed to refuse anybody's trade.
- The spread is read at evaluate time, from the same tick the stop and sizing gates used, not from `spec.spread` (which the MT4 bridge does not populate) and not from a configured typical. That makes the gate as transient as the market, so `spread_too_wide` is applied FIRST: a temporary blowout is named as a wide spread, and only a spread the instrument carries under normal conditions is named as a misconfiguration. A spread of zero or less is a broken tick, and the gate abstains rather than reporting a pass it did not measure.
- `config.example.toml` and `config.handover.toml` seed `XAUUSD = 150` with the measurement in a comment. `XAGUSD = 150` is seeded because it has the same SHAPE and is labelled as NOT measured. No other symbol gets a value, and the measured numbers live in one place, `tests/live_measurements.py`, which separates what was read off the rig from what was merely chosen to make a fixture runnable.

### The refusal-reason roster is a denominator again (issue #61, closed)

- The roster asserted itself against `risk.py` by regex, and the regex required a closing quote. `reason="spec_not_measured:" + join(...)` therefore matched nothing, so the roster read GREEN while the module carried a reason no case covered. The instrument had silently stopped measuring its subject, which is the class this repo keeps finding.
- It now reads the AST (`tests/refusal_scan.py`), so a concatenation, an f-string, a module constant and a conditional all resolve. **The fix that matters is not the parser: it is that a site the scanner cannot read is REPORTED, not skipped.** `unresolved` is asserted empty before any count is compared, because a denominator built on a partial read is not a denominator.
- Sites that forward a reason rather than name one (`reason=self._halt_reason`) are returned and PINNED, not ignored. A silent ignore list is the same defect in a better costume, so a new forwarding form fails the suite and gets looked at.
- The count went 22 -> 24 the moment the scanner could see. The two that were missing are `spec_not_measured`, the reason #61 named, and `deviation_below_spread`, which was written in the SAME concatenated shape on purpose: rewriting it into a friendlier form would have left the fix unproven against the form that broke the scanner.
- `test_refusal_scan.py` feeds synthetic source, because a test that could only run against today's `risk.py` is pinned to the shapes that file happens to contain, which is how the hole opened. It includes the superseded regex as an executable assertion, so "the old scanner was blind to this" is measured rather than asserted in prose.

### Not fixed here

- `tests/test_refusal_reasons.py` runs most of its cases on a default tick of 20 points against the 20-point default deviation, i.e. EXACTLY at the new floor. It passes, and it is pinned by a boundary test so a later loosening to `<=` fails by name rather than turning half the suite red for no stated reason. The fixture itself is left alone.
- The paper broker's `default_spec` has no metals branch: `default_spec("XAUUSD")` returns a 5-digit FX spec with `point` 0.00001, so paper-mode gold is not gold. Fixing it needs a contract size and tick value nobody has read off a live venue, so it is reported rather than invented.
- No gate owns a crossed or zero-spread tick. `deviation_below_spread` abstains on one and says so, rather than absorbing a second defect quietly.
- `deviation_points = 0` is still accepted by `validate()`, and the MT4 Expert reads <= 0 as "use my own `input int Slippage`". That silent handover to a number configured on the other side of the bridge is now CAUGHT by the deviation gate rather than by validation, which is enough to stop a send, but the config key itself is still unvalidated.
- Nothing here ran against the rig, which is SSH-keyed from the lead's laptop only. That the gate refuses the configuration measured rejecting is proven in the suite; that a gold send at 150 points then FILLS is COULD NOT MEASURE from here.


### Two attached Experts can no longer both send the same order

- `Process()` in `mt4/Experts/Mt4RiskBot.mq4` claimed a request by reading the shared name and deleting it AFTERWARDS, with `FILE_SHARE_READ|FILE_SHARE_WRITE` on the open and only a per-instance `gBusy` flag for exclusion. Two attached Experts both read the whole body and both called `OrderSend`: one requested trade, two positions, double the sized risk, and only one of them journalled, which corrupts every drawdown, equity-peak and exposure figure derived from the journal. The only mitigation was the sentence "attach to one chart", written four times across the docs. That is a documented rule with no mechanism, on the order-execution path.
- The Expert now CLAIMS the request before reading it. It takes a terminal-wide mutex (`GlobalVariableSetOnCondition`, the one primitive MQL4 documents as atomic, and documents for exactly this use), renames `mt4_risk_bot.req` to `mt4_risk_bot.req.claim.<chart id>`, and reads the body from the claimed path. A loser finds the source gone, logs `claim lost`, and executes nothing. `FileMove` is the second barrier, never the guarantee: MQL4 does not document it as atomic.
- `OnInit()` takes a second terminal-wide lock and returns `INIT_FAILED` when another instance holds it, so a duplicate attach refuses to start and says why in the Experts log instead of silently competing. The holder refreshes the lock on every timer tick and market tick and releases it in `OnDeinit`; a crashed instance frees it after `SingletonStaleSeconds` (default 15), and both locks are temporary globals that MT4 deletes at terminal shutdown, so neither can survive a crash on disk and block a legitimate restart.
- An orphaned claim file is not the mailbox and blocks nothing; the same chart overwrites it on its next claim. The orphaned request is deliberately NOT replayed, because replaying a claimed order after a restart is the duplicate this change removes.
- Scope stated plainly in `docs/MT4.md`: MQL4's atomic guarantee is per TERMINAL. Two MT4 terminals on one host share Common Files, and there the rename is the only barrier.
- `mt4/README.md` and `docs/MT4.md` no longer ask the operator to remember "one chart". They state what the software enforces and what happens if two are attached.

## 1.5.0

The last-line size guard can fire (issue #55). It was dead. It recomputed the cap `lots_for_risk` had already applied, from the same entry, stop, spec and equity, with a LOOSER tolerance (`1e-6` against the sizer `1e-9`), so every input that would have tripped it had already been turned into 0 lots and reported as `size_zero`. A 497,664-case sweep reached the line 114,840 times and tripped it zero times.

- The guard now takes TWO caps and refuses on the tighter of them. The first is the old per-trade cap, kept as the backstop for a future change that loosens the sizer, with its tolerance brought into line with the sizer own (`1e-9`, not `1e-6`). The second is `RiskManager.loss_room`: what the account may still lose before the daily-loss or max-drawdown halt trips. That figure is derived from the persisted `EquitySnapshot` (`day_start_equity`, `peak_equity`), which `lots_for_risk` is never given, so the gate can DISAGREE with the sizer instead of recomputing it. A second layer that reads the first layer inputs is not a second layer.
- **Behaviour change an operator will see.** A trade whose full stop-out would carry the account through the daily-loss or drawdown halt is now refused as `size_exceeds_risk` instead of being sent. The halt used to fire after the loss; it now also refuses the size that would cause it. A configuration where one trade risks more than the whole daily loss budget (`risk_pct` times `max_risk_multiple` above `daily_loss_pct`) refuses every entry rather than sending trades the daily loss limit cannot absorb.
- The same 497,664-case sweep now reaches the line 114,840 times and trips it 54,111 times. A smaller sweep of the same shape ships as a test (2,880 cases, 1,080 reached, 444 refused, 636 allowed) and asserts BOTH counts: a guard that refuses everything is as useless as one that refuses nothing. The shipped grid is a quarter of the measured one because each case rewrites the persisted snapshot, which took the Windows CI leg from 65s to 4m37s at 11,520 cases; a sweep nobody tolerates in CI gets deleted.
- Independence is asserted as an experiment, not as an argument. Every input `lots_for_risk` receives is held exactly constant, only the persisted snapshot moves, and the verdict flips from `ok` to `size_exceeds_risk`.
- The per-trade half still cannot fire against the current sizer, by construction. It is exercised by a test that loosens the sizer by 1.5x on purpose and watches the refusal, so the term is a backstop and not decoration.
- `size_zero` and `size_exceeds_risk` stay two different words for two different situations, asserted on one manager and one account with only the stop distance changing.
- **The pin that held this line dead did NOT fail when the line became reachable, and that is the second finding.** `test_size_exceeds_risk_is_dominated_by_size_zero` said it would FAIL the day the guard could fire. It re-implemented the guard old arithmetic from `lots_for_risk` instead of calling `evaluate`, so what it measured was the SIZER, which this change does not touch, and it stayed green through the whole of it. It is deleted on purpose, named in the PR that deletes it, and replaced by a case that exercises the reason. A pin on a dead line has to call the line.
- Not fixed here, and still open: `size_zero` remains one word for two situations (a degenerate input, and a broker minimum lot that would risk more than the budget). Splitting it is a separate change to the reason vocabulary.


## 1.4.1

Tests and findings only. The shipped product is unchanged: no file under `src/` has a behaviour edit in this release, and the version moves only so these findings have a place to be recorded.

- Every refusal reason `RiskManager` can name now has a test that asserts the NAMED reason. `allowed is False` cannot tell you a control has stopped testing anything. The roster is `tests/test_refusal_reasons.py`, and it measures its own denominator against `risk.py`, so a reason added to the module without a case fails the suite instead of quietly lowering the count.
- The count was 20 reasons, not 15. Issue #11 reported 15 and 5 of them named; the measured figures are 20 reasons, 5 asserted by name, 11 refusal returns never executed (6 of those inside `evaluate`). After this change 19 of 20 are asserted by name and 1 refusal return is still unexecuted, for the reason below.
- Each guard was mutated so its refusal could not fire, and each test was watched going red before being trusted. 19 of 20 went red. The 20th did not, which is the first finding.

### Three pieces of `risk.py` cannot execute. Reported, not fixed.

None of these is a behaviour defect today and none is changed here. Each is pinned by a test that FAILS if it ever becomes reachable, so no test in this repo is left passing against a line that cannot run.

- **`size_exceeds_risk` cannot fire.** The last-line size guard recomputes exactly what `lots_for_risk` already checked, from the same entry, stop and spec, but with a looser tolerance (`1e-6` against the inner `1e-9`). Anything that would trip it has already been turned into 0 lots by the tighter inner check and is reported as `size_zero`. Deleting the guard outright leaves the whole suite green, which is how this was confirmed rather than argued. The reason string is still live in the product, but only from `engine.py` on the `/replace` path, which keeps the volume the broker already accepted and never calls `lots_for_risk`. That is the refusal an operator can actually receive, and it now has a test naming the site.
- **The `halted` fallback cannot fire.** `circuit_reason` reads `self._halt_reason or "halted"`. Every site that raises the halt flag sets a reason in the same block, so no public call can leave the flag up with an empty reason.
- **The zero-equity branch of the margin gate cannot be taken.** `if account.equity > 0:` guards a division, so the interesting case is a wiped account, and no wiped account reaches that line: the daily-loss gate fires first for every one of them, because a fresh day sets `day_start_equity` to the account equity and `0 >= 0` is true. The gate fails CLOSED on a wiped account, which is correct; the branch under it is simply unreachable.

- `circuit_reason`, the gate deciding whether the model may stage at all, had no test on four of its branches (`halted`, `trade_not_allowed`, `live_not_accepted`, `max_drawdown`). All four are now named. `circuit_reason` is also asserted NOT to latch a market verdict, which `circuit` does and it must not.
- `no_signal` and `already_in_symbol` have no production caller that can reach them: the auto leg returns before both (`engine.py`), and the desk only ever builds BUY or SELL. `already_in_symbol` is reachable from the desk and is tested there; `no_signal` is a defensive guard on a public method and is tested at that method.
- `outside_session` is reachable on the auto leg only. The desk passes `manual=True`, which bypasses the session window by design, so no operator command can produce it. Tested on the auto leg, off the bar clock.
- Where a reason is reachable through the desk or the auto leg, the assertion is the structured `reject` record from 1.2.0 rather than the return value, because the record is what an operator and an auditor read after the fact.


## 1.4.0


- Tests and gates only. No runtime behaviour changes, so this consumes no release number.
- MT4 golden wire transcripts. `tests/test_mt4_wire.py` drives the adapter through `FileBridge`, a real mailbox on disk, and a stand-in Expert that answers with the byte-exact text `mt4/Experts/Mt4RiskBot.mq4` emits. Every op has a transcript. The existing suite drove the adapter through a stub that returned native Python dicts, so the pipe-separated decoding never ran through the broker at all.
- The transcripts distinguish a MEASURED value from a DEFAULTED one. `Transcript.keys_sent()` answers whether the Expert put a field on the wire, and `value_sent()` gives the raw string it sent. A field the Expert never emits is absent, so the value reported for it is the adapter's own default.
- Pinned, not changed: the Expert sends 11 of the 15 keys the symbol reader consumes. `trade_mode`, `currency_base`, `currency_profit` and `currency_margin` are never on the wire, so a symbol spec always reports trade mode 4 (full). Nothing in `src/` reads `SymbolSpec.trade_mode` yet, so the consequence is latent.
- Pinned, not changed: a `tick_value` measured as zero is replaced by 1.0 and becomes indistinguishable from a genuine 1.0. The Expert emits four decimals, so any real value below 0.00005 arrives as zero.
- Field order is now checked. The Expert's pipe-join order for positions, orders and bars is asserted against the adapter's field tuples. A dict-returning stub is indifferent to order, so reordering one field used to leave the suite green.
- The reply-id match is now covered. A reply carrying a foreign id is not consumed, and the request is left in the mailbox after a timeout.
- Per-file coverage floors, declared in `[tool.straightedge.coverage_floors]` in `pyproject.toml`. `broker/mt4_live.py` has a floor of 97%. A package-wide `--cov-fail-under` cannot go red for one file.
- `broker/mt4_live.py` coverage: 86.94% to 97.59%. Tests touching it: 18 to 114. Suite: 305 to 415.
Smaller items batch (#15), in priority order. No version bump encoded here
(several PRs open today already claim conflicting numbers); assigned at
merge.

- **Redaction gap, and it was an exfiltration path, not just a logging
  gap.** `journal.redact_text` covered the BotFather token pattern only.
  Advice turns persist to `journal.advice.json` and are replayed verbatim
  into `Advisor._memory` on every subsequent provider call, so a
  `sk-ant-...` or `xai-...` key pasted once into a chat question (or
  echoed back in a model reply) was written to disk unredacted and
  resent to the third-party provider on every following turn. Verified
  the replay claim directly: the raw key showed up in the SECOND
  outbound HTTP payload in a test before this fix. Widened the pattern
  list; `_remember` (write) and `load` (read, so an already-persisted
  legacy turn is cleaned on the next process start too) both already
  routed through the one function, so one fix closes both the disk and
  the replay side.
- **`desk.py` vs `risk.py` mode-gate mismatch.** `Desk._live_needs_flag`
  gated the "arm live first" warning on `mode != "mt5"`; `risk.py`'s real
  send gate uses `mode in {"mt5", "mt4"}`. The desk side was wrong: on an
  MT4 real account, `/approve always` armed with no warning at all. The
  send was still refused downstream (`live_not_accepted`, risk.py's gate
  is correct), so this was never a path to an unwarned send, but the desk
  lied about the precondition until that refusal. Fixed to match risk.py's
  set form.
- **`/resume` message, and a sharper finding underneath it.** The issue
  named `daily_loss`: `clear_operator_halt` trusted a single `_halt_reason`
  slot that `circuit()` overwrites to `"halt_file"` on every call while the
  operator HALT file exists, so a poll tick between `/halt` and `/resume`
  made `/resume` claim "trading may resume" during a live `daily_loss`
  halt. For `daily_loss` / `max_drawdown` this really was message-only:
  every gate recomputes them fresh from the snapshot, so the next gate
  call re-halts. It is NOT message-only for `state_unreadable` /
  `state_unwritable`: neither is recomputed the same way (the state file
  is read once, at start), so the same clobbering let `clear_operator_halt`
  silently drop a COULD NOT MEASURE halt with nothing to re-derive it
  from, and let `_persist_state`'s evidence-preservation guard reopen and
  overwrite the corrupt file it exists to protect. Fixed with a dedicated,
  never-clobbered slot for the state-integrity reason, and daily_loss /
  max_drawdown re-derived from the snapshot instead of trusted from the
  stale slot.
- **Model pin.** `claude-sonnet-4-5` -> `claude-sonnet-5` in all 3 places
  it appears (`config.py` dataclass default, `config.py` loader default,
  `config.example.toml`). `grok_model` / `computer_model` checked and are
  current; not touched. Nothing asserted on the old string.
- **`ruff` and `mypy` in CI**, as a new `lint` job feeding the `ci`
  aggregator's `needs:`. `ruff` selects `E4,E7,E9,F` deliberately (real
  bugs: unused imports, undefined names, syntax-adjacent issues), not the
  rest of `E`/`W`: this codebase's own idiom runs long, dense lines, and a
  line-length gate would be a rewrite, not "a cheap win". `mypy` runs
  against `src/straightedge` with two narrow, documented per-module
  overrides (`desk.py`'s deliberately `object`-typed `engine`; the MT5
  binding's `Any | None` optional-import pattern in `mt5_live.py`), plus
  four small `dict[str, object]` annotations and two `PaperBroker`
  narrowing asserts in `__main__.py` that were genuine (if minor) type
  gaps, not gate suppressions. Both tools verified clean against `src/` at
  this commit before landing.

Docs corrected to match current code (#14). No behaviour change.

- `SECURITY.md` said real money is refused unless the bot started with
  `--i-accept-risk`. That was never the only path: `/live on I-ACCEPT-RISK`
  in the locked Telegram chat arms it too, and always has. The restart
  half of the original defect (a journal-restored `/live on` re-arming a
  fresh process) was already fixed by #17; this corrects the doc to name
  both arming paths and their per-process, not-restart-restored behaviour,
  instead of naming only one.
- `README.md` named `the gateway` (Cloudflare AI Gateway) alongside `the
  bot` / `the desk` / `the agent` with no scope. Only `AI_PROVIDER=computer`
  (the agent) routes through it; the default `grok` and `claude` are BYOK
  straight to `api.x.ai` / `api.anthropic.com`, with none of the gateway's
  billing, caching, rate limiting, or observability. Both the intro and
  the Names table now say so.
An MT4 market order can no longer be left open with no stop while the desk is told the send failed (issue #23).

- An MT4 market send is two calls: `OrderSend` with no stop, then `OrderModify`. If the second call failed, the Expert attempted one unchecked `OrderClose` and reported a plain failure either way. If that close also failed, or if the Expert could not even SELECT the ticket, the position stayed open with no stop and the desk was told the send failed. Nothing on either side reconciled that state.
- The rollback is now verified against the book rather than against a return value. The Expert re-reads the ticket and only reports a clean failure when `OrderCloseTime()` confirms it is gone. A failed `OrderSelect` now rolls back instead of abandoning the ticket.
- When the rollback cannot be verified, the reply carries `survivor_ticket=<ticket>` and `error=sl_modify_failed_position_live`. `Fail()` had no ticket field at all, so the wire previously could not express this state even in principle.
- `OrderResult.survivor_ticket`: `0` nothing survived, a positive ticket is live exposure the desk was told did not exist, `None` is COULD NOT MEASURE. Venues that attach the stop with the entry, including MT5, have no such window and report `0`.
- An Expert older than 1.2.0 does not send the field. That reads as `None`, never as `0`. An unanswered question is not an all clear.
- Two journal events: `unmanaged_position` and `survivor_unknown`. Both are also sent to Telegram in words, because a live unstopped position the desk does not know about is not a journal-only condition.
- Working orders get the same treatment, with `OrderDelete` and `sl_modify_failed_order_live`.
- `OnInit` scans the book at startup and prints every position that is open with no stop. New `ReconcileMagic` input filters the scan; `0` reports all. The Expert reports these and does not adopt them.
- `docs/MT4.md` documented the two-step entry as an ECN feature and stated that the Expert closes the ticket on a modify failure. It did not close it reliably. The section now states the three outcomes and which one leaves money at risk.

Ships the handover config gate (#25). `/approve always` and `/auto on` are the
two paths to a real-money send with no human keystroke: `/approve always`
sends inside the same Telegram `handle()` call as the advice turn, and
`/auto on` trades from the EMA signal with no confirm step at all.

- `telegram.allow_approve_always` and `telegram.allow_auto` in `config.toml`
  (env: `TELEGRAM_ALLOW_APPROVE_ALWAYS`, `TELEGRAM_ALLOW_AUTO`). Both default
  true, so an existing deployment that never sets these keys is unaffected.
- Set either false and the matching command is refused with a named reason
  (`approve_always_disabled`, `auto_disabled`), journaled as `reject`
  (`source=telegram`, `stage=approve`/`auto`), and never answered in chat.
  With no journal reachable the refusal still prints to stderr.
  `/approve off` and `/auto off` are never refused.
- A value that is present but not a clean boolean parses to false, never to
  the default: a config typo or a string-valued env var (`bool("false")` is
  `True` in Python) can only ever remove the capability, never grant it.
- `config.handover.toml`, a new file, ships with both set false. Copy it to
  `config.toml` for a handed-over desk.
- The default stays true on purpose (flipping it would silently change every
  existing deployment); the resulting gap is closed by observability, not a
  stricter default. `doctor` and `run` print the posture
  (`approve always: allowed|disabled`, `auto: allowed|disabled`), and every
  `start` journal record carries `approve_always_allowed` / `auto_allowed`,
  so a session's posture is readable both live and after the fact from
  `journal.jsonl`.

Version chosen, not assumed: `main` is 1.1.5, and PR #40 (1.1.6) and PR #49
(1.2.0) are both open. This adds new config surface rather than fixing a
defect in existing behaviour, so MINOR under the project rule; 1.3.0 avoids
colliding with either open lane's claimed number. Whoever merges last still
needs to renumber deliberately; a clean merge is not evidence the version is
right (see #38's "version trap").


## 1.3.2

The MT4 symbol reader no longer fabricates values it never measured (issue #30, superseding #12).

- All 15 reads in `Mt4Broker.symbol` used `d.get(key, DEFAULT) or DEFAULT`. `or` fires on a legitimate ZERO as well as on absence, so a broker-reported zero became a EURUSD-shaped default that nothing downstream could tell from a measurement. Two real producers of zero: `MarketInfo` answers 0 for a symbol that is not in Market Watch, and the Expert truncated `tick_value` to four decimals so any real value below 0.00005 arrived as zero.
- `SymbolSpec.unmeasured` records the fields that were not measured. An unmeasured field is left at a value that cannot be mistaken for usable, never at a plausible default.
- The risk gate refuses with `spec_not_measured:<fields>` BEFORE any gate reads the spec. Previously an unusable `volume_step` produced `size_zero`, which says the budget was too small: a different fact. The last-line guard could not catch the tick-value case at all, because `risk.py` recomputes `money_per_lot_at_stop` from the same corrupt spec.
- `1.0` was not a conservative default. Below 1.0 it undersizes, which is safe; above 1.0 it oversizes by exactly the ratio, so a 2.5 tick value spends 2.5x the intended budget. MQL4 has ONE tick-value identifier, `MODE_TICKVALUE`, with no loss-leg variant, so the MT5 remedy does not transfer and the honest fix is to refuse.
- The 4 fields the Expert cannot send (`trade_mode`, `currency_base`, `currency_profit`, `currency_margin`) are marked unmeasured instead of invented. `trade_mode` no longer defaults to 4 (full trading), which had meant a close-only symbol presented as fully tradable. That was LATENT rather than live: nothing in `src/` reads `SymbolSpec.trade_mode`, only `Account.trade_mode` is consumed. Closed so it cannot become live later.
- Zero remains a real reading where zero is real: `digits` on an instrument quoted in whole points, and `stops_level`, `freeze_level` and `spread`. Only fields where zero is impossible are treated as failed measurements.
- The Expert now serialises `volume_min`, `volume_max`, `volume_step` and `tick_value` with 8 decimals instead of 2 and 4. A 0.001 lot step used to arrive as `0.00` and refuse every order with no explanation. This removes TRUNCATION as a producer of zero; it does not remove zero itself, and that one is still a refusal.
- `docs/MT4.md` now states field by field what MT4 can and cannot supply, and says outright that MT4 has one tick value by design, so the loss-leg field is not re-proposed.

Currency-exposure limit: confirm the pair, or say the limit does not apply (issue #10).

- The pair is the first six alphabetic characters after non-alphabetic characters are dropped, and BOTH halves must be recognised currency codes (ISO 4217 plus the metal codes ISO assigns, so `XAUUSD` and `XAGUSD` parse). The table CONFIRMS a pair; it never refuses a trade.
- Pairs whose base or quote contains an M now parse. `USDMXN`, `MXNJPY`, `EURMXN`, `GBPMXN` and `CADMXN` previously did not, so those positions were absent from the per-currency limit.
- Every vendor suffix convention resolves (`EURUSDm`, `EURUSD.a`, `EURUSD_i`, `EURUSDmicro`, `EURUSD-5`), and so does a separator inside the pair (`EUR.USD`, `EUR/USD`).
- One rule, two outcomes. If either half is not a recognised code, or six alphabetic characters do not exist, the limit DOES NOT APPLY: the trade is ALLOWED and the exclusion is recorded as `currency_limit_not_applicable` with the excluded symbols. That covers an instrument that cannot be a pair (`US30`, `USOIL`, `GER40.cash`), decoration that hides the pair (`FXEURUSD`, `mEURUSD`), and a code missing from the table.
- There is no third state. Cannot-tell and is-not-FX get the same treatment, because the honest answer to both is the same: do not pretend to measure currency exposure, do not block the trade, make the exclusion visible.
- Not applicable is never silent. Silence was the original defect.
- A code missing from the table degrades to allowed-and-recorded, never to refused, so completeness is desirable rather than a safety property. Crypto codes are not in the table, so `BTCUSD` is recorded as excluded.
- `currency_exposure` raises for a symbol that is not an FX pair, so the silent skip cannot be reintroduced by a future caller. `evaluate` classifies first, so the `exposure_unmeasured` refusal is a tripwire against caller/classifier divergence and cannot fire from a broker symbol. A non-FX position contributes nothing to currency exposure, which is correct rather than an underestimate.

## 1.3.0

Sender-level authorization for Telegram commands (GHSA-9fg6-2x5f-3jvp).

- `TELEGRAM_ALLOW_SENDERS`, or `telegram.allow_senders` in `config.toml`, lists the Telegram sender ids that may command the desk. Every inbound command is checked against it, read-only commands included. A comma separates ids in the environment variable.
- An update whose sender cannot be read is refused. An identity that was not measured is not an authorized one.
- A group, supergroup, or channel chat id is negative. On a negative chat id with an empty allow-list, `run` and `doctor` exit non-zero instead of starting.
- An empty allow-list on a private chat id is unchanged behaviour. An existing single-operator deployment needs no config edit.
- A refused command is journaled as `command_rejected` with the sender id, the chat id, and the command name. It is never answered in chat.


## 1.1.5

- Safety fix. A pre-trade check that never ran is no longer treated as a check that passed. MQL5 `order_check` reports a PASSED check as retcode `0`, and the MT5 adapter used to synthesize retcode `0` when the terminal call returned nothing, so the engine guard let the failure through and sent the order. A call that returns nothing now yields `RETCODE_UNKNOWN` (`-1`) and `OrderResult.measured` is false. The engine aborts before sending.
- `order_check_fail` now carries `reason`: `broker_refused` (the venue rejected the check) or `not_measured` (the venue returned nothing, so the check never ran). An operator can tell "the broker said no" from "we never asked".
- MT4: a mailbox reply carrying no `ok` and no `retcode` was reported as `REJECT`, which said the broker refused when the Expert had in fact answered nothing. It is now `not_measured`. Both abort, so this changes the reason, not the outcome.
- A genuine `order_check` retcode `0` still passes, and a genuine venue rejection still reports `broker_refused`.


## 1.1.4

- `flatten` can fail loudly. It counts positions requested, positions confirmed closed, positions closed elsewhere, and survivors, and returns a `FlattenReport`. Every count goes to `journal.jsonl` as `flatten`.
- A sweep that leaves risk open also writes `flatten_incomplete` and alerts `FLATTEN INCOMPLETE: n still open` with the tickets. `notify_events` cannot silence that alert (`telegram.ALWAYS_NOTIFY_EVENTS`).
- Survivors are no longer folded into `_seen_pos`. A position that outlived a flatten alerts again instead of being marked already-seen.
- A close is confirmed only when the filled volume covers the whole position. `RETCODE_OK` includes `DONE_PARTIAL`, so `ok=True` with residual volume now counts as a survivor, not a close. `RETCODE_OK` itself is unchanged.
- An unreadable book on a flatten is COULD NOT MEASURE, reported as an incomplete sweep with the survivor count as an upper bound. Never a clean one.
- `flatten` no longer raises. A broker call that fails mid sweep is journaled (`close_failed`, `cancel_failed`, `close_partial`), the sweep finishes, and `halted` is still set. Before this, an exception on one close skipped the halt entirely.
- Cancelling working orders checks its results too. A refused cancel is a survivor.
- `/halt` reports what happened, with counts, instead of the fixed string `flattened and halted.`


## 1.1.3

Restart no longer restores the permissive state and discards the protective one (issue #7).

- Risk state persists to `journal.equity.json` next to the journal: `day_key`, `day_start_equity`, and `peak_equity`. `start` reads it back. A restart inside the same UTC day does not hand out a new loss budget, and the drawdown gate does not read a zeroed peak. A genuine new UTC day still resets the daily budget; the peak is not daily.
- The snapshot is written whenever one of those three fields moves, not only at a halt. A file written only at the halt has already lost the peak.
- The write is atomic (temp file, fsync, rename). A kill mid-write cannot leave a truncated file.
- What is persisted is the INPUT the gates recompute from, never a stored verdict. No halt is made sticky by this file.
- A corrupt, truncated, mistyped, non-finite, or newer-version snapshot halts with reason `state_unreadable` and the file is left alone for inspection. A snapshot that cannot be written halts with reason `state_unwritable`. Both are COULD NOT MEASURE and both fail closed; neither is treated as a clean start.
- To reset the peak, stop the bot and delete `journal.equity.json`. Point the bot at a different account and delete it too.
- Two new journal events: `live_not_restored` and `risk_state_error`. `/risk` also prints the state error, so COULD NOT MEASURE is visible at start and in chat, not only at the first refusal.
- `live_accepted` is now PER PROCESS. `start` never arms real money from a `live_on` journal record; it writes `live_not_restored` and `/live` says arming was not restored. Re-arm with `/live on I-ACCEPT-RISK`. A crash loop can no longer keep real money armed from a `/live on` typed weeks earlier.


## 1.1.2

- Tests compare paths with `pathlib.Path`, not slash strings. Windows `\tmp\...` vs `/tmp/...` is not a failure.


## 1.1.1

- Windows can run the bot next to MT4. `journal.lock` uses `msvcrt.locking` on Windows and `flock` on Unix. `import fcntl` no longer happens at module load.
- MT4 mailbox retries `unlink` / `replace` on `PermissionError` (NTFS sharing). Writes LF even on Windows. Reads FILE_ANSI via `mbcs`.
- Empty `mt4.files_dir` on Windows defaults to `%APPDATA%\\MetaQuotes\\Terminal\\Common\\Files`. `%APPDATA%` in the path expands.
- Expert opens the mailbox with `FILE_SHARE_READ|FILE_SHARE_WRITE` and writes `.res` via `.res.tmp` + `FileMove`.


## 1.1.0

- MetaTrader 4 is a third venue. `account.mode = "mt4"` selects `Mt4Broker`.
- MT4 has no official Python package. The owned ICD is a Common Files mailbox (`mt4_risk_bot.req` / `.res`) spoken by `mt4/Experts/Mt4RiskBot.mq4`. See `docs/MT4.md`.
- `run --mode mt4`. `doctor --connect` pings that mailbox when mode is `mt4`.
- Real-money MT4 (`trade_mode=2`) uses the same fuse as MT5: `--i-accept-risk` or `/live on I-ACCEPT-RISK`.
- `MT4_FILES_DIR` / `mt4.files_dir` is the Common Files path. Not a secret.


## 1.0.0

- Development Status Production/Stable. Production bar holds: exclusive `journal.lock` (second `run --loop` exits 2), `journal.heartbeat` each successful `step_all`, journal rotate to `journal.jsonl.1` at 10 MiB, CI pytest on Python 3.12 and 3.13 plus `doctor`, launchd `KeepAlive` / `Umask` 63 / heartbeat path, pytest and PR CI coverage >= 80%.
- Paper is still the default. No profit guarantee.
- `run` takes an exclusive flock on `journal.lock` next to the journal. A second `run --loop` on the same journal exits 2 with stderr `already running`.
- launchd example: `KeepAlive`, `Umask` 63 (077), `journal.heartbeat` path comment. Secrets stay `REPLACE_ME`.
- Advice conversation persists in `journal.advice.json` (last 40 turns, chmod 0600) and restores on restart. This is the desk context, not an in-memory buffer.
- `AI_PROVIDER=computer` sends `/ask` to a Cloudflare Computer Durable Object. Working memory is the workspace filesystem (`notes.md`, `log.md`, `snapshot.md`, `history.json` from `journal.tail`). Inference is AI Gateway Unified Billing (`CF_AIG_TOKEN`), not provider BYOK.
- `broker_for(cfg)` selects PaperBroker or Mt5Broker from `account.mode`. `PendingOrder.kind` is `limit` or `stop`; engine lists and replaces from that string, not MT5 type ints.
- `PendingOrder.kind` is `limit` or `stop`. Engine and desk never read MT5 `type_code`.
- `Side` has no MT5 order type integers. Adapters map buy/sell for `order_send`.
- Engine uses `OrderResult.unchanged` and `OrderResult.invalid_stops`. It does not import MT5 retcode integers.
- Advice send: default is `/confirm`. `/approve always` sends after risk preview. README and advice context match CONTRACT.
- Runtime journal siblings (`journal.advice.json`, `journal.heartbeat`, `journal.tg_offset`, `journal.jsonl.1`) are gitignored.


## 0.3.0

- Development Status Beta. Production bar holds: Telegram 429/5xx retry and persisted `getUpdates` offset, MT5 reconnect, journaled confirm restore, secret redaction and chat_id lock, doctor paper plus `--connect` fail-closed, launchd, HALT, `--i-accept-risk`, `run --loop` survives a bad `step_all`, config validation on start, pytest and CI coverage >= 80%, journal and offset chmod 0600, close-by hedge-only with a netting fake.
- Paper is still the default. No profit guarantee.


## 0.2.0

- Telegram is the desk: /buy /sell /close /sl /tp /be /trail /history /risk /confirm.
- `/confirm` reprices market orders, re-runs risk, honors halt, reports retcode.
- Partial close `/close TICKET VOL`. Staged confirm is not overwritten.
- Limit/stop working orders (`limit=` / `stop=`), `/orders`, `/cancel TICKET`.
- Tick fill alerts and SL/TP checks run even when `/auto` is off.
- `/quote` with no symbol lists the configured book. `/trail` never loosens.
- `/trail on|off` manages existing positions every tick without EMA entries. Default off.
- `/sl` `/tp` TICKET modify a working order (`TRADE_ACTION_MODIFY`) as well as a position.
- `/symbols list|add|remove` edits the configured book at runtime.
- `/tp TICKET PRICE VOL` scales out VOL at PRICE; circuit still refuses.
- UTC day roll sends a recap notify (equity vs day_start, journal tail). `/recap` dumps it. Not a trade.
- `doctor` pings Telegram (skip if unset) and paper `/buy` `/confirm` `/close` with no live terminal.
- `doctor --connect` is non-zero if the MT5 binding is missing or login fails.
- Live `Mt5Broker.orders` maps `orders_get` onto `PendingOrder` (covered without a terminal).
- If the circuit would halt, advice is hold/close only; buy/sell is not staged.
- `/replace TICKET PRICE` moves a working order; circuit and risk_pct still refuse.
- `/reverse TICKET` stages close plus opposite market. `/confirm` is two market sends. Circuit and risk_pct still refuse.
- `/closeby TICKET OTHER` offsets opposite positions (`TRADE_ACTION_CLOSE_BY`). Hedge-only on live; paper always hedges. Paper P/L is not live.
- `run --loop` retries Telegram 429/5xx with backoff, resumes `getUpdates` at the same offset, and re-`initialize`s a dropped MT5 IPC. One bad tick is journaled (`reconnect` / `loop_error`).
- `step_all` calls `ensure_connected` before `account`.
- `getUpdates` offset is persisted as `journal.tg_offset` after each handled or skipped update. Restart does not replay or drop commands.
- `journal.jsonl` and `journal.tg_offset` are chmod 0600.
- HALT file is chmod 0600. umask 077 at process start.
- Journal, `loop_error` stderr, and Telegram chat echoes redact BotFather tokens (`[REDACTED]`).
- Staged `/confirm` is journaled (`confirm_stage`) and restored on `start` if the TTL has not expired.
- Grok (xAI) and Claude (Anthropic) via env keys. Last 6 turns kept. Advice never auto-sends.
- Advice JSON may stage `limit=` / `stop=` or close TICKET. `/ask` context includes `/risk`, orders, positions, quotes.
- Auto EMA regime is off until `/auto on`.


## 0.1.0

- Risk-first engine: 0.5% per trade, daily-loss circuit, drawdown circuit, HALT file.
- Paper broker and synthetic/CSV backtest. Live adapter for MetaTrader5 / mt5-mac.
- Telegram alerts and /status /positions /halt /resume.
- CI jobs `ci` and `coverage` (80% fail-under) for the org `main` gate.
