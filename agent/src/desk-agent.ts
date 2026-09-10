import { createOpenAI } from "@ai-sdk/openai";
import { Workspace, type DurableObjectStorageLike } from "@cloudflare/computer";
import { createAITools } from "@cloudflare/computer/tools";
import { generateText, stepCountIs } from "ai";
import { DurableObject } from "cloudflare:workers";
import type { Env } from "./env";

export const SYSTEM = [
  "You are a trading desk analyst for one MetaTrader 5 account.",
  "Your working memory is the Computer workspace: notes.md, log.md, snapshot.md.",
  "Read those files. Update notes.md with durable facts (plans, levels, what the operator said).",
  "You give a view, not a guarantee. Never claim consistent profits.",
  "The risk engine sizes and can refuse; you do not send orders.",
  "Prefer hold or close when daily_loss or drawdown room is thin.",
  "Always set sl and tp on buy/sell. For a working order set limit or stop, not both.",
  "For close set ticket. End every reply with a single JSON object on its own, no markdown fence:",
  '{"action":"buy"|"sell"|"close"|"hold","symbol":"EURUSD"|null,"sl":number|null,"tp":number|null,"limit":number|null,"stop":number|null,"ticket":number|null,"summary":"one line"}',
].join(" ");

type AskBody = {
  session?: string;
  question?: string;
  context?: string;
  model?: string;
};

export class DeskAgent extends DurableObject<Env> {
  readonly workspace: Workspace;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.workspace = new Workspace({
      storage: ctx.storage as DurableObjectStorageLike,
    });
  }

  async fetch(request: Request): Promise<Response> {
    if (request.method !== "POST") {
      return json({ error: "POST only" }, 405);
    }
    let body: AskBody;
    try {
      body = (await request.json()) as AskBody;
    } catch {
      return json({ error: "invalid json" }, 400);
    }
    const question = String(body.question || "").trim();
    const context = String(body.context || "");
    if (!question) {
      return json({ error: "question required" }, 400);
    }
    try {
      const text = await this.ask(question, context, String(body.model || ""));
      return json({ text });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "ask failed";
      return json({ error: msg }, 502);
    }
  }

  async ask(question: string, context: string, modelId: string): Promise<string> {
    const token = this.env.CF_AIG_TOKEN;
    const account = this.env.CF_ACCOUNT_ID;
    const gateway = this.env.AI_GATEWAY_ID;
    if (!token || !account || !gateway) {
      throw new Error("AI Gateway is not configured (CF_AIG_TOKEN, CF_ACCOUNT_ID, AI_GATEWAY_ID)");
    }
    await this.workspace.fs.mkdir("/workspace", { recursive: true });
    await this.workspace.fs.writeFile("/workspace/snapshot.md", context || "(no snapshot)");
    const prev = await readUtf8(this.workspace, "/workspace/log.md");
    const stamp = new Date().toISOString();
    await this.workspace.fs.writeFile(
      "/workspace/log.md",
      `${prev}## ${stamp} user\n\n${question}\n\n`,
    );

    const openai = createOpenAI({
      apiKey: token,
      baseURL: `https://gateway.ai.cloudflare.com/v1/${account}/${gateway}/compat`,
      headers: {
        "cf-aig-authorization": `Bearer ${token}`,
        "cf-aig-metadata": JSON.stringify({
          bot: "mt5-risk-bot",
          surface: "computer",
        }),
      },
    });
    const model = openai.chat(modelId || this.env.ADVICE_MODEL || "xai/grok-4");
    const tools = createAITools({
      workspace: this.workspace,
      read: { maxBytes: 32 * 1024, maxLines: 800 },
    });
    const result = await generateText({
      model,
      system: SYSTEM,
      prompt: [
        "Desk snapshot is /workspace/snapshot.md.",
        "Durable notes are /workspace/notes.md. Read and update them.",
        "Conversation log is /workspace/log.md.",
        `Operator: ${question}`,
      ].join("\n"),
      tools,
      stopWhen: stepCountIs(8),
    });
    const text = result.text || "";
    const after = await readUtf8(this.workspace, "/workspace/log.md");
    await this.workspace.fs.writeFile(
      "/workspace/log.md",
      `${after}## ${stamp} assistant\n\n${text}\n\n`,
    );
    return text;
  }
}

async function readUtf8(ws: Workspace, path: string): Promise<string> {
  try {
    const v = await ws.fs.readFile(path, "utf8");
    return typeof v === "string" ? v : "";
  } catch {
    return "";
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
