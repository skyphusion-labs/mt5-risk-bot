import { describe, expect, it } from "vitest";
import { GOOD_TOKEN, askRequest, call, liveDeskIds } from "./helpers";

function req(path: string, init: RequestInit = {}) {
  return new Request(`https://agent.test${path}`, init);
}

const AUTH = { authorization: `Bearer ${GOOD_TOKEN}` };

describe("routing: /health", () => {
  it("answers GET /health with 200 and no credential", async () => {
    const res = await call(req("/health"));
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/plain");
    expect(await res.text()).toBe("ok");
  });

  it("still answers 200 when a wrong credential is presented", async () => {
    const res = await call(req("/health", { headers: { authorization: "Bearer nope" } }));
    expect(res.status).toBe(200);
  });

  it("answers 200 even when ADVICE_TOKEN is unset", async () => {
    const res = await call(req("/health"), { ADVICE_TOKEN: "" });
    expect(res.status).toBe(200);
  });

  it("touches no Durable Object", async () => {
    await call(req("/health"));
    expect(await liveDeskIds()).toEqual([]);
  });

  it.each(["POST", "PUT", "DELETE", "HEAD"])(
    "does not serve health for %s, it falls through to 404",
    async (method) => {
      const res = await call(
        req("/health", { method, body: method === "HEAD" ? undefined : "{}" }),
      );
      expect(res.status).toBe(404);
    },
  );
});

describe("routing: everything that is not /ask is 404", () => {
  it.each([
    ["root", "/"],
    ["unknown path", "/nope"],
    ["trailing slash on ask", "/ask/"],
    ["different case", "/ASK"],
    ["ask as a prefix", "/askew"],
    ["ask nested", "/v1/ask"],
    ["empty-ish", "//"],
  ])("returns 404 for %s", async (_label, path) => {
    const res = await call(req(path, { headers: AUTH }));
    expect(res.status).toBe(404);
    expect(await res.text()).toBe("not found");
  });

  it("returns 404 before the credential is checked, so 404 is not an auth oracle", async () => {
    const withAuth = await call(req("/nope", { headers: AUTH }));
    const withoutAuth = await call(req("/nope"));
    const withWrongAuth = await call(req("/nope", { headers: { authorization: "Bearer x" } }));
    expect([withAuth.status, withoutAuth.status, withWrongAuth.status]).toEqual([404, 404, 404]);
  });
});

describe("routing: /ask is gated, and the gate comes before the method check", () => {
  it("answers 401, not 405, for an unauthenticated GET /ask", async () => {
    const res = await call(req("/ask"));
    expect(res.status).toBe(401);
  });

  it.each(["GET", "PUT", "DELETE", "PATCH"])(
    "answers 405 for an authenticated %s /ask",
    async (method) => {
      const res = await call(
        req("/ask", { method, headers: AUTH, body: method === "GET" ? undefined : "{}" }),
      );
      expect(res.status).toBe(405);
      expect(await res.json()).toEqual({ error: "POST only" });
    },
  );

  it("gates /ask with a query string the same way", async () => {
    const unauth = await call(req("/ask?x=1", { method: "POST", body: "{}" }));
    expect(unauth.status).toBe(401);
    const auth = await call(
      new Request("https://agent.test/ask?x=1", {
        method: "POST",
        headers: { ...AUTH, "content-type": "application/json" },
        body: JSON.stringify({ question: "q" }),
      }),
    );
    expect(auth.status).toBe(200);
  });

  it("serves an authorized POST /ask", async () => {
    const res = await call(askRequest({ question: "q" }, { token: `Bearer ${GOOD_TOKEN}` }));
    expect(res.status).toBe(200);
  });
});
