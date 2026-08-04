import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ComparisonView } from "./ComparisonView";
import { NewProcurementConversation, ProcurementConversation } from "./ProcurementConversation";
import { QuoteWorkspace } from "./QuoteWorkspace";
import { RequirementReview } from "./RequirementReview";
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

function allExcluded(): ProcurementRequest {
  const current = analyzed();
  const comparison = current.comparison!;
  return {
    ...current,
    comparison: {
      ...comparison,
      result: {
        ...comparison.result,
        eligible_count: 0,
        excluded_count: comparison.result.quotes.length,
        recommended_quote_id: null,
        recommendation_explanation: ["没有报价同时满足全部硬性条件，需要调整需求或重新询价"],
        quotes: comparison.result.quotes.map((quote) => ({
          ...quote,
          eligible: false,
          rank: null,
          score: null,
          exclusion_reasons: [{ code: "lead_time", message: "交期超过上限" }],
        })),
      },
    },
  };
}

describe("procurement workflow views", () => {
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

  it("does not duplicate V2 dynamic specs in the quote review", () => {
    const dynamicRequest: ProcurementRequest = {
      ...request,
      schema_version: 2,
      category: "label_printing",
      specifications: {
        material: {
          label: "材质",
          type: "text",
          value: "铜版纸",
          match: "exact",
          priority: "hard",
        },
        color: {
          label: "颜色",
          type: "text",
          value: "白色",
          match: "exact",
          priority: "hard",
        },
      },
      quotes: [{
        ...request.quotes[0],
        extracted: {
          ...request.quotes[0].extracted,
          specifications: {
            material: {
              value: "铜版纸",
              confidence: 0.9,
              status: "accepted",
              source: { document_kind: "xlsx", locator: "Quote!B2", excerpt: "材质: 铜版纸", method: "test" },
            },
            color: {
              value: "白色",
              confidence: 0.9,
              status: "accepted",
              source: { document_kind: "xlsx", locator: "Quote!B3", excerpt: "颜色: 白色", method: "test" },
            },
          },
        },
      }],
    };
    const html = renderToString(
      <QuoteWorkspace
        request={dynamicRequest}
        meta={meta}
        busy={null}
        onUpload={async () => undefined}
        onCorrect={async () => undefined}
        onAnalyze={async () => undefined}
      />
    );
    expect((html.match(/data-field="material"/g) || []).length).toBe(1);
    expect((html.match(/data-field="color"/g) || []).length).toBe(1);
    expect(html).toContain("报价字段与来源证据");
  });

  it("shows V2 specs from standard quote fields and hides duplicated MOQ", () => {
    const extractedField = (value: string | number) => ({
      value,
      confidence: 0.97,
      status: "accepted" as const,
      source: {
        document_kind: "xlsx",
        locator: "报价明细!A4",
        excerpt: String(value),
        method: "table_header",
      },
    });
    const dynamicRequest: ProcurementRequest = {
      ...request,
      schema_version: 2,
      category: "ecommerce_packaging",
      specifications: {
        尺寸: {
          label: "成品尺寸",
          type: "text",
          value: "100×150",
          match: "exact",
          priority: "hard",
        },
        厚度: {
          label: "材料厚度",
          type: "number",
          value: "80",
          unit: "μm",
          match: "tolerance",
          tolerance: "5",
          priority: "hard",
        },
        材质: {
          label: "面材",
          type: "text",
          value: "铜版纸",
          match: "exact",
          priority: "hard",
        },
        颜色: {
          label: "底色",
          type: "text",
          value: "白色",
          match: "exact",
          priority: "hard",
        },
        MOQ: {
          label: "最小起订量",
          type: "number",
          value: "20000",
          unit: "张",
          match: "lte",
          priority: "hard",
        },
      },
      quotes: [{
        ...request.quotes[0],
        status: "ready",
        review_count: 0,
        review_fields: [],
        extracted: {
          ...request.quotes[0].extracted,
          fields: {
            ...request.quotes[0].extracted.fields,
            width_mm: extractedField("100"),
            length_mm: extractedField("150"),
            thickness_um: extractedField("80"),
            material: extractedField("铜版纸"),
            color: extractedField("白色"),
            moq: extractedField(10000),
          },
        },
      }],
      quote_count: 1,
      unresolved_field_count: 0,
    };
    const html = renderToString(
      <QuoteWorkspace
        request={dynamicRequest}
        meta={{
          ...meta,
          field_meta: {
            ...meta.field_meta,
            moq: { label: "起订量（MOQ）", kind: "integer", required: true },
          },
        }}
        busy={null}
        onUpload={async () => undefined}
        onCorrect={async () => undefined}
        onAnalyze={async () => undefined}
      />
    );
    expect(html).toContain('data-field="尺寸"');
    expect(html).toContain("100×150");
    expect(html).toContain("80");
    expect(html).toContain("铜版纸");
    expect(html).toContain("白色");
    expect((html.match(/data-field="MOQ"/g) || []).length).toBe(0);
    expect(html).not.toContain("原文未找到");
  });

  it("keeps the quote workspace visible for completed requests", () => {
    const html = renderToString(
      <RequirementReview
        request={{ ...request, status: "approved" }}
        busy={false}
        onSave={async () => undefined}
      />
    );
    expect(html).toContain("采购需求已确认");
    expect(html).toContain("展开");
    expect(html).not.toContain("保存人工确认");
  });

  it("derives the required delivery date from the request date and lead time", () => {
    const html = renderToString(
      <RequirementReview
        request={request}
        busy={false}
        onSave={async () => undefined}
      />
    );
    expect(html).toContain('value="2026-08-11"');
  });

  it("renders dynamic specification rows for a V2 requirement", () => {
    const dynamicRequest: ProcurementRequest = {
      ...request,
      schema_version: 2,
      category: "general",
      item_name: "透明封箱胶带",
      quantity: "12.5",
      unit: "卷",
      specifications: {
        length: {
          label: "长度",
          type: "number",
          value: "100",
          unit: "m",
          match: "exact",
          priority: "hard",
        },
      },
    };
    const html = renderToString(
      <RequirementReview
        request={dynamicRequest}
        busy={false}
        onSave={async () => undefined}
      />
    );
    expect(html).toContain("新增规格");
    expect(html).toContain("长度");
    expect(html).not.toContain("宽度（mm）");
  });

  it("keeps a reply composer when requirement capture asks for confirmation", () => {
    const client = new QueryClient();
    client.setQueryData(["procurement-run", "run"], {
      status: "require_human",
      error: "verification requires human review: output is missing required text: ['【采购决策已验证】']",
    });
    client.setQueryData(["procurement-messages", "run"], []);
    client.setQueryData(["procurement-tools", "run"], []);
    const html = renderToString(
      <QueryClientProvider client={client}>
        <ProcurementConversation
          request={{ ...request, analysis_run_id: "run", unresolved_field_count: 0 }}
          onResume={async () => undefined}
          onRecover={async () => undefined}
          onOpenComparison={() => undefined}
        />
      </QueryClientProvider>
    );
    expect(html).toContain("补充 Agent 请求的信息");
    expect(html).toContain("恢复采购 Agent");
    expect(html).toContain("报价字段尚未全部确认，请在右侧复核后继续。");
    expect(html).not.toContain("verification requires human review");
    expect(html).not.toContain("【采购决策已验证】");
  });

  it("explains deterministic cost ranking after excluding hard violations", () => {
    const html = renderToString(
      <ComparisonView request={analyzed()} busy={null} onAnalyze={async () => undefined} onApprove={async () => undefined} />
    );
    expect(html).toContain("规则推荐");
    expect(html).toContain("起订量（MOQ）20000 高于采购量 10000");
    expect(html).toContain("总到货成本");
    expect(html).toContain("精确金额核算");
    expect(html).toContain("提交供应商审批");
  });

  it("keeps recovery actions available when every quote is excluded", () => {
    const html = renderToString(
      <ComparisonView request={allExcluded()} busy={null} onAnalyze={async () => undefined} onApprove={async () => undefined} />
    );
    expect(html).toContain("调整需求");
    expect(html).toContain("补充报价");
    expect(html).toContain("重新比价");
    expect(html).toContain("本轮流标");
    expect(html).not.toContain("提交供应商审批");
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

  it("renders a durable no-award report without a supplier or execution drafts", () => {
    const noAward: ProcurementRequest = {
      ...allExcluded(),
      status: "no_award",
      decision: {
        id: "decision-no-award",
        request_id: "request",
        snapshot_id: "snapshot",
        quote_id: null,
        run_id: "run",
        approval_id: "approval",
        decision: "no_award",
        actor: "采购员",
        note: "本轮报价均不满足交期",
        created_at: "2026-07-27T00:00:04Z",
      },
    };
    const report = {
      schema_version: 1,
      evidence_sha256: "e".repeat(64),
      request: noAward,
      quotes: noAward.quotes,
      comparison: noAward.comparison,
      decision: noAward.decision,
      execution_artifacts: [],
      audit_events: [{ id: "event-no-award", request_id: "request", run_id: "run", type: "supplier_no_award", actor: "采购员", payload: {}, created_at: "2026-07-27T00:00:04Z" }],
      runtime: { session_id: "session", run_id: "run" },
    } satisfies ProcurementAuditReport;
    const html = renderToString(<ReportView request={noAward} report={report} loading={false} />);
    expect(html).toContain("本轮流标");
    expect(html).toContain("流标不会生成订单或供应商邮件草稿");
    expect(procurementReportMarkdown(report)).toContain("本轮流标");
  });
});
