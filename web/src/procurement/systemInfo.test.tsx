import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SystemInfo } from "./SystemInfo";

function platformBody() {
  return {
    service: "procurement-service",
    backend_version: "0.5.0",
    api_schema_version: 19,
    components: { mysql: "ready", kafka: "127.0.0.1:9092", redis: "ready" },
    parsers: { quote_parser_versions: ["packaging-quote-v3"] },
    rulesets: { comparison_rulesets: ["landed-cost-v1"] },
    model: { provider: "procurement_fake", model: "procurement-fake-v1", api_key_configured: false, api_key_preview: null, reasoning_effort: "none" },
    db: { status: "ready", ai_tasks: 3 },
    gateway: {
      source: "heartbeat",
      providers: [
        { provider: "openai", state: "open", remaining_open_s: 42, stats: { failures: 7, rate_limited: 2, degraded: 1 }, limits: { qps: 10 } },
        { provider: "procurement_openai", state: "closed", stats: { successes: 9 } },
      ],
    },
    capabilities: ["state_machine_engine_v1", "llm_gateway_v1"],
  };
}

describe("SystemInfo gateway section", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders sanitized LLM gateway states with degradation markers", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/procurement/platform")) {
        return new Response(JSON.stringify(platformBody()), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <SystemInfo />
        </QueryClientProvider>
      );
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(fetchMock).toHaveBeenCalled();

    const text = host.textContent || "";
    expect(text).toContain("LLM 网关（限流 / 熔断 / 降级）");
    expect(text).toContain("熔断中");
    expect(text).toContain("剩余 42s");
    expect(text).toContain("失败 7 / 限流 2 / 降级 1");
    expect(text).toContain("正常");
    expect(text).toContain("llm_gateway_v1");
    expect(text).not.toContain("api_key");
    await act(async () => root.unmount());
    host.remove();
  });
});
