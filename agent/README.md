# mt5-risk-agent

Cloudflare Computer worker. This is the advice brain for the bot.
Working memory is the workspace: `notes.md`, `log.md`, `snapshot.md`.
Inference is AI Gateway Unified Billing. The bot does not send trades
from advice.

| Item | Value |
| --- | --- |
| Worker | `https://mt5-risk-agent.skyphusion.workers.dev` |
| Health | `GET /health` |
| Ask | `POST /ask` |
| Gateway | `mt5-risk-bot` |
| Model | `xai/grok-4.6` |

Computer is a Cloudflare preview. The bot still runs next to MT5.

## Inference

Use the AI Gateway REST API.

```
POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions
Authorization: Bearer CF_AIG_TOKEN
cf-aig-gateway-id: mt5-risk-bot
cf-aig-collect-log-payload: false
{"model":"xai/grok-4.6","messages":[...]}
```

See https://developers.cloudflare.com/ai-gateway/usage/rest-api/

CAUTION: Do not put `CF_AIG_TOKEN` in `Authorization` on
`gateway.ai.cloudflare.com`. The gateway forwards that header to xAI
as a provider key.

## Secrets

Worker secrets: `CF_AIG_TOKEN`, `ADVICE_TOKEN`. Never commit them.

```
cd agent
npx wrangler secret put CF_AIG_TOKEN
npx wrangler secret put ADVICE_TOKEN
npx wrangler deploy
```

Laptop copy: `agent/.dev.vars` (0600, gitignored). Source it. Do not
paste tokens into chat.

## Desk

```
set -a && source agent/.dev.vars && set +a
export AI_PROVIDER=computer
export ADVICE_URL=https://mt5-risk-agent.skyphusion.workers.dev/ask
```

`/ask` posts `{session, question, context}`. Session is the Telegram
chat id.
