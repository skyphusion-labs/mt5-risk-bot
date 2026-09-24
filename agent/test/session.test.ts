import { describe, expect, it } from "vitest";
import {
  askRequest,
  call,
  goodAsk,
  liveDeskIds,
  logFor,
  unexpectedDesks,
} from "./helpers";

/**
 * The `session` field in the request body flows straight into
 * DESK.idFromName(), so the caller picks which Durable Object serves it. These
 * tests PIN what that does today rather than judging it. Issue #22 will
 * constrain the value; these are the observations that change has to move, and
 * the ones it must not.
 *
 * The observation channel is the Durable Object's own workspace, not the
 * response body: a question only appears in the /workspace/log.md of the
 * instance that actually served it.
 */

async function ask(session: unknown, question: string) {
  const body: Record<string, unknown> = { question };
  if (session !== undefined) {
    body.session = session;
  }
  const res = await call(goodAsk(body));
  expect(res.status).toBe(200);
  return res;
}

describe("session: two callers, two workspaces", () => {
  it("keeps distinct sessions isolated", async () => {
    const baseline = await liveDeskIds();
    await ask("iso-alpha", "QUESTION_ALPHA");
    await ask("iso-beta", "QUESTION_BETA");

    const alpha = await logFor("iso-alpha");
    const beta = await logFor("iso-beta");

    expect(alpha).toContain("QUESTION_ALPHA");
    expect(alpha).not.toContain("QUESTION_BETA");
    expect(beta).toContain("QUESTION_BETA");
    expect(beta).not.toContain("QUESTION_ALPHA");

    expect(await unexpectedDesks(baseline, ["iso-alpha", "iso-beta"])).toEqual([]);
  });

  it("accumulates a conversation inside one session", async () => {
    const baseline = await liveDeskIds();
    await ask("accumulate", "FIRST_QUESTION");
    await ask("accumulate", "SECOND_QUESTION");
    const log = await logFor("accumulate");
    expect(log).toContain("FIRST_QUESTION");
    expect(log).toContain("SECOND_QUESTION");
    expect(log.indexOf("FIRST_QUESTION")).toBeLessThan(log.indexOf("SECOND_QUESTION"));
    expect(await unexpectedDesks(baseline, ["accumulate"])).toEqual([]);
  });
});

describe("session: values that fall back to the shared default instance", () => {
  it.each([
    ["absent", undefined, "FALLBACK_ABSENT"],
    ["empty string", "", "FALLBACK_EMPTY"],
    ["null", null, "FALLBACK_NULL"],
    ["false", false, "FALLBACK_FALSE"],
    ["the number zero", 0, "FALLBACK_ZERO"],
  ])("routes %s to the instance named default", async (_label, value, marker) => {
    const baseline = await liveDeskIds();
    await ask(value, marker);
    expect(await logFor("default")).toContain(marker);
    expect(await unexpectedDesks(baseline, ["default"])).toEqual([]);
  });
});

describe("session: values that collide onto one instance today", () => {
  it("routes the number 123 and the string 123 to the same instance", async () => {
    const baseline = await liveDeskIds();
    await ask(123, "FROM_NUMBER");
    await ask("123", "FROM_STRING");
    const log = await logFor("123");
    expect(log).toContain("FROM_NUMBER");
    expect(log).toContain("FROM_STRING");
    expect(await unexpectedDesks(baseline, ["123"])).toEqual([]);
  });

  it("routes every object to one shared instance", async () => {
    const baseline = await liveDeskIds();
    await ask({ desk: "a" }, "FROM_OBJECT_A");
    await ask({ desk: "b" }, "FROM_OBJECT_B");
    const log = await logFor("[object Object]");
    expect(log).toContain("FROM_OBJECT_A");
    expect(log).toContain("FROM_OBJECT_B");
    expect(await unexpectedDesks(baseline, ["[object Object]"])).toEqual([]);
  });

  it("routes a single-element array and its string form together", async () => {
    const baseline = await liveDeskIds();
    await ask(["shared-value"], "FROM_ARRAY");
    await ask("shared-value", "FROM_PLAIN_STRING");
    const log = await logFor("shared-value");
    expect(log).toContain("FROM_ARRAY");
    expect(log).toContain("FROM_PLAIN_STRING");
    expect(await unexpectedDesks(baseline, ["shared-value"])).toEqual([]);
  });

  it("routes the boolean true and the string true together", async () => {
    const baseline = await liveDeskIds();
    await ask(true, "FROM_BOOLEAN");
    await ask("true", "FROM_TRUE_STRING");
    const log = await logFor("true");
    expect(log).toContain("FROM_BOOLEAN");
    expect(log).toContain("FROM_TRUE_STRING");
    expect(await unexpectedDesks(baseline, ["true"])).toEqual([]);
  });
});

describe("session: the value is unconstrained today", () => {
  it.each([
    ["a long value", "s".repeat(2048)],
    ["a path-like value", "../../../etc/passwd"],
    ["a value with whitespace", "  spaced  out  "],
    ["a value with newlines", "line-one\nline-two"],
    ["a value with unicode", "risk-desk-éè-中文"],
    ["a value with a null escape", "a\u0000b"],
    ["a value that looks like two sessions", "unc-alpha\u0000unc-beta"],
  ])("accepts %s verbatim", async (_label, value) => {
    const baseline = await liveDeskIds();
    await ask(value, "UNCONSTRAINED_QUESTION");
    expect(await logFor(value)).toContain("UNCONSTRAINED_QUESTION");
    expect(await unexpectedDesks(baseline, [value])).toEqual([]);
  });

  it("gives a 2048 character session its own instance, distinct from a 2047 one", async () => {
    const long = `L${"s".repeat(2047)}`;
    const shorter = `L${"s".repeat(2046)}`;
    const baseline = await liveDeskIds();
    await ask(long, "LONG_QUESTION");
    await ask(shorter, "SHORTER_QUESTION");
    const longLog = await logFor(long);
    expect(longLog).toContain("LONG_QUESTION");
    expect(longLog).not.toContain("SHORTER_QUESTION");
    expect(await unexpectedDesks(baseline, [long, shorter])).toEqual([]);
  });
});

describe("session: it is only read from a POST body", () => {
  it("falls back to default when the body is not valid JSON", async () => {
    const baseline = await liveDeskIds();
    const res = await call(
      askRequest(null, { token: "Bearer test-advice-token", rawBody: "{not json" }),
    );
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid json" });
    expect(await unexpectedDesks(baseline, ["default"])).toEqual([]);
  });

  it("uses default for a non-POST method, which the Durable Object then refuses", async () => {
    const baseline = await liveDeskIds();
    const res = await call(
      new Request("https://agent.test/ask", {
        method: "GET",
        headers: { authorization: "Bearer test-advice-token" },
      }),
    );
    expect(res.status).toBe(405);
    expect(await unexpectedDesks(baseline, ["default"])).toEqual([]);
  });
});
