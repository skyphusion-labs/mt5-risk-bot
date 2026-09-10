# Security

Report vulnerabilities to conrad@skyphusion.org. Do not open a public issue
for a live trading defect that could move money.

## Production secrets

Secrets live in the environment, never in `config.toml` (gitignored):
`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `XAI_API_KEY`, `ANTHROPIC_API_KEY`. Env vars override
toml if both are set.

Journal writes replace keys named `token`, `password`, `api_key`,
`grok_key`, and `claude_key` with `[REDACTED]`, and strip BotFather
token patterns from string fields. Loop stderr and Telegram `send`
strip the same BotFather pattern.

Only `TELEGRAM_CHAT_ID` is accepted. Updates from any other chat are
ignored (the update is still consumed). Replies go only to that chat.

`journal.jsonl` is chmod 0600 on open and after each write.
`journal.tg_offset` is chmod 0600 on each persist.
`journal.lock` is chmod 0600 when `run` takes the exclusive flock.
`HALT` is chmod 0600 when the process writes it.

A real-money account is refused unless the process was started with
`--i-accept-risk`.
