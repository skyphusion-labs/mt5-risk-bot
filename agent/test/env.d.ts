import type { Env as WorkerEnv } from "../src/env";

declare module "cloudflare:test" {
  interface ProvidedEnv extends WorkerEnv {}
}

declare global {
  namespace Cloudflare {
    interface Env extends WorkerEnv {}
  }
}
