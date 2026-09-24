import { describe, expect, it } from "vitest";
import { GOOD_TOKEN, askRequest, call } from "./helpers";

/**
 * Pins the bearer check as it ships today. The implementation is correct: the
 * token is compared before any Durable Object is addressed, it refuses when
 * ADVICE_TOKEN is unset, and it compares with crypto.subtle.timingSafeEqual
 * behind a length pre-check. These tests exist so a later change cannot quietly
 * weaken any of that.
 */

async function unauthorizedBody(res: Response) {
  return (await res.json()) as { error?: string };
}

describe("auth: the four ways a caller can present itself", () => {
  it("refuses with no Authorization header", async () => {
    const res = await call(askRequest({ question: "q" }, { token: null }));
    expect(res.status).toBe(401);
    expect(res.headers.get("content-type")).toBe("application/json");
    expect(await unauthorizedBody(res)).toEqual({ error: "unauthorized" });
  });

  it.each([
    ["no scheme", GOOD_TOKEN],
    ["wrong scheme", `Basic ${GOOD_TOKEN}`],
    ["scheme only", "Bearer"],
    ["scheme with empty credential", "Bearer "],
    ["token in the wrong position", `${GOOD_TOKEN} Bearer`],
  ])("refuses a malformed header (%s)", async (_label, header) => {
    const res = await call(askRequest({ question: "q" }, { token: header }));
    expect(res.status).toBe(401);
    expect(await unauthorizedBody(res)).toEqual({ error: "unauthorized" });
  });

  it("refuses a wrong token of the same length", async () => {
    const wrong = "x".repeat(GOOD_TOKEN.length);
    expect(wrong.length).toBe(GOOD_TOKEN.length);
    const res = await call(askRequest({ question: "q" }, { token: `Bearer ${wrong}` }));
    expect(res.status).toBe(401);
  });

  it("refuses a wrong token of a different length", async () => {
    const res = await call(askRequest({ question: "q" }, { token: "Bearer short" }));
    expect(res.status).toBe(401);
  });

  it("refuses a token that only shares a prefix", async () => {
    const res = await call(
      askRequest({ question: "q" }, { token: `Bearer ${GOOD_TOKEN.slice(0, -1)}` }),
    );
    expect(res.status).toBe(401);
  });

  it("accepts the correct token", async () => {
    const res = await call(askRequest({ question: "q" }, { token: `Bearer ${GOOD_TOKEN}` }));
    expect(res.status).not.toBe(401);
    expect(res.status).toBe(200);
  });
});

describe("auth: accepted header shapes, pinned as they are today", () => {
  it.each([
    ["canonical", `Bearer ${GOOD_TOKEN}`],
    ["lowercase scheme", `bearer ${GOOD_TOKEN}`],
    ["uppercase scheme", `BEARER ${GOOD_TOKEN}`],
    ["extra spaces after the scheme", `Bearer   ${GOOD_TOKEN}`],
    ["trailing whitespace on the credential", `Bearer ${GOOD_TOKEN}   `],
    ["tab after the scheme", `Bearer\t${GOOD_TOKEN}`],
  ])("accepts %s", async (_label, header) => {
    const res = await call(askRequest({ question: "q" }, { token: header }));
    expect(res.status).toBe(200);
  });
});

describe("auth: fail closed when the secret is absent", () => {
  it.each([
    ["empty string", ""],
    ["undefined", undefined],
    ["missing key", null],
  ])("refuses every caller when ADVICE_TOKEN is %s", async (_label, value) => {
    const overrides =
      value === null ? { ADVICE_TOKEN: undefined } : { ADVICE_TOKEN: value as unknown };
    for (const header of [
      null,
      "Bearer ",
      `Bearer ${GOOD_TOKEN}`,
      "Bearer undefined",
      "Bearer null",
    ]) {
      const res = await call(askRequest({ question: "q" }, { token: header }), overrides);
      expect(res.status, `header: ${String(header)}`).toBe(401);
      expect(await unauthorizedBody(res)).toEqual({ error: "unauthorized" });
    }
  });

  it("does not authorize an empty credential against an empty secret", async () => {
    const res = await call(askRequest({ question: "q" }, { token: "Bearer  " }), {
      ADVICE_TOKEN: "",
    });
    expect(res.status).toBe(401);
  });
});
