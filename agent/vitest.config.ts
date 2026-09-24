import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

/**
 * The Worker runs in workerd, not node, so the suite uses the Workers-native
 * pool. The AI Gateway is the only hop the suite cannot execute for real, so it
 * is the ONE seam: `outboundService` replaces the network for every outbound
 * request the Worker (and its Durable Object) makes. Nothing in src/ is stubbed.
 *
 * The fake gateway echoes the request it observed back as the completion body,
 * which is how test/ask.test.ts asserts on the outbound contract without a
 * second channel. It never echoes the credential value, only whether one was
 * present. Any request that is not the gateway gets 599 so unexpected egress is
 * loud rather than silent.
 */
function fakeGateway(
  model: string,
  headers: { get(name: string): string | null },
  toolCount: number,
): string {
  return JSON.stringify({
    marker: "FAKE_DESK_REPLY",
    model,
    toolCount,
    gatewayId: headers.get("cf-aig-gateway-id"),
    collectLogPayload: headers.get("cf-aig-collect-log-payload"),
    metadata: headers.get("cf-aig-metadata"),
    authorization: headers.get("authorization") ? "present" : "absent",
  });
}

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.jsonc" },
      miniflare: {
        bindings: {
          ADVICE_TOKEN: "test-advice-token",
          CF_AIG_TOKEN: "test-gateway-token",
        },
        async outboundService(request) {
          const url = new URL(request.url);
          if (!url.pathname.endsWith("/ai/v1/chat/completions")) {
            return new Response(`unexpected egress: ${url.pathname}`, { status: 599 });
          }
          let sent: { model?: string; tools?: unknown[] } = {};
          try {
            sent = (await request.json()) as typeof sent;
          } catch {
            sent = {};
          }
          const toolCount = Array.isArray(sent.tools) ? sent.tools.length : 0;
          return Response.json({
            id: "fake-completion",
            object: "chat.completion",
            created: 1,
            model: sent.model ?? "unknown",
            choices: [
              {
                index: 0,
                message: {
                  role: "assistant",
                  content: fakeGateway(sent.model ?? "unknown", request.headers, toolCount),
                },
                finish_reason: "stop",
              },
            ],
            usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
          });
        },
      },
    }),
  ],
  test: {
    include: ["test/**/*.test.ts"],
  },
});
