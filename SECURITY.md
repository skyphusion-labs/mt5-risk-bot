# Security

Report vulnerabilities to conrad@skyphusion.org.

WARNING
Do not open a public issue for a live trading defect that could move money.

The bot is the Python process on this computer.
The desk is Telegram chat commands.
The agent is the Cloudflare Computer worker.
The gateway is Cloudflare AI Gateway `mt5-risk-bot`.

## Production secrets

Secrets live in the environment.
Do not put secrets in `config.toml`.
`config.toml` is gitignored.

Secret names:

- `MT5_LOGIN`
- `MT5_PASSWORD`
- `MT5_SERVER`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `XAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `ADVICE_URL`
- `ADVICE_TOKEN`

The agent uses `CF_AIG_TOKEN` and `ADVICE_TOKEN`.
The agent bills through the gateway with Unified Billing.
Do not put a provider key on the agent.
Env vars override toml if both are set.

Journal writes replace keys named `token`, `password`, `api_key`, `grok_key`, and `claude_key` with `[REDACTED]`.
Journal writes also strip BotFather token patterns from string fields.
Loop stderr and Telegram `send` strip the same BotFather pattern.

Only `TELEGRAM_CHAT_ID` is accepted.
Updates from any other chat are ignored.
The bot still consumes those updates.
Replies go only to that chat.

`journal.jsonl` is chmod 0600 on open and after each write.
`journal.tg_offset` is chmod 0600 on each persist.
`journal.lock` is chmod 0600 when `run` takes the exclusive flock.
`HALT` is chmod 0600 when the bot writes it.

WARNING
A real-money account is refused unless the bot started with `--i-accept-risk`.
