import { DeskAgent } from "./desk-agent";
import type { Env } from "./env";
import { checkSession } from "./session";

export { DeskAgent };

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return new Response("ok", { headers: { "content-type": "text/plain" } });
    }
    if (url.pathname !== "/ask") {
      return new Response("not found", { status: 404 });
    }
    const got = bearer(request);
    if (!env.ADVICE_TOKEN || !timingSafeEq(got, env.ADVICE_TOKEN)) {
      return new Response(JSON.stringify({ error: "unauthorized" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      });
    }
    // The session key names the Durable Object, so everything below runs BEFORE
    // the namespace is addressed. An addressed Durable Object is one the caller
    // has made the platform create, so a request that will not be served must
    // not reach that far. The method check and the refusals the Durable Object
    // used to answer therefore live here now; the status codes and bodies are
    // unchanged.
    if (request.method !== "POST") {
      return json({ error: "POST only" }, 405);
    }
    let parsed: unknown;
    try {
      parsed = await request.clone().json();
    } catch {
      return json({ error: "invalid json" }, 400);
    }
    const supplied =
      typeof parsed === "object" && parsed !== null
        ? (parsed as { session?: unknown }).session
        : undefined;
    const checked = checkSession(supplied, env.ADVICE_SESSIONS);
    if (!checked.ok) {
      return json({ error: "invalid session" }, 400);
    }
    const id = env.DESK.idFromName(checked.session);
    return env.DESK.get(id).fetch(request);
  },
};

function bearer(request: Request): string {
  const h = request.headers.get("authorization") || "";
  const m = /^Bearer\s+(.+)$/i.exec(h);
  return m ? m[1].trim() : "";
}

function timingSafeEq(a: string, b: string): boolean {
  const enc = new TextEncoder();
  const aa = enc.encode(a);
  const bb = enc.encode(b);
  if (aa.byteLength !== bb.byteLength) {
    return false;
  }
  return crypto.subtle.timingSafeEqual(aa, bb);
}

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
