# Changelog

## 0.2.0

- Telegram is the desk: /buy /sell /close /sl /tp /be /trail /history /risk /confirm.
- `/confirm` reprices market orders, re-runs risk, honors halt, reports retcode.
- Partial close `/close TICKET VOL`. Staged confirm is not overwritten.
- Limit/stop working orders (`limit=` / `stop=`), `/orders`, `/cancel TICKET`.
- Tick fill alerts and SL/TP checks run even when `/auto` is off.
- `/quote` with no symbol lists the configured book. `/trail` never loosens.
- Grok (xAI) and Claude (Anthropic) via env keys. Last 6 turns kept. Advice never auto-sends.
- Advice JSON may stage `limit=` / `stop=` or close TICKET. `/ask` context includes `/risk`, orders, positions, quotes.
- Auto EMA regime is off until `/auto on`.

## 0.1.0

- Risk-first engine: 0.5% per trade, daily-loss circuit, drawdown circuit, HALT file.
- Paper broker and synthetic/CSV backtest. Live adapter for MetaTrader5 / mt5-mac.
- Telegram alerts and /status /positions /halt /resume.
- CI jobs `ci` and `coverage` (80% fail-under) for the org `main` gate.
