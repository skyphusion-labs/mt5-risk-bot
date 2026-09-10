# Changelog

## 0.2.0

- Telegram is the desk: /buy /sell /close /sl /tp /confirm, free-text advice.
- Grok (xAI) and Claude (Anthropic) via env keys. Advice never auto-sends.
- Auto EMA regime is off until `/auto on`.

## 0.1.0

- Risk-first engine: 0.5% per trade, daily-loss circuit, drawdown circuit, HALT file.
- Paper broker and synthetic/CSV backtest. Live adapter for MetaTrader5 / mt5-mac.
- Telegram alerts and /status /positions /halt /resume.
- CI jobs `ci` and `coverage` (80% fail-under) for the org `main` gate.
