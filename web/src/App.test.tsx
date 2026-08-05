import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App from "./App";
import { api } from "./api/client";
import { REQUIRED_API_SCHEMA_VERSION } from "./api/compatibility";

describe("采购工作台入口", () => {
  it("只暴露采购运行所需的只读 API", () => {
    expect(Object.keys(api).sort()).toEqual([
      "health",
      "messages",
      "report",
      "run",
      "toolInvocations",
    ]);
  });

  it("在后端兼容时渲染采购入口", () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ matches: false }),
    });
    const client = new QueryClient();
    client.setQueryData(["health"], {
      service: "agentharness",
      status: "ok",
      backend_version: "0.3.0",
      api_schema_version: REQUIRED_API_SCHEMA_VERSION,
      api_capabilities: [
        "procurement_sourcing_v1",
        "procurement_approval_v1",
        "procurement_audit_v1",
        "procurement_stream_v1",
      ],
      data_dir: "/tmp/data",
      max_global_seq: 0,
    });

    const html = renderToString(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>
    );

    expect(html).toContain("采价台");
    expect(html).toContain("采购询价与供应商比价");
    expect(html).toContain('aria-label="搜索采购任务"');
    expect(html).toContain('aria-label="采购目标"');
    expect(html).toContain('data-testid="conversation-upload"');
    expect(html).toContain("报价附件");
    expect(html).not.toContain("把目标交给 Agent");
    expect(html).not.toContain('data-testid="run-composer"');
  });
});
