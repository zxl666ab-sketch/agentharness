import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { ComparisonView } from "./ComparisonView";
import { friendlyProcurementError } from "./api";
import {
  NewProcurementConversation,
  ProcurementConversation,
} from "./ProcurementConversation";
import { QuoteWorkspace } from "./QuoteWorkspace";
import { procurementReportMarkdown, ReportView } from "./ReportView";
import type {
  ProcurementAuditReport,
  ProcurementMeta,
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

describe("procurement workflow views", () => {
  it("explains a blocked model request and preserves the recovery path", () => {
    expect(friendlyProcurementError("Your request was blocked.")).toContain("模型网关拒绝");
    expect(friendlyProcurementError("Your request was blocked.")).toContain("从持久化状态重新分析");
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
