# mt5-risk-agent

Cloudflare Computer Durable Object that is the desk's advice brain.
Working memory is the workspace filesystem (`notes.md`, `log.md`, `snapshot.md`).
Inference goes through AI Gateway Unified Billing (`CF_AIG_TOKEN`), not provider BYOK.

This package is an early Computer preview. The Python bot still runs next to MT5.

## Secrets (never in git)

```
cd agent
npx wrangler secret put CF_AIG_TOKEN
npx wrangler secret put ADVICE_TOKEN
```

Create an AI Gateway named `mt5-risk-bot` (matches `vars.AI_GATEWAY_ID`) on account
`fabcb25d9c7eb087110ec474a03e50d2`. Unified Billing, no provider keys in the Worker.

## Desk

```
export AI_PROVIDER=computer
export ADVICE_URL=https://mt5-risk-agent.<account>.workers.dev/ask
export ADVICE_TOKEN=...
```

`/ask` and free text POST `{session, question, context}`. Session is the Telegram chat id.
The agent reads and writes its workspace; it does not send trades.
