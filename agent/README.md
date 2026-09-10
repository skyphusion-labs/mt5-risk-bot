# mt5-risk-agent

Cloudflare Computer Durable Object that is the desk's advice brain.
Working memory is the workspace filesystem (`/workspace/notes.md`,
`log.md`, `snapshot.md`) plus Computer tools (`read`/`write`/`edit`/`ls`/`grep`).
Inference is AI Gateway Unified Billing, not provider BYOK.

Live:

- Worker: `https://mt5-risk-agent.skyphusion.workers.dev`
- Health: `GET /health` -> `ok`
- Ask: `POST /ask` with `Authorization: Bearer ADVICE_TOKEN`
- Gateway: `mt5-risk-bot` (account `fabcb25d9c7eb087110ec474a03e50d2`)
- Model: `xai/grok-4.6`

This package is an early Computer preview. The Python bot still runs next to MT5.

## Inference path (docs)

New calls use the AI Gateway REST API, not `/compat`:

```
POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions
Authorization: Bearer CF_AIG_TOKEN
cf-aig-gateway-id: mt5-risk-bot
cf-aig-collect-log-payload: false
{"model":"xai/grok-4.6","messages":[...]}
```

See [REST API](https://developers.cloudflare.com/ai-gateway/usage/rest-api/) and
[Unified Billing](https://developers.cloudflare.com/ai-gateway/features/unified-billing/).
If `Authorization` is sent on `gateway.ai.cloudflare.com`, the gateway forwards
that header to xAI as a provider key and Unified Billing is skipped.

## Secrets (never in git)

Worker secrets: `CF_AIG_TOKEN`, `ADVICE_TOKEN`.

```
cd agent
npx wrangler secret put CF_AIG_TOKEN
npx wrangler secret put ADVICE_TOKEN
npx wrangler deploy
```

Laptop copy: `agent/.dev.vars` (0600, gitignored). Source it; do not paste tokens
into chat.

## Desk

```
set -a && source agent/.dev.vars && set +a
export AI_PROVIDER=computer
export ADVICE_URL=https://mt5-risk-agent.skyphusion.workers.dev/ask
```

`/ask` and free text POST `{session, question, context, model}`. Session is the
Telegram chat id. The agent does not send trades.
