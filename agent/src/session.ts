/**
 * The session key names the Durable Object that serves a request, so the entry
 * Worker treats it as an input to validate, not a value to coerce. One explicit
 * shape, checked before the namespace is ever addressed.
 *
 * ADVICE_SESSIONS is optional and unset by default. When it IS set it is the
 * complete list of keys this deployment serves, and a list with no usable entry
 * serves nobody: an operator who pins the list gets a closed door, not a wider
 * one.
 */

/** Longest key the Worker will address a Durable Object for. */
export const SESSION_MAX_LENGTH = 64;

/** Letters, digits, dot, underscore, hyphen. No whitespace, no separators. */
const SESSION_SHAPE = /^[A-Za-z0-9._-]+$/;

export type SessionCheck = { ok: true; session: string } | { ok: false };

/**
 * The configured allowlist, or null when none is configured. Entries are
 * trimmed and empty entries are dropped, so a list that is present but holds
 * nothing usable returns an EMPTY array, which matches no key. Null and empty
 * are different answers on purpose.
 */
export function allowedSessions(raw: unknown): string[] | null {
  if (typeof raw !== "string" || raw.trim().length === 0) {
    return null;
  }
  return raw
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
}

export function checkSession(raw: unknown, allowlist: unknown): SessionCheck {
  if (typeof raw !== "string") {
    return { ok: false };
  }
  if (raw.length === 0 || raw.length > SESSION_MAX_LENGTH) {
    return { ok: false };
  }
  if (!SESSION_SHAPE.test(raw)) {
    return { ok: false };
  }
  const allowed = allowedSessions(allowlist);
  if (allowed !== null && !allowed.includes(raw)) {
    return { ok: false };
  }
  return { ok: true, session: raw };
}
