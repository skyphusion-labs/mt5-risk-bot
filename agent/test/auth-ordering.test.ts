import { describe, expect, it } from "vitest";
import { GOOD_TOKEN, askRequest, call, liveDeskIds } from "./helpers";

/**
 * The bearer check must run BEFORE the Worker addresses a Durable Object,
 * because a Durable Object that has been addressed is a Durable Object an
 * unauthenticated caller has made the platform create.
 *
 * Durable Object storage is isolated per test file, so this file exists on its
 * own: nothing else in it ever authorizes a request, which is what makes the
 * absolute assertion "the namespace is empty" meaningful. Put an authorized ask
 * in this file and these tests stop being able to fail.
 */

async function expectEmptyNamespace() {
  expect(await liveDeskIds()).toEqual([]);
}

describe("auth runs before any Durable Object work", () => {
  it("starts from an empty namespace, so the assertions below can fail", async () => {
    await expectEmptyNamespace();
  });

  it("creates no Durable Object on a request with a wrong bearer", async () => {
    await expectEmptyNamespace();
    const res = await call(
      askRequest({ session: "refused-wrong-token", question: "q" }, { token: "Bearer wrong" }),
    );
    expect(res.status).toBe(401);
    await expectEmptyNamespace();
  });

  it("creates no Durable Object on a request with no bearer", async () => {
    const res = await call(askRequest({ session: "refused-no-token", question: "q" }, { token: null }));
    expect(res.status).toBe(401);
    await expectEmptyNamespace();
  });

  it("creates no Durable Object when ADVICE_TOKEN is unset", async () => {
    const res = await call(
      askRequest({ session: "refused-unset", question: "q" }, { token: `Bearer ${GOOD_TOKEN}` }),
      { ADVICE_TOKEN: "" },
    );
    expect(res.status).toBe(401);
    await expectEmptyNamespace();
  });

  it("refuses before the request body is parsed at all", async () => {
    const res = await call(askRequest(null, { token: "Bearer wrong", rawBody: "{not json" }));
    expect(res.status).toBe(401);
    await expectEmptyNamespace();
  });

  it("creates no Durable Object for a non-POST refused request", async () => {
    const res = await call(
      new Request("https://agent.test/ask", { method: "GET", headers: { authorization: "Bearer wrong" } }),
    );
    expect(res.status).toBe(401);
    await expectEmptyNamespace();
  });
});
