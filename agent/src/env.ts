/** Hand-authored Env. Mirrors wrangler.jsonc bindings. Do not generate. */

export interface Env {
  DESK: DurableObjectNamespace;
  CF_ACCOUNT_ID: string;
  AI_GATEWAY_ID: string;
  ADVICE_MODEL: string;
  CF_AIG_TOKEN: string;
  ADVICE_TOKEN: string;
  /**
   * Optional comma-separated list of the session keys this deployment serves.
   * Not in wrangler.jsonc: like ADVICE_TOKEN and CF_AIG_TOKEN it is set on the
   * deployment, not in the repo. Unset means the shape rule in src/session.ts
   * is the only constraint.
   */
  ADVICE_SESSIONS?: string;
}
