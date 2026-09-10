# Changelog

NOTE: Operator docs from 1.0.0 use 8th-grade Simplified Technical English.
Do not treat older changelog wording as the operator contract.
See README.md and docs/CONTRACT.md.

## 1.0.0

- Development Status Production/Stable. Production bar holds: exclusive `journal.lock` (second `run --loop` exits 2), `journal.heartbeat` each successful `step_all`, journal rotate to `journal.jsonl.1` at 10 MiB, CI pytest on Python 3.12 and 3.13 plus `doctor`, launchd `KeepAlive` / `Umask` 63 / heartbeat path, pytest and PR CI coverage >= 80%.
- Paper is still the default. No profit guarantee.
- `run` takes an exclusive flock on `journal.lock` next to the journal. A second `run --loop` on the same journal exits 2 with stderr `already running`.
- launchd example: `KeepAlive`, `Umask` 63 (077), `journal.heartbeat` path comment. Secrets stay `REPLACE_ME`.
- Advice conversation persists in `journal.advice.json` (last 40 turns, chmod 0600) and restores on restart. This is the desk context, not an in-memory buffer.
- `AI_PROVIDER=computer` sends `/ask` to a Cloudflare Computer Durable Object. Working memory is the workspace filesystem. Inference is AI Gateway Unified Billing (`CF_AIG_TOKEN`), not provider BYOK.
- `PendingOrder.kind` is `limit` or `stop`. Engine and desk never read MT5 `type_code`.

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
