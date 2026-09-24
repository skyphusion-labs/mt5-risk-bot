import { describe, expect, it } from "vitest";
import {
  FAKE_MARKER,
  askRequest,
  call,
  fileFor,
  goodAsk,
  logFor,
  withDeskEnv,
} from "./helpers";

/**
 * The Durable Object's own surface: request validation, the success shape, the
 * refusal when the gateway is not configured, and what it writes into its
 * workspace. The only thing replaced is the network (see vitest.config.ts); the
 * Durable Object, its SQLite storage and the model client all execute for real.
 */

const AUTH = "Bearer test-advice-token";

function echoed(text: string) {
  return JSON.parse(text) as {
    marker: string;
    model: string;
    toolCount: number;
    gatewayId: string | null;
    collectLogPayload: string | null;
    metadata: string | null;
    authorization: string;
  };
}

describe("ask: request validation", () => {
  it.each([
    ["no question field", {}],
    ["an empty question", { question: "" }],
    ["a whitespace-only question", { question: "   \n\t " }],
    ["a null question", { question: null }],
  ])("returns 400 for %s", async (_label, body) => {
    const res = await call(goodAsk(body as Record<string, unknown>));
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "question required" });
  });

  it.each([
    ["malformed json", "{not json"],
    ["an empty body", ""],
    ["a bare string", '"just a string"'],
  ])("returns 400 for %s", async (_label, raw) => {
    const res = await call(askRequest(null, { token: AUTH, rawBody: raw }));
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string };
    expect(["invalid json", "question required"]).toContain(body.error);
  });

  it("returns json, not prose, on every refusal", async () => {
    const res = await call(goodAsk({}));
    expect(res.headers.get("content-type")).toBe("application/json");
  });
});

describe("ask: the success path", () => {
  it("returns 200 with a text string", async () => {
    const res = await call(goodAsk({ session: "ok", question: "what is my risk room" }));
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/json");
    const body = (await res.json()) as { text: string };
    expect(typeof body.text).toBe("string");
    expect(body.text.length).toBeGreaterThan(0);
    expect(echoed(body.text).marker).toBe(FAKE_MARKER);
  });

  it("returns no error key on success", async () => {
    const res = await call(goodAsk({ session: "ok2", question: "q" }));
    const body = (await res.json()) as Record<string, unknown>;
    expect(Object.keys(body)).toEqual(["text"]);
  });
});

describe("ask: the outbound gateway contract", () => {
  it("sends the configured gateway id, model and privacy header", async () => {
    const res = await call(goodAsk({ session: "outbound", question: "q" }));
    const body = (await res.json()) as { text: string };
    const seen = echoed(body.text);
    expect(seen.gatewayId).toBe("mt5-risk-bot");
    expect(seen.model).toBe("xai/grok-4.6");
    expect(seen.collectLogPayload).toBe("false");
    expect(seen.authorization).toBe("present");
    expect(JSON.parse(seen.metadata ?? "{}")).toEqual({
      bot: "mt5-risk-bot",
      surface: "computer",
    });
  });

  it("lets the request body override the model", async () => {
    const res = await call(
      goodAsk({ session: "model-override", question: "q", model: "openai/gpt-5" }),
    );
    const body = (await res.json()) as { text: string };
    expect(echoed(body.text).model).toBe("openai/gpt-5");
  });

  it("exposes the workspace tools to the model", async () => {
    const res = await call(goodAsk({ session: "tools", question: "q" }));
    const body = (await res.json()) as { text: string };
    expect(echoed(body.text).toolCount).toBeGreaterThan(0);
  });
});

describe("ask: it refuses rather than guessing when the gateway is unconfigured", () => {
  /**
   * These drive the Durable Object directly. A Durable Object receives its
   * bindings from the runtime, not from the env object its caller holds, so
   * overriding a DESK-side variable at the entry Worker has no effect on it.
   * The test below this block pins that boundary.
   */
  function askRequestFor(question: string) {
    return new Request("https://agent.test/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    });
  }

  it.each([
    ["CF_AIG_TOKEN", { CF_AIG_TOKEN: "" }],
    ["CF_ACCOUNT_ID", { CF_ACCOUNT_ID: "" }],
    ["AI_GATEWAY_ID", { AI_GATEWAY_ID: "" }],
    ["CF_AIG_TOKEN as undefined", { CF_AIG_TOKEN: undefined }],
    ["every gateway variable", { CF_AIG_TOKEN: "", CF_ACCOUNT_ID: "", AI_GATEWAY_ID: "" }],
  ])("returns 502 when %s is missing", async (_label, overrides) => {
    const res = await withDeskEnv("unconfigured", overrides, (instance) =>
      instance.fetch(askRequestFor("q")),
    );
    expect(res.status).toBe(502);
    expect(res.headers.get("content-type")).toBe("application/json");
    const body = (await res.json()) as { error: string };
    expect(typeof body.error).toBe("string");
    expect(body.error.length).toBeGreaterThan(0);
  });

  it("writes nothing to the workspace when it refuses", async () => {
    const res = await withDeskEnv("unconfigured-clean", { CF_AIG_TOKEN: "" }, (instance) =>
      instance.fetch(askRequestFor("NEVER_LOGGED")),
    );
    expect(res.status).toBe(502);
    expect(await logFor("unconfigured-clean")).toBe("");
    expect(await fileFor("unconfigured-clean", "/workspace/snapshot.md")).toBe("");
  });

  it("validates the request before it looks at the gateway configuration", async () => {
    const res = await withDeskEnv("unconfigured-validate", { CF_AIG_TOKEN: "" }, (instance) =>
      instance.fetch(
        new Request("https://agent.test/ask", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({}),
        }),
      ),
    );
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "question required" });
  });
});

describe("ask: a Durable Object does not inherit its caller's env", () => {
  it("ignores a gateway override applied at the entry Worker", async () => {
    const res = await call(goodAsk({ session: "propagation", question: "q" }), {
      CF_AIG_TOKEN: "",
      CF_ACCOUNT_ID: "",
      AI_GATEWAY_ID: "",
    });
    expect(res.status).toBe(200);
  });

  it("honours an ADVICE_TOKEN override, which the entry Worker reads itself", async () => {
    const res = await call(goodAsk({ session: "propagation", question: "q" }), {
      ADVICE_TOKEN: "a-different-token",
    });
    expect(res.status).toBe(401);
  });
});

describe("ask: what it writes into the workspace", () => {
  it("writes the supplied context to snapshot.md", async () => {
    await call(goodAsk({ session: "ws", question: "q", context: "EQUITY 10000 DD 2%" }));
    expect(await fileFor("ws", "/workspace/snapshot.md")).toBe("EQUITY 10000 DD 2%");
  });

  it("writes a placeholder when no context is supplied", async () => {
    await call(goodAsk({ session: "ws-empty", question: "q" }));
    expect(await fileFor("ws-empty", "/workspace/snapshot.md")).toBe("(no snapshot)");
  });

  it("writes the supplied history as json", async () => {
    const history = [{ role: "user", content: "earlier" }];
    await call(goodAsk({ session: "ws-hist", question: "q", history }));
    const raw = await fileFor("ws-hist", "/workspace/history.json");
    expect(JSON.parse(raw)).toEqual(history);
  });

  it("writes an empty array when history is not an array", async () => {
    await call(goodAsk({ session: "ws-bad-hist", question: "q", history: { not: "an array" } }));
    expect(JSON.parse(await fileFor("ws-bad-hist", "/workspace/history.json"))).toEqual([]);
  });

  it("records both sides of the exchange in log.md", async () => {
    await call(goodAsk({ session: "ws-log", question: "LOGGED_QUESTION" }));
    const log = await logFor("ws-log");
    expect(log).toContain("LOGGED_QUESTION");
    expect(log).toContain(FAKE_MARKER);
    expect(log).toContain("user");
    expect(log).toContain("assistant");
  });

  it("replaces the snapshot on each call rather than appending", async () => {
    await call(goodAsk({ session: "ws-replace", question: "q", context: "FIRST" }));
    await call(goodAsk({ session: "ws-replace", question: "q", context: "SECOND" }));
    const snapshot = await fileFor("ws-replace", "/workspace/snapshot.md");
    expect(snapshot).toBe("SECOND");
    expect(snapshot).not.toContain("FIRST");
  });
});
