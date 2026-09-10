# Changelog

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
- Live `Mt5Broker.orders` maps `orders_get` onto `PendingOrder` (covered without a terminal).
- If the circuit would halt, advice is hold/close only; buy/sell is not staged.
- `/replace TICKET PRICE` moves a working order; circuit and risk_pct still refuse.
- `/reverse TICKET` stages close plus opposite market. `/confirm` is two market sends. Circuit and risk_pct still refuse.
- `/closeby TICKET OTHER` offsets opposite positions (`TRADE_ACTION_CLOSE_BY`). Hedge-only on live; paper always hedges. Paper P/L is not live.
- `run --loop` retries Telegram 429/5xx with backoff, resumes `getUpdates` at the same offset, and re-`initialize`s a dropped MT5 IPC. One bad tick is journaled (`reconnect` / `loop_error`).
- `step_all` calls `ensure_connected` before `account`.
- `getUpdates` offset is persisted as `journal.tg_offset` after each handled or skipped update. Restart does not replay or drop commands.
- `journal.jsonl` and `journal.tg_offset` are chmod 0600.
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
