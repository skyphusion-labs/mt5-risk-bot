# mt5-risk-agent

The agent is the Cloudflare Computer worker.
It is the desk's advice brain.
The desk is Telegram chat commands.
The bot is the Python process on this computer.
The gateway is Cloudflare AI Gateway `mt5-risk-bot`.

Working memory is the workspace filesystem (`/workspace/notes.md`, `log.md`, `snapshot.md`, `history.json`).
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

## Tests

```
npm ci
npm run typecheck
npm test
```

The suite runs in workerd via `@cloudflare/vitest-pool-workers`, not in node, so
the Worker, the `DeskAgent` Durable Object and its SQLite-backed workspace all
execute for real. CI runs it as the `agent-test` job.

The AI Gateway is the one hop the suite cannot call, so it is the one seam:
`vitest.config.ts` supplies an `outboundService` that answers the gateway and
returns 599 for anything else, which makes unexpected egress a failure rather
than a silent pass. Nothing under `agent/src/` is replaced or stubbed. The fake
gateway echoes back the headers and model it observed, which is how the outbound
contract is asserted; it never echoes the credential value.

Two properties of the runner are worth knowing before adding tests.

- A Durable Object receives its bindings from the runtime, not from the `env`
  object its caller holds. Overriding `CF_AIG_TOKEN` at the entry Worker does
  nothing to `DeskAgent`. Use the `withDeskEnv` helper for DESK-side variables.
- Storage is isolated per test FILE, not per test. `reset()` does not clear the
  namespace listing. A test that needs "no Durable Object exists at all" belongs
  in a file where nothing else authorizes a request; `test/auth-ordering.test.ts`
  is that file, and it says so at the top.

`agent/package.json` pins `overrides.miniflare` because the version the test pool
depends on ships a workerd older than this Worker's `compatibility_date`. Without
the override the runtime refuses to start. Raise the override, do not lower the
compatibility date: the suite has to run the runtime that ships.

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

`/ask` and free text POST `{session, question, context, history, model}`.
`history` is `journal.tail` (JSON list). The agent writes it to `/workspace/history.json`.
Session is the Telegram chat id.
The agent does not send trades.
