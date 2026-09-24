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

## 1.4.0

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


## 1.3.0

Sender-level authorization for Telegram commands (GHSA-9fg6-2x5f-3jvp).

- `TELEGRAM_ALLOW_SENDERS`, or `telegram.allow_senders` in `config.toml`, lists the Telegram sender ids that may command the desk. Every inbound command is checked against it, read-only commands included. A comma separates ids in the environment variable.
- An update whose sender cannot be read is refused. An identity that was not measured is not an authorized one.
- A group, supergroup, or channel chat id is negative. On a negative chat id with an empty allow-list, `run` and `doctor` exit non-zero instead of starting.
- An empty allow-list on a private chat id is unchanged behaviour. An existing single-operator deployment needs no config edit.
- A refused command is journaled as `command_rejected` with the sender id, the chat id, and the command name. It is never answered in chat.


## 1.2.0

- Every refusal on the desk and advice paths is now a structured journal record. Before this, `reject` was written on the auto/EMA leg only, so a refusal of anything a human or the model initiated left no machine-readable trace and could only be read as the English reply `refused: <reason>`. Asserting a gate on that string is asserting on prose.
- One event name for every gate that says no, on every path: `reject`, carrying the NAMED `reason` plus `source` (`auto`, `telegram`, `advice`) and `stage` (`signal`, `stage`, `stage_close`, `confirm`, `reverse`, `confirm_reverse`, `reverse_after_close`, `approve`). `symbol`, `kind`, `rr`, `ticket`, and `command` ride along when the refused request had them. The auto leg now carries `source` and `stage` too, so the discriminator is total instead of being read from an absent field.
- A refusal is journaled and is never echoed back into the chat that triggered it. It goes through `journal.write`, never `Engine._emit`, which is the chat broadcast path.
- With no journal configured a refusal still prints to stderr. A control that goes silent because a file is missing cannot report that it was exercised.
- The advice turn itself is now recorded: `advice_turn` with `provider`, `session`, `action`, `symbol`, `sl`, `tp`, `limit`, `stop`, `ticket`, and `staged`. The question and the model reply are never journaled, so the redaction surface does not grow and `advice_history` stays free of prose.
- `advice_circuit_block` records the circuit refusing to let the model stage at all. That gate decided whether the model could trade and wrote nothing.
- `advice_stage_failed` carries `measured=false` for an advice action that could not be turned into an order at all. COULD NOT MEASURE is not REFUSED and is deliberately not a `reject`, so a refusal-reason count cannot absorb an unmeasured outcome.
- `/auto on` and `/auto off` write `auto_on` and `auto_off`. `/live` and `/approve` were both audited and arming the autonomous trader was not.
- No behaviour changes. Every refusal returns the same reply it did before; the records are additive.


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
