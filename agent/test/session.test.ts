import { describe, expect, it } from "vitest";
import { askRequest, call, goodAsk, liveDeskIds, logFor, unexpectedDesks } from "./helpers";

/**
 * `session` names the Durable Object that serves the request, so it is the key
 * that keeps one caller's workspace out of another's. Issue #22 constrained it:
 * the entry Worker now requires an explicit string of a fixed shape, and refuses
 * anything else BEFORE it addresses a Durable Object.
 *
 * These replace the observations PR #42 pinned on the pre-fix Worker. Where an
 * assertion was inverted, the test name says what used to happen, so the change
 * is readable in the diff rather than silent.
 *
 * The observation channel is the Durable Object's own workspace, not the
 * response body: a question only appears in the /workspace/log.md of the
 * instance that actually served it. A refusal is therefore proved two ways, by
 * the status and body, and by no Durable Object coming into existence.
 */

const REFUSED = { error: "invalid session" };
const AUTH = "Bearer test-advice-token";

function body(session: unknown, question: string): Record<string, unknown> {
  const out: Record<string, unknown> = { question };
  if (session !== undefined) {
    out.session = session;
  }
  return out;
}

/** A session the Worker accepts: 200, and the named instance did the work. */
async function served(session: string, question: string): Promise<void> {
  const res = await call(goodAsk(body(session, question)));
  expect(res.status).toBe(200);
}

/** A session the Worker refuses: 400, one fixed body, and no new instance. */
async function refuses(session: unknown, question: string): Promise<void> {
  const baseline = await liveDeskIds();
  const res = await call(goodAsk(body(session, question)));
  expect(res.status).toBe(400);
  expect(await res.json()).toEqual(REFUSED);
  expect(await unexpectedDesks(baseline, [])).toEqual([]);
}

describe("session: two callers, two workspaces", () => {
  it("keeps distinct sessions isolated", async () => {
    const baseline = await liveDeskIds();
    await served("iso-alpha", "QUESTION_ALPHA");
    await served("iso-beta", "QUESTION_BETA");

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
    await served("accumulate", "FIRST_QUESTION");
    await served("accumulate", "SECOND_QUESTION");
    const log = await logFor("accumulate");
    expect(log).toContain("FIRST_QUESTION");
    expect(log).toContain("SECOND_QUESTION");
    expect(log.indexOf("FIRST_QUESTION")).toBeLessThan(log.indexOf("SECOND_QUESTION"));
    expect(await unexpectedDesks(baseline, ["accumulate"])).toEqual([]);
  });
});

const NON_STRINGS: [string, unknown][] = [
  ["the number 123", 123],
  ["the number zero", 0],
  ["a float", 1.5],
  ["the boolean true", true],
  ["the boolean false", false],
  ["null", null],
  ["an object", { desk: "a" }],
  ["an empty object", {}],
  ["an array", ["shared-value"]],
  ["a nested array", [["x"]]],
];

describe("session: a non-string is refused, never coerced", () => {
  it.each(NON_STRINGS)("refuses %s", async (_label, value) => {
    await refuses(value, "COERCION_REFUSED");
  });

  it("no longer lands every object on one shared instance", async () => {
    const baseline = await liveDeskIds();
    await refuses({ desk: "a" }, "FROM_OBJECT_A");
    await refuses({ desk: "b" }, "FROM_OBJECT_B");
    expect(await unexpectedDesks(baseline, [])).toEqual([]);
    expect(await logFor("[object Object]")).toBe("");
  });

  it("no longer collides the number 123 with the string 123", async () => {
    await refuses(123, "FROM_NUMBER");
    await served("123", "FROM_STRING");
    const log = await logFor("123");
    expect(log).toContain("FROM_STRING");
    expect(log).not.toContain("FROM_NUMBER");
  });

  it("no longer collides a single element array with its string form", async () => {
    await refuses(["shared-value"], "FROM_ARRAY");
    await served("shared-value", "FROM_PLAIN_STRING");
    const log = await logFor("shared-value");
    expect(log).toContain("FROM_PLAIN_STRING");
    expect(log).not.toContain("FROM_ARRAY");
  });

  it("no longer collides the boolean true with the string true", async () => {
    await refuses(true, "FROM_BOOLEAN");
    await served("true", "FROM_TRUE_STRING");
    const log = await logFor("true");
    expect(log).toContain("FROM_TRUE_STRING");
    expect(log).not.toContain("FROM_BOOLEAN");
  });
});

describe("session: a falsy value is refused, not turned into the shared default desk", () => {
  const FALSY: [string, unknown][] = [
    ["an empty string", ""],
    ["null", null],
    ["false", false],
    ["the number zero", 0],
  ];

  it.each(FALSY)("refuses %s", async (_label, value) => {
    await refuses(value, "FALLBACK_REFUSED");
  });

  it("refuses an absent session instead of serving the shared default desk", async () => {
    const baseline = await liveDeskIds();
    const res = await call(goodAsk({ question: "FALLBACK_ABSENT" }));
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual(REFUSED);
    expect(await unexpectedDesks(baseline, [])).toEqual([]);
  });

  it("never writes a refused question into the default desk", async () => {
    await refuses("", "NEVER_IN_DEFAULT");
    const log = await logFor("default");
    expect(log).not.toContain("NEVER_IN_DEFAULT");
    expect(log).not.toContain("FALLBACK_REFUSED");
    expect(log).not.toContain("FALLBACK_ABSENT");
  });

  it("still serves the literal string default, which the shipped caller can send", async () => {
    await served("default", "EXPLICIT_DEFAULT");
    expect(await logFor("default")).toContain("EXPLICIT_DEFAULT");
  });
});

describe("session: the accepted shape", () => {
  it.each([
    ["a telegram chat id", "42"],
    ["a negative telegram chat id", "-1001234567890"],
    ["the literal default", "default"],
    ["dots, underscores and hyphens", "desk.one_two-3"],
    ["a single character", "a"],
    ["64 characters", "s".repeat(64)],
  ])("serves %s", async (_label, value) => {
    await served(value, "SHAPE_ACCEPTED");
    expect(await logFor(value)).toContain("SHAPE_ACCEPTED");
  });

  it.each([
    ["65 characters", "s".repeat(65)],
    ["2048 characters", "s".repeat(2048)],
    ["a path-like value", "../../../etc/passwd"],
    ["leading and trailing whitespace", "  spaced  out  "],
    ["an inner space", "two words"],
    ["a newline", "line-one\nline-two"],
    ["a tab", "line-one\tline-two"],
    ["unicode", "risk-desk-éè"],
    ["a null escape", "a\u0000b"],
    ["a value that looks like two sessions", "unc-alpha\u0000unc-beta"],
    ["a colon", "desk:one"],
    ["a slash", "desk/one"],
    ["a percent escape", "desk%2Fone"],
  ])("refuses %s", async (_label, value) => {
    await refuses(value, "SHAPE_REFUSED");
  });

  it("gives a 64 character session its own instance, distinct from a 63 one", async () => {
    const long = "L" + "s".repeat(63);
    const shorter = "L" + "s".repeat(62);
    const baseline = await liveDeskIds();
    await served(long, "LONG_QUESTION");
    await served(shorter, "SHORTER_QUESTION");
    const longLog = await logFor(long);
    expect(longLog).toContain("LONG_QUESTION");
    expect(longLog).not.toContain("SHORTER_QUESTION");
    expect(await unexpectedDesks(baseline, [long, shorter])).toEqual([]);
  });
});

/**
 * The namespace listing only reports Durable Objects that hold STORED state, so
 * it cannot tell "never addressed" from "addressed and wrote nothing". Measured:
 * the pre-fix Worker forwarded a non-POST and a malformed body to the `default`
 * instance, the instance refused before writing, and an assertion on the listing
 * passed anyway. That is a green that cannot go red.
 *
 * So these tests hand the entry Worker a DESK binding that records every
 * idFromName it receives and refuses to hand back a stub. It is not a stand-in
 * for the Durable Object (every other test in this file drives the real one); it
 * is the instrument for one question the listing cannot answer, which is whether
 * the Worker addressed the namespace at all. The last test in the block is the
 * positive control: it shows the instrument firing.
 */
function spyDesk(): { names: string[]; binding: unknown } {
  const names: string[] = [];
  return {
    names,
    binding: {
      idFromName(name: string) {
        names.push(name);
        return name;
      },
      get() {
        throw new Error("DESK.get reached");
      },
    },
  };
}

describe("session: the namespace is not addressed until the session is valid", () => {
  it("does not address the namespace for a non-POST", async () => {
    const spy = spyDesk();
    const res = await call(
      new Request("https://agent.test/ask", { method: "GET", headers: { authorization: AUTH } }),
      { DESK: spy.binding },
    );
    expect(res.status).toBe(405);
    expect(await res.json()).toEqual({ error: "POST only" });
    expect(spy.names).toEqual([]);
  });

  it("does not address the namespace for a body that is not valid json", async () => {
    const spy = spyDesk();
    const res = await call(askRequest(null, { token: AUTH, rawBody: "{not json" }), {
      DESK: spy.binding,
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid json" });
    expect(spy.names).toEqual([]);
  });

  it("does not address the namespace for a body that is not a json object", async () => {
    const spy = spyDesk();
    const res = await call(askRequest(null, { token: AUTH, rawBody: '"just a string"' }), {
      DESK: spy.binding,
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual(REFUSED);
    expect(spy.names).toEqual([]);
  });

  it("does not address the namespace for a refused session", async () => {
    const spy = spyDesk();
    const res = await call(goodAsk({ session: { desk: "a" }, question: "q" }), {
      DESK: spy.binding,
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual(REFUSED);
    expect(spy.names).toEqual([]);
  });

  it("addresses the namespace with the accepted session, and only that, so the four above can fail", async () => {
    const spy = spyDesk();
    await expect(call(goodAsk({ session: "spy-control", question: "q" }), { DESK: spy.binding })).rejects.toThrow(
      "DESK.get reached",
    );
    expect(spy.names).toEqual(["spy-control"]);
  });
});

describe("session: an operator can pin the accepted keys with ADVICE_SESSIONS", () => {
  it("serves a listed key", async () => {
    const res = await call(goodAsk({ session: "alpha", question: "LISTED" }), {
      ADVICE_SESSIONS: "alpha,beta",
    });
    expect(res.status).toBe(200);
    expect(await logFor("alpha")).toContain("LISTED");
  });

  it("refuses an unlisted key that would otherwise pass the shape rule", async () => {
    const baseline = await liveDeskIds();
    const res = await call(goodAsk({ session: "gamma", question: "UNLISTED" }), {
      ADVICE_SESSIONS: "alpha,beta",
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual(REFUSED);
    expect(await unexpectedDesks(baseline, [])).toEqual([]);
  });

  it("ignores whitespace and empty entries around the list", async () => {
    const res = await call(goodAsk({ session: "beta", question: "LISTED_SPACED" }), {
      ADVICE_SESSIONS: " alpha , beta , ",
    });
    expect(res.status).toBe(200);
    expect(await logFor("beta")).toContain("LISTED_SPACED");
  });

  it("fails closed when the list is set but holds no usable entry", async () => {
    const baseline = await liveDeskIds();
    const res = await call(goodAsk({ session: "alpha", question: "EMPTY_LIST" }), {
      ADVICE_SESSIONS: " , , ",
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual(REFUSED);
    expect(await unexpectedDesks(baseline, [])).toEqual([]);
  });

  it("refuses a listed key that does not pass the shape rule", async () => {
    const baseline = await liveDeskIds();
    const res = await call(goodAsk({ session: "two words", question: "BAD_SHAPE_LISTED" }), {
      ADVICE_SESSIONS: "two words",
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual(REFUSED);
    expect(await unexpectedDesks(baseline, [])).toEqual([]);
  });

  it("applies the shape rule alone when the list is unset", async () => {
    const ok = await call(goodAsk({ session: "shape-only", question: "NO_LIST" }), {
      ADVICE_SESSIONS: "",
    });
    expect(ok.status).toBe(200);
    const bad = await call(goodAsk({ session: "two words", question: "NO_LIST_BAD" }), {
      ADVICE_SESSIONS: "",
    });
    expect(bad.status).toBe(400);
    expect(await bad.json()).toEqual(REFUSED);
  });
});
