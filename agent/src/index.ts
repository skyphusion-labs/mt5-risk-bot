import { DeskAgent } from "./desk-agent";
import type { Env } from "./env";

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
    let session = "default";
    if (request.method === "POST") {
      const copy = request.clone();
      try {
        const body = (await copy.json()) as { session?: string };
        if (body.session) session = String(body.session);
      } catch {
        session = "default";
      }
    }
    const id = env.DESK.idFromName(session);
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
