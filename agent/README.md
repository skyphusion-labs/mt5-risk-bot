# mt5-risk-agent

The agent is the Cloudflare Computer worker.
It is the desk's advice brain.
The desk is Telegram chat commands.
The bot is the Python process on this computer.
The gateway is Cloudflare AI Gateway `mt5-risk-bot`.

Working memory is the workspace filesystem (`/workspace/notes.md`, `log.md`, `snapshot.md`).
Computer tools are `read`, `write`, `edit`, `ls`, and `grep`.
Inference is Unified Billing on the gateway.
It is not provider BYOK.

Live:

- Worker: `https://mt5-risk-agent.skyphusion.workers.dev`
- Health: `GET /health` -> `ok`
- Ask: `POST /ask` with `Authorization: Bearer ADVICE_TOKEN`
- The gateway: `mt5-risk-bot` (account `fabcb25d9c7eb087110ec474a03e50d2`)
- Model: `xai/grok-4.6`

The agent is an early Computer preview.
The bot still runs next to MT5.

## Inference path (docs)

New calls use the gateway REST API, not `/compat`:

```
POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions
Authorization: Bearer CF_AIG_TOKEN
cf-aig-gateway-id: mt5-risk-bot
cf-aig-collect-log-payload: false
{"model":"xai/grok-4.6","messages":[...]}
```

See [REST API](https://developers.cloudflare.com/ai-gateway/usage/rest-api/) and
[Unified Billing](https://developers.cloudflare.com/ai-gateway/features/unified-billing/).

WARNING
Do not put `CF_AIG_TOKEN` on `gateway.ai.cloudflare.com` as `Authorization`.
The gateway forwards that header to xAI as a provider key.
Unified Billing is skipped.

## Secrets (never in git)

Agent secrets: `CF_AIG_TOKEN`, `ADVICE_TOKEN`.

1. Change to the agent directory.
   `cd agent`
2. Put the gateway token.
   `npx wrangler secret put CF_AIG_TOKEN`
3. Put the desk token.
   `npx wrangler secret put ADVICE_TOKEN`
4. Deploy.
   `npx wrangler deploy`

Laptop copy: `agent/.dev.vars` (0600, gitignored).
Source it.
Do not paste tokens into chat.

## Desk

1. Load the laptop token file.
   `set -a`
2. Source it.
   `source agent/.dev.vars`
3. Stop exporting.
   `set +a`
4. Point the bot at the agent.
   `export AI_PROVIDER=computer`
   `export ADVICE_URL=https://mt5-risk-agent.skyphusion.workers.dev/ask`

`/ask` and free text POST `{session, question, context, model}`.
Session is the Telegram chat id.
The agent does not send trades.
