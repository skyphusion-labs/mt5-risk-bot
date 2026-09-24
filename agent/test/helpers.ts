import { env, listDurableObjectIds, runInDurableObject } from "cloudflare:test";
import type { DeskAgent } from "../src/desk-agent";
import type { Env } from "../src/env";
import worker from "../src/index";

/** Matches the binding in vitest.config.ts. */
export const GOOD_TOKEN = "test-advice-token";

/** What the fake gateway in vitest.config.ts always puts in the completion. */
export const FAKE_MARKER = "FAKE_DESK_REPLY";

export type EnvOverrides = Partial<Record<keyof Env, unknown>>;

/**
 * Overrides apply to the ENTRY Worker only. A Durable Object receives its
 * bindings from the runtime, not from the env object its caller was handed, so
 * DESK-side variables (CF_AIG_TOKEN, CF_ACCOUNT_ID, AI_GATEWAY_ID) cannot be
 * changed this way. test/ask.test.ts drives those through the Durable Object
 * directly, and test/ask.test.ts pins this propagation boundary so the next
 * author does not lose an afternoon to it.
 */
function testEnv(overrides: EnvOverrides): Env {
  return { ...(env as unknown as Env), ...overrides } as Env;
}

/** Drive the shipped Worker. Nothing in src/ is replaced. */
export function call(request: Request, overrides: EnvOverrides = {}): Promise<Response> {
  return worker.fetch(request, testEnv(overrides));
}

export function askRequest(
  body: unknown,
  init: { token?: string | null; method?: string; rawBody?: string } = {},
): Request {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (init.token !== null && init.token !== undefined) {
    headers.authorization = init.token;
  }
  const method = init.method ?? "POST";
  const payload = init.rawBody ?? JSON.stringify(body);
  return new Request("https://agent.test/ask", {
    method,
    headers,
    body: method === "GET" || method === "HEAD" ? undefined : payload,
  });
}

/** A well-formed, authorized ask. */
export function goodAsk(body: Record<string, unknown>): Request {
  return askRequest(body, { token: `Bearer ${GOOD_TOKEN}` });
}

function deskStub(session: string): DurableObjectStub<DeskAgent> {
  return env.DESK.get(env.DESK.idFromName(session)) as unknown as DurableObjectStub<DeskAgent>;
}

/** Read any file in one Durable Object's workspace, without going through the Worker. */
export async function fileFor(session: string, path: string): Promise<string> {
  return runInDurableObject(deskStub(session), async (instance) => {
    try {
      const v = await instance.workspace.fs.readFile(path, "utf8");
      return typeof v === "string" ? v : "";
    } catch {
      return "";
    }
  });
}

export function logFor(session: string): Promise<string> {
  return fileFor(session, "/workspace/log.md");
}

/**
 * Run something against a Durable Object with patched bindings. This is the
 * only way to exercise DESK-side configuration, because the runtime, not the
 * caller, supplies a Durable Object's env.
 */
export async function withDeskEnv<T>(
  session: string,
  overrides: EnvOverrides,
  fn: (instance: DeskAgent) => Promise<T>,
): Promise<T> {
  return runInDurableObject(deskStub(session), async (instance) => {
    const mutable = instance as unknown as { env: Env };
    const original = mutable.env;
    mutable.env = { ...original, ...overrides } as Env;
    try {
      return await fn(instance);
    } finally {
      mutable.env = original;
    }
  });
}

/** Ids of every Durable Object that currently holds stored state. */
export async function liveDeskIds(): Promise<string[]> {
  const ids = await listDurableObjectIds(env.DESK);
  return ids.map((id) => id.toString()).sort();
}

export function idOf(session: string): string {
  return env.DESK.idFromName(session).toString();
}

/**
 * Durable Object storage is isolated per test FILE, not per test (measured
 * against @cloudflare/vitest-pool-workers 0.22.0; the `isolatedStorage` pool
 * option is gone and `reset()` does not clear the namespace listing). So inside
 * a file that creates Durable Objects, take a baseline, act, then assert that
 * nothing outside the named sessions came into existence.
 *
 * A test that needs the absolute assertion "no Durable Object exists at all"
 * belongs in a file of its own. test/auth-ordering.test.ts is that file.
 */
export async function unexpectedDesks(baseline: string[], allowed: string[]): Promise<string[]> {
  const allowedIds = new Set(allowed.map(idOf));
  const now = await liveDeskIds();
  return now.filter((id) => !baseline.includes(id) && !allowedIds.has(id));
}
