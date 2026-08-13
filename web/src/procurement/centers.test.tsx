import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AiTaskCenter } from "./AiTaskCenter";
import { ReviewCenter } from "./ReviewCenter";
import type {
  AiTaskDetail,
  ComparisonQuote,
  ProcurementRequestSummary,
  ReviewDetail,
} from "./types";

const request: ProcurementRequestSummary = {
  id: "b".repeat(32),
  reference: "RFQ-20260812-ABC123",
  title: "华东仓标签采购",
  category: "general",
  item_name: "热敏标签",
  quantity: 10_000,
  unit: "piece",
  specifications: {},
  constraints: {},
  status: "analyzed",
  requirement_confirmed: true,
  session_id: "session",
  quote_count: 2,
  unresolved_field_count: 0,
  created_at: "2026-08-12T01:00:00Z",
  updated_at: "2026-08-12T01:05:00Z",
};

const aiTask: AiTaskDetail = {
  ai_task_id: "a".repeat(32),
  business_id: request.id,
  generation: 1,
  status: "SUCCEEDED",
  task_type: "QUOTE_ANALYSIS",
  trace_id: "c".repeat(32),
  current_step: "RESULT_PUBLISH",
  progress: 1,
  retry_count: 0,
  max_retries: 3,
  retryable: false,
  operation_id: "11111111-1111-1111-1111-111111111111",
  result_id: "d".repeat(32),
  stale: false,
  error_code: null,
  assignee: "采购员",
  started_at: "2026-08-12T01:00:00Z",
  finished_at: "2026-08-12T01:00:02Z",
  created_at: "2026-08-12T01:00:00Z",
  updated_at: "2026-08-12T01:00:02Z",
  records: [{
    record_id: "e".repeat(32),
    ai_task_id: "a".repeat(32),
    operation_id: "11111111-1111-1111-1111-111111111111",
    attempt: 1,
    sequence: 1,
    step: "QUOTE_PARSE",
    status: "SUCCEEDED",
    summary: "已核对两份报价来源",
    duration_ms: 42,
    created_at: "2026-08-12T01:00:01Z",
  }],
  result: {
    ai_result_id: "d".repeat(32),
    ai_task_id: "a".repeat(32),
    business_id: request.id,
    generation: 1,
    input_sha256: "1".repeat(64),
    result_sha256: "2".repeat(64),
    raw_result: { quote_count: 2 },
    structured_result: { summary: "两份报价均已核对", risk_flags: [] },
    sources: [{
      artifact_id: `jb${"3".repeat(32)}`,
      locator: "报价明细!B4",
      excerpt: "供应商名称：甲方标签",
      confidence: 0.97,
      method: "key_value_cell",
    }],
    provider: "procurement_fake",
    model: "deterministic",
    prompt_version: "quote-analysis-v1",
    parser_version: "packaging-quote-v3",
    stale: false,
    created_at: "2026-08-12T01:00:02Z",
  },
};

const quote = (id: string, supplier: string, eligible: boolean, total: string): ComparisonQuote => ({
  quote_id: id,
  supplier_name: supplier,
  eligible,
  exclusion_reasons: eligible ? [] : [{ code: "LEAD_TIME", message: "交期超过上限" }],
  warnings: [],
  match: { item: "热敏标签", quoted_description: "100x150 标签", passed: true, spec_checks: [] },
  commercial: {
    moq: eligible ? 5_000 : 20_000,
    lead_time_days: eligible ? 7 : 30,
    tax_rate: "0.13",
    tax_included: true,
    shipping_included: true,
    supports_invoice: true,
  },
  cost: {
    quote_currency: "CNY",
    base_currency: "CNY",
    fx_rate: "1",
    quoted_price: total,
    price_basis: 10_000,
    normalized_unit_quote_currency: "0.05",
    goods_before_tax_quote_currency: total,
    tax_quote_currency: "57.52",
    freight_quote_currency: "0",
    landed_total_quote_currency: total,
    landed_total_base: total,
    landed_unit_base: "0.05",
  },
  rank: eligible ? 1 : null,
  score: eligible ? "100" : null,
});

const review: ReviewDetail = {
  review_id: "f".repeat(32),
  business_id: request.id,
  ai_task_id: aiTask.ai_task_id,
  ai_result_id: aiTask.result!.ai_result_id,
  status: "PENDING",
  priority: 70,
  risk_flags: ["LOW_CONFIDENCE"],
  waiting_since: "2026-08-12T01:00:03Z",
  version: 0,
  generation: 1,
  task_version: 4,
  snapshot_id: "4".repeat(32),
  input_sha256: "1".repeat(64),
  suggested_quote_id: "quote-alpha",
  evidence_sha256: "5".repeat(64),
  created_at: "2026-08-12T01:00:03Z",
  updated_at: "2026-08-12T01:00:03Z",
  ai_result: aiTask.result!,
  comparison: {
    id: "4".repeat(32),
    request_id: request.id,
    run_id: "6".repeat(32),
    version: 1,
    input_sha256: "1".repeat(64),
    artifact_id: `jb${"7".repeat(32)}`,
    created_at: "2026-08-12T01:00:02Z",
    result: {
      schema_version: 1,
      ruleset_version: "landed-cost-v1",
      request_id: request.id,
      base_currency: "CNY",
      quantity: 10_000,
      quotes: [
        quote("quote-alpha", "甲方标签", true, "500.00"),
        quote("quote-beta", "乙方标签", false, "480.00"),
      ],
      eligible_count: 1,
      excluded_count: 1,
      recommended_quote_id: "quote-alpha",
      recommendation_explanation: ["满足硬约束且到货成本最低"],
    },
  },
  history: [],
};

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
}

function aiHtml() {
  const queryClient = client();
  queryClient.setQueryData(["procurement-ai-task", aiTask.ai_task_id], aiTask);
  return renderToString(
    <QueryClientProvider client={queryClient}>
      <AiTaskCenter
        requests={[request]}
        tasks={[aiTask]}
        loading={false}
        error={null}
        selectedId={aiTask.ai_task_id}
        onSelect={vi.fn()}
        onOpenTask={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

function reviewHtml(value: ReviewDetail) {
  const queryClient = client();
  queryClient.setQueryData(["procurement-review", value.review_id], value);
  return renderToString(
    <QueryClientProvider client={queryClient}>
      <ReviewCenter
        requests={[request]}
        reviews={[value]}
        loading={false}
        error={null}
        selectedId={value.review_id}
        onSelect={vi.fn()}
        onOpenTask={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("AI task center", () => {
  it("shows persisted steps, result versions and source artifacts", () => {
    const html = aiHtml();
    expect(html).toContain("AI 任务中心");
    expect(html).toContain("已核对两份报价来源");
    expect(html).toContain("两份报价均已核对");
    expect(html).toContain("quote-analysis-v1");
    expect(html).toContain("packaging-quote-v3");
    expect(html).toContain("报价明细!B4");
    expect(html).toContain("采购详情");
  });
});

describe("human review center", () => {
  it("shows immutable AI advice, quote evidence and all four actions", () => {
    const html = reviewHtml(review);
    expect(html).toContain("AI 建议");
    expect(html).toContain("甲方标签");
    expect(html).toContain("乙方标签");
    expect(html).toContain("交期超过上限");
    expect(html).toContain("确认建议");
    expect(html).toContain("修改后通过");
    expect(html).toContain("驳回重跑");
    expect(html).toContain("流标");
    expect(html).toContain("提交审核");
  });

  it("requires a second confirmation before submitting an action", async () => {
    const queryClient = client();
    queryClient.setQueryData(["procurement-review", review.review_id], review);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ReviewCenter
            requests={[request]}
            reviews={[review]}
            loading={false}
            error={null}
            selectedId={review.review_id}
            onSelect={vi.fn()}
            onOpenTask={vi.fn()}
          />
        </QueryClientProvider>,
      );
    });
    const submit = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("提交审核"));
    expect(submit).toBeTruthy();
    await act(async () => submit!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("确认提交：确认 AI 建议");
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("我已核对 AI 建议、报价原件与确定性比价证据");
    await act(async () => root.unmount());
  });

  it("keeps a finalized decision read-only", () => {
    const completed: ReviewDetail = {
      ...review,
      status: "APPROVED",
      action: "APPROVE_SUGGESTION",
      actor: "采购员甲",
      final_quote_id: "quote-alpha",
      decision_id: "8".repeat(32),
      reason: "已核对原件",
      acted_at: "2026-08-12T01:10:00Z",
    };
    const html = reviewHtml(completed);
    expect(html).toContain("正式决定已形成");
    expect(html).toContain("人工审核记录");
    expect(html).not.toContain("提交审核");
  });
});
