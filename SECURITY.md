# Security

Report vulnerabilities to conrad@skyphusion.org.

WARNING: Do not open a public issue for a live trading defect that
could move money.

## Secrets

Put secrets in the environment. Do not put them in `config.toml`.

| Name | Use |
| --- | --- |
| `MT5_LOGIN` `MT5_PASSWORD` `MT5_SERVER` | terminal login |
| `TELEGRAM_BOT_TOKEN` `TELEGRAM_CHAT_ID` | desk |
| `XAI_API_KEY` | Grok direct |
| `ANTHROPIC_API_KEY` | Claude direct |
| `ADVICE_URL` `ADVICE_TOKEN` | Computer worker |
| `CF_AIG_TOKEN` | AI Gateway (Worker secret only) |

Env vars override toml. The Computer worker uses Unified Billing. Do not
put a provider key in that worker.

Journal writes replace keys named `token`, `password`, `api_key`,
`grok_key`, and `claude_key` with `[REDACTED]`. BotFather token patterns
are stripped from journal fields, loop stderr, and Telegram `send`.

Only `TELEGRAM_CHAT_ID` is accepted. Replies go only to that chat.

These files are chmod 0600: `journal.jsonl`, `journal.jsonl.1`,
`journal.tg_offset`, `journal.lock`, `journal.heartbeat`, `HALT`.

A real-money account is refused unless you start with `--i-accept-risk`.
