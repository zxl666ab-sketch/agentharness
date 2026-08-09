import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { ComparisonView } from "./ComparisonView";
import { buildCsv, DashboardView } from "./DashboardView";
import { KnowledgeReferences } from "./KnowledgeReferences";
import { friendlyProcurementError } from "./api";
import {
  NewProcurementConversation,
  ProcurementConversation,
} from "./ProcurementConversation";
import { QuoteWorkspace } from "./QuoteWorkspace";
import { ProcurementWorkbench } from "./ProcurementWorkbench";
import { procurementReportMarkdown, ReportView } from "./ReportView";
import type {
  ProcurementAuditReport,
  ProcurementMeta,
  ProcurementModelConfig,
  ProcurementRequest,
} from "./types";

const meta: ProcurementMeta = {
  category: "ecommerce_packaging",
  parser_version: "packaging-quote-v3",
  ruleset_version: "landed-cost-v1",
  max_file_bytes: 5_242_880,
  max_conversation_upload_bytes: 20_971_520,
  max_quotes_per_request: 50,
  allowed_extensions: [".xlsx", ".pdf"],
  field_meta: {
    supplier_name: { label: "供应商", kind: "text", required: true },
    unit_price: { label: "报价", kind: "decimal", required: true },
  },
};

const request: ProcurementRequest = {
  id: "request",
  reference: "RFQ-20260727-ABC123",
  title: "华东仓快递袋询价",
  category: "ecommerce_packaging",
  item_name: "快递袋",
  quantity: 10_000,
  unit: "piece",
  specifications: { width_mm: "250", length_mm: "350", thickness_um: "60" },
  constraints: { max_lead_days: 15 },
  status: "review",
  session_id: "session",
  attachments: [],
  quote_count: 2,
  unresolved_field_count: 1,
  quotes: [{
    id: "quote-alpha",
    request_id: "request",
    supplier_name: "Alpha Packaging",
    source_filename: "Alpha Packaging.xlsx",
    source_kind: "xlsx",
    source_artifact_id: "artifact-source",
    source_sha256: "a".repeat(64),
    extracted: {
      schema_version: 1,
      parser_version: "packaging-quote-v1",
      document_kind: "xlsx",
      processing_ms: 12,
      fields: {
        supplier_name: {
          value: "Alpha Packaging",
          confidence: 0.55,
          status: "needs_review",
          source: {
            document_kind: "xlsx",
            locator: "filename",
            excerpt: "Alpha Packaging.xlsx",
            method: "filename_fallback",
          },
        },
        unit_price: {
          value: "520",
          confidence: 0.97,
          status: "accepted",
          source: {
            document_kind: "xlsx",
            locator: "Quote!B4",
            excerpt: "Unit Price: 520",
            method: "key_value_cell",
          },
        },
      },
    },
    status: "needs_review",
    review_count: 1,
    review_fields: ["supplier_name"],
    parser_version: "packaging-quote-v1",
    processing_ms: 12,
    created_at: "2026-07-27T00:00:00Z",
    updated_at: "2026-07-27T00:00:00Z",
  }],
  comparison: null,
  decision: null,
  created_at: "2026-07-27T00:00:00Z",
  updated_at: "2026-07-27T00:00:00Z",
};

function analyzed(): ProcurementRequest {
  return {
    ...request,
    status: "analyzed",
    unresolved_field_count: 0,
    analysis_run_id: "run",
    current_snapshot_id: "snapshot",
    comparison: {
      id: "snapshot",
      request_id: "request",
      run_id: "run",
      version: 1,
      input_sha256: "b".repeat(64),
      artifact_id: "artifact-comparison",
      created_at: "2026-07-27T00:00:02Z",
      result: {
        schema_version: 1,
        ruleset_version: "landed-cost-v1",
        request_id: "request",
        base_currency: "CNY",
        quantity: 10_000,
        eligible_count: 1,
        excluded_count: 1,
        recommended_quote_id: "quote-alpha",
        recommendation_explanation: ["Alpha Packaging 在满足全部硬性条件的报价中到货总成本最低"],
        approval: { id: "approval", invocation_id: "invocation", arguments_sha256: "c".repeat(64), status: "pending" },
        quotes: [
          {
            quote_id: "quote-alpha",
            supplier_name: "Alpha Packaging",
            eligible: true,
            exclusion_reasons: [],
            warnings: [],
            match: { item: "快递袋", quoted_description: "PE mailer", passed: true, spec_checks: [] },
            commercial: { moq: 5000, lead_time_days: 7, tax_rate: "0.13", tax_included: true, shipping_included: true, supports_invoice: true },
            cost: { quote_currency: "CNY", base_currency: "CNY", fx_rate: "1", quoted_price: "520", price_basis: 1000, normalized_unit_quote_currency: "0.5200", goods_before_tax_quote_currency: "5200.00", tax_quote_currency: "598.23", freight_quote_currency: "0.00", landed_total_quote_currency: "5200.00", landed_total_base: "5200.00", landed_unit_base: "0.5200" },
            rank: 1,
            score: "100.0",
          },
          {
            quote_id: "quote-delta",
            supplier_name: "Delta Factory",
            eligible: false,
            exclusion_reasons: [{ code: "moq", message: "起订量（MOQ）20000 高于采购量 10000" }],
            warnings: [],
            match: { item: "快递袋", quoted_description: "PE mailer", passed: true, spec_checks: [] },
            commercial: { moq: 20_000, lead_time_days: 8, tax_rate: "0.13", tax_included: true, shipping_included: true, supports_invoice: true },
            cost: { quote_currency: "CNY", base_currency: "CNY", fx_rate: "1", quoted_price: "490", price_basis: 1000, normalized_unit_quote_currency: "0.4900", goods_before_tax_quote_currency: "4900.00", tax_quote_currency: "563.72", freight_quote_currency: "0.00", landed_total_quote_currency: "4900.00", landed_total_base: "4900.00", landed_unit_base: "0.4900" },
            rank: null,
            score: null,
          },
        ],
      },
    },
  };
}

vi.mock("../useAgentStream", () => ({
  useAgentStream: () => ({ status: "closed", events: [] }),
}));

describe("procurement workflow views", () => {
  it("explains a blocked model request and preserves the recovery path", () => {
    expect(friendlyProcurementError("Your request was blocked.")).toContain("模型网关拒绝");
    expect(friendlyProcurementError("Your request was blocked.")).toContain("从持久化状态重新分析");
  expect(
    friendlyProcurementError("UNIQUE constraint failed: tool_invocations.id")
  ).toContain("刷新");
  expect(
    friendlyProcurementError("UNIQUE constraint failed: tool_invocations.id")
  ).toContain("无需重复操作");
  });

  it("keeps the reply composer available whenever the agent requires human input", () => {
    const queryClient = new QueryClient();
    const clarificationRequest: ProcurementRequest = {
      ...request,
      status: "collecting",
      analysis_run_id: "run-needs-input",
      unresolved_field_count: 0,
    };
    queryClient.setQueryData(["procurement-run", "run-needs-input"], {
      id: "run-needs-input",
      status: "require_human",
      error: null,
    });
    queryClient.setQueryData(["procurement-messages", "run-needs-input"], []);
    queryClient.setQueryData(["procurement-tools", "run-needs-input"], []);

    const html = renderToString(
      <QueryClientProvider client={queryClient}>
        <ProcurementConversation
          request={clarificationRequest}
          streamLive
          onResume={async () => undefined}
          onRecover={async () => undefined}
          onOpenComparison={() => undefined}
        />
      </QueryClientProvider>
    );

    expect(html).toContain('aria-label="补充澄清信息"');
  });

  it("renders a completed run as finished instead of waiting after refresh", () => {
    const queryClient = new QueryClient();
    const completedRequest: ProcurementRequest = {
      ...request,
      status: "analyzed",
      analysis_run_id: "run-completed",
      unresolved_field_count: 0,
    };
    queryClient.setQueryData(["procurement-run", "run-completed"], {
      id: "run-completed",
      status: "completed",
      error: null,
    });
    queryClient.setQueryData(["procurement-messages", "run-completed"], []);
    queryClient.setQueryData(["procurement-tools", "run-completed"], []);

    const html = renderToString(
      <QueryClientProvider client={queryClient}>
        <ProcurementConversation
          request={completedRequest}
          streamLive
          onResume={async () => undefined}
          onRecover={async () => undefined}
          onOpenComparison={() => undefined}
        />
      </QueryClientProvider>
    );

    expect(html).toContain("采购决策已完成");
    expect(html).not.toContain("等待运行");
    expect(html).not.toContain("Agent 正在分析报价");
  });

  it("uses the server quote limit for a 30-file blind-test batch", () => {
    const html = renderToString(
      <NewProcurementConversation
        busy={false}
        maxFileBytes={meta.max_file_bytes}
        maxTotalBytes={meta.max_conversation_upload_bytes}
        maxQuotes={meta.max_quotes_per_request}
        onStart={async () => undefined}
      />
    );
    expect(html).toContain("0<!-- --> / <!-- -->50<!-- --> 份");
  });

  it("shows low-confidence fields with source evidence and blocks comparison", () => {
    const html = renderToString(
      <QuoteWorkspace
        request={request}
        meta={meta}
        busy={null}
        onUpload={async () => undefined}
        onCorrect={async () => undefined}
        onAnalyze={async () => undefined}
      />
    );
    expect(html).toContain("55%");
    expect(html).toContain("文件名");
    expect(html).toContain("Alpha Packaging.xlsx");
    expect(html).toContain("1 项待复核");
    expect(html).toContain("确认当前值并完成复核");
    expect(html).toContain("disabled");
  });

  it("explains deterministic cost ranking after excluding hard violations", () => {
    const html = renderToString(
      <ComparisonView request={analyzed()} busy={null} onAnalyze={async () => undefined} onApprove={async () => undefined} onNoAward={async () => undefined} />
    );
    expect(html).toContain("规则推荐");
    expect(html).toContain("起订量（MOQ）20000 高于采购量 10000");
    expect(html).toContain("总到货成本");
    expect(html).toContain("精确金额核算");
    expect(html).toContain("提交供应商审批");
  });

  it("allows the buyer to submit a no-award decision when every quote is excluded", () => {
    const noAward: ProcurementRequest = analyzed();
    noAward.comparison = {
      ...noAward.comparison!,
      result: {
        ...noAward.comparison!.result,
        eligible_count: 0,
        excluded_count: noAward.comparison!.result.quotes.length,
        recommended_quote_id: null,
        recommendation_explanation: ["没有报价满足全部硬性条件"],
        quotes: noAward.comparison!.result.quotes.map((quote) => ({
          ...quote,
          eligible: false,
          rank: null,
          score: null,
          exclusion_reasons: quote.exclusion_reasons.length
            ? quote.exclusion_reasons
            : [{ code: "budget", message: "到货单价超过预算" }],
        })),
      },
    };

    const html = renderToString(
      <ComparisonView
        request={noAward}
        busy={null}
        onAnalyze={async () => undefined}
        onApprove={async () => undefined}
        onNoAward={async () => undefined}
      />
    );

    expect(html).toContain("确认无合格报价");
  });

  it("renders a durable approved report with source and comparison hashes", () => {
    const approved: ProcurementRequest = {
      ...analyzed(),
      status: "approved",
      approved_quote_id: "quote-alpha",
      decision: {
        id: "decision",
        request_id: "request",
        snapshot_id: "snapshot",
        quote_id: "quote-alpha",
        run_id: "run",
        approval_id: "approval",
        decision: "approved",
        actor: "采购员",
        note: "成本和交期符合计划",
        created_at: "2026-07-27T00:00:04Z",
      },
    };
    const report = {
      schema_version: 1,
      evidence_sha256: "d".repeat(64),
      request: approved,
      quotes: approved.quotes,
      comparison: approved.comparison,
      decision: approved.decision,
      audit_events: [{ id: "event", request_id: "request", run_id: "run", type: "supplier_approved", actor: "采购员", payload: {}, created_at: "2026-07-27T00:00:04Z" }],
      runtime: { session_id: "session", run_id: "run" },
    } satisfies ProcurementAuditReport;
    const html = renderToString(<ReportView request={approved} report={report} loading={false} />);
    expect(html).toContain("已选定");
    expect(html).toContain("Alpha Packaging");
    expect(html).toContain("证据指纹");
    expect(html).toContain("报价原件与字段来源");
    expect(html).toContain("采购审计时间线");
    const markdown = procurementReportMarkdown(report);
    expect(markdown).toContain("# 采购审批报告");
    expect(markdown).toContain("选定供应商：Alpha Packaging");
    expect(markdown).toContain("供应商已人工批准");
    expect(markdown).toContain("分析运行 ID：run");
  });
});
describe("历史成交参考（stage-6 RAG）", () => {
  function reference(index: number) {
    const id = `chunk-${index}`;
    return {
      chunk_id: id,
      chunk_sha256: id.padEnd(64, "0"),
      request_reference: `RFQ-2026060${index}-HISTORY`,
      decision_at: `2026-06-0${index}T00:00:00+00:00`,
      supplier_name: `供应商${index}`,
      item_name: "快递袋",
      specification_summary: "250×350mm / 60μm / PE / 白色 / 1色",
      unit_price: "0.42",
      currency: "CNY",
      landed_unit_cost: "0.4521",
      lead_days: 10,
      moq: 5000,
      decision: "approved",
      source_sha256: "9".repeat(64),
      score: "0.93",
      quality_flags: [],
      text: `RFQ-2026060${index}-HISTORY 供应商${index} 快递袋`,
    };
  }

  function references(count: number) {
    return Array.from({ length: count }, (_, index) => reference(index + 1));
  }

  it("shows top-3 by default with traceable source and expandable top-5", () => {
    const withHistory = analyzed();
    withHistory.knowledge_references = references(5);
    const html = renderToString(
      <ComparisonView
        request={withHistory}
        busy={null}
        onAnalyze={async () => undefined}
        onApprove={async () => undefined}
        onNoAward={async () => undefined}
        onKnowledgeFeedback={() => undefined}
      />
    );
    expect(html).toContain("历史成交参考");
    expect(html).toContain("供应商1");
    expect(html).toContain("供应商2");
    expect(html).toContain("供应商3");
    expect(html).not.toContain("供应商4");
    expect(html).toContain("展开全部 5 条");
    expect(html).toContain("已成交");
    expect(html).toContain("RFQ-20260601-HISTORY");
    expect(html).toContain("查看详情");
    expect(html).toContain("有帮助");
  });

  it("shows the empty state when there is no similar history", () => {
    const noHistory = analyzed();
    noHistory.knowledge_references = [];
    const html = renderToString(
      <ComparisonView
        request={noHistory}
        busy={null}
        onAnalyze={async () => undefined}
        onApprove={async () => undefined}
        onNoAward={async () => undefined}
      />
    );
    expect(html).toContain("暂无相似历史成交");
  });

  it("expands from top-3 to top-5 and records viewed/adopted feedback", async () => {
    const calls: Array<[string, string]> = [];
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <KnowledgeReferences
          references={references(5)}
          onFeedback={(chunkId, action) => calls.push([chunkId, action])}
        />
      );
    });
    expect(container.querySelectorAll(".proc-knowledge-table tbody tr").length).toBe(3);
    const more = container.querySelector("button.proc-knowledge-more") as HTMLButtonElement;
    await act(async () => {
      more.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelectorAll(".proc-knowledge-table tbody tr").length).toBe(5);

    const viewButton = container.querySelector(
      'button[aria-label^="查看详情"]'
    ) as HTMLButtonElement;
    await act(async () => {
      viewButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const adoptButton = container.querySelector(
      'button[aria-label^="有帮助"]'
    ) as HTMLButtonElement;
    await act(async () => {
      adoptButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(calls).toEqual([
      ["chunk-1".padEnd(64, "0"), "viewed"],
      ["chunk-1".padEnd(64, "0"), "adopted"],
    ]);
    expect(container.textContent).toContain("来源哈希");
    await act(async () => {
      root.unmount();
    });
    document.body.removeChild(container);
  });
});

  it("hides manual correction after approval and keeps cancel for review fields", () => {
    const approvedRequest: ProcurementRequest = {
      ...request,
      status: "approved",
      unresolved_field_count: 0,
      quote_count: 2,
      decision: {
        id: "decision",
        request_id: "request",
        snapshot_id: "snapshot",
        quote_id: "quote-alpha",
        run_id: "run",
        approval_id: "approval",
        decision: "approved",
        actor: "采购员",
        created_at: "2026-07-27T00:00:03Z",
      },
    };
    const html = renderToString(
      <QuoteWorkspace
        request={approvedRequest}
        meta={meta}
        busy={null}
        onUpload={async () => undefined}
        onCorrect={async () => undefined}
        onAnalyze={async () => undefined}
      />
    );
    // Accepted field: the pencil edit entry must disappear once approved.
    expect(html).not.toContain('aria-label="修正报价"');
    // A review field editor still renders (cancel affordance stays), but the
    // save action must be disabled once the request is approved.
    expect(html).toMatch(/aria-label="保存供应商修正"[^>]*disabled/);
    // needs_review field: the inline editor keeps a cancel affordance instead
    // of trapping the buyer with no way out.
    expect(html).toContain('aria-label="取消供应商修正"');
  });

  it("highlights the actually approved supplier after approval", () => {
    const approvedRequest: ProcurementRequest = {
      ...analyzed(),
      status: "approved",
      decision: {
        id: "decision",
        request_id: "request",
        snapshot_id: "snapshot",
        quote_id: "quote-delta",
        run_id: "run",
        approval_id: "approval",
        decision: "approved",
        actor: "采购员",
        created_at: "2026-07-27T00:00:03Z",
      },
    };
    const html = renderToString(
      <ComparisonView
        request={approvedRequest}
        busy={null}
        onAnalyze={async () => undefined}
        onApprove={async () => undefined}
        onNoAward={async () => undefined}
      />
    );
    expect(html).toContain("供应商已人工批准：");
    expect(html).toContain("Delta Factory");
    expect(html).toContain('class="excluded selected"');
    expect(html).not.toContain('class="eligible selected"');
  });


  it("renders the operations dashboard from cached metrics and runs", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(["proc-runs"], {
      items: [
        {
          id: "run-abc",
          session_id: "s",
          root_run_id: "run-abc",
          status: "completed",
          model: "procurement-fake-v1",
          usage_json: JSON.stringify({ total_tokens: 1200, estimated_cost_usd: 0.5 }),
          created_at: "2026-07-27T00:00:00Z",
          updated_at: "2026-07-27T00:00:00Z",
        },
      ],
      total: 1,
      offset: 0,
      has_more: false,
    });
    queryClient.setQueryData(["proc-metrics-summary"], {
      runs: 1,
      by_status: { completed: 1 },
      by_model: { "procurement-fake-v1": 1 },
      tokens: { input: 800, output: 400, cached_input: 0, total: 1200 },
      model_turns: 2,
      estimated_cost_usd: 0.5,
      cost_unknown_runs: 0,
      cache_hit_rate: 0.0,
      avg_duration_ms: 1200,
      duration_runs: 1,
      budget_warnings: 0,
    });

    const html = renderToString(
      <QueryClientProvider client={queryClient}>
        <DashboardView />
      </QueryClientProvider>
    );
    expect(html).toContain("运营仪表盘");
    expect(html).toContain("run-abc");
    expect(html).toContain("$0.5000");
    expect(html).toContain("1,200");
  });

  it("renders comparison with an invalid currency code without crashing", () => {
    const bad = analyzed();
    const quote = bad.comparison!.result.quotes[0];
    quote.cost = { ...quote.cost, quote_currency: "123" };
    const html = renderToString(
      <ComparisonView
        request={bad}
        busy={null}
        onAnalyze={async () => undefined}
        onApprove={async () => undefined}
        onNoAward={async () => undefined}
      />
    );
    // money() falls back to "<value> <currency>" instead of throwing RangeError.
    expect(html).toContain("598.23 123");
  });

  it("keeps two files with the same name and size but different lastModified", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <NewProcurementConversation
          busy={false}
          maxFileBytes={meta.max_file_bytes}
          maxTotalBytes={meta.max_conversation_upload_bytes}
          maxQuotes={meta.max_quotes_per_request}
          onStart={async () => undefined}
        />
      );
    });
    const input = container.querySelector(
      '[data-testid="conversation-upload"]'
    ) as HTMLInputElement;
    const fileA = new File(["aaaa"], "quote.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      lastModified: 1000,
    });
    const fileB = new File(["aaaa"], "quote.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      lastModified: 2000,
    });
    expect(fileA.lastModified).toBe(1000);
    expect(fileB.lastModified).toBe(2000);
    Object.defineProperty(input, "files", {
      value: [fileA, fileB] as unknown as FileList,
      configurable: true,
    });
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(container.querySelectorAll(".proc-compose-files li").length).toBe(2);
    await act(async () => {
      root.unmount();
    });
    document.body.removeChild(container);
  });

  it("guards CSV cells against formula injection through leading whitespace", () => {
    const csv = buildCsv([
      { a: "  =1+1", b: "normal", c: "\t=SUM(A1)", d: "\n=cmd()" },
    ]);
    expect(csv).toContain('"\'  =1+1"');
    expect(csv).toContain('"normal"');
    expect(csv).toContain('"\'\t=SUM(A1)"');
    expect(csv).toContain('"\'\n=cmd()"');


  });

  it("restores focus in the config drawer after saving and keeps Escape dismissal", async () => {
    const originalFetch = globalThis.fetch;
    const config: ProcurementModelConfig = {
      provider: "openai",
      model: "gpt-4o-mini",
      base_url: null,
      api_mode: "auto",
      reasoning_effort: "auto",
      api_key_configured: true,
      api_key_preview: "••••abcd",
      input_price_per_million_usd: 0,
      output_price_per_million_usd: 0,
      cached_input_price_per_million_usd: 0,
      max_cost_usd: null,
      ai_review_enabled: false,
      review_provider: "openai",
      review_model: null,
      review_policy: "evidence",
    };
    const json = (data: unknown) =>
      new Response(JSON.stringify(data), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/procurement/meta")) return json(meta);
      if (url.endsWith("/api/procurement/config")) return json(config);
      if (url.includes("/api/procurement/requests")) return json([]);
      return new Response("not found", { status: 404 });
    }) as unknown as typeof fetch;

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    try {
      await act(async () => {
        root.render(
          <QueryClientProvider client={client}>
            <ProcurementWorkbench theme="light" backendVersion="0.3.0" onToggleTheme={() => undefined} />
          </QueryClientProvider>
        );
      });
      const configButton = container.querySelector('button[aria-label="API / 模型配置"]') as HTMLButtonElement;
      expect(configButton).toBeTruthy();
      await act(async () => {
        configButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await act(async () => {
        await Promise.resolve();
      });
      const dialog = container.querySelector('[role="dialog"]');
      expect(dialog).toBeTruthy();
      const submit = container.querySelector('[role="dialog"] button[type="submit"]') as HTMLButtonElement;
      expect(submit.textContent).toContain("保存配置");
      // Clicking the submit button disables it while saving (browsers then
      // drop focus to <body>); the drawer must return focus to the close
      // button so Escape and keyboard navigation keep working.
      await act(async () => {
        submit.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      });
      expect(document.activeElement?.getAttribute("aria-label")).toBe("关闭配置");
      // Escape must dismiss even when focus is outside the dialog.
      document.body.focus();
      await act(async () => {
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      });
      expect(container.querySelector('[role="dialog"]')).toBeNull();
    } finally {
      await act(async () => {
        root.unmount();
      });
      document.body.removeChild(container);
      globalThis.fetch = originalFetch;
    }
  });
