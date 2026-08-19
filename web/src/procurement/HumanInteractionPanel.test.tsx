import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HumanInteractionPanel } from "./HumanInteractionPanel";
import { ProcurementConversation } from "./ProcurementConversation";
import type { HumanInteraction, ProcurementRequest } from "./types";

function interaction(overrides: Partial<HumanInteraction> = {}): HumanInteraction {
  return {
    id: "interaction-1",
    task_id: "task-1",
    run_id: "run-1",
    checkpoint_id: "checkpoint-1",
    generation: 2,
    kind: "REVIEW",
    question: "请补充采购数量和最长交期",
    reason: "这些字段会影响供应商资格和排序。",
    business_step: "字段复核",
    related_fields: ["quantity", "max_lead_days"],
    related_artifact_ids: [],
    answer_schema: {
      type: "field_review",
      fields: [
        { name: "quantity", label: "采购数量", type: "number", required: true, unit: "个" },
        { name: "max_lead_days", label: "最长交期", type: "number", required: true, unit: "天" },
      ],
    },
    status: "WAITING",
    answer: null,
    answer_note: null,
    answer_artifact_ids: null,
    answered_by: null,
    answered_at: null,
    applied_at: null,
    expires_at: null,
    cancel_reason: null,
    operation_id: null,
    created_at: "2026-08-18T00:00:00Z",
    updated_at: "2026-08-18T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mount(element: React.ReactNode, queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
})) {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  act(() => root.render(<QueryClientProvider client={queryClient}>{element}</QueryClientProvider>));
  return { host, root, queryClient };
}

function setInput(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = input instanceof HTMLTextAreaElement
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function submit(host: HTMLElement) {
  await act(async () => {
    host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await Promise.resolve();
  });
}

async function click(button: HTMLButtonElement) {
  await act(async () => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
  });
}

const roots: Root[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount());
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("HumanInteractionPanel", () => {
  it("renders the blocking question context and validates required review fields", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const mounted = mount(<HumanInteractionPanel interaction={interaction()} />);
    roots.push(mounted.root);

    expect(mounted.host.textContent).toContain("请补充采购数量和最长交期");
    expect(mounted.host.textContent).toContain("这些字段会影响供应商资格和排序。");
    expect(mounted.host.textContent).toContain("字段复核");

    await submit(mounted.host);

    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toBe("请填写采购数量");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("submits JSON numbers and reuses the idempotency key for the same network retry", async () => {
    const calls: Array<{ body: Record<string, unknown>; key: string | null }> = [];
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
        key: new Headers(init?.headers).get("Idempotency-Key"),
      });
      return calls.length === 1
        ? jsonResponse({ message: "网络超时，请重试" }, 500)
        : jsonResponse({ operation_id: "operation-1", status: "accepted" });
    });
    vi.stubGlobal("fetch", fetchMock);
    const mounted = mount(<HumanInteractionPanel interaction={interaction()} />);
    roots.push(mounted.root);
    const inputs = [...mounted.host.querySelectorAll<HTMLInputElement>('input[type="number"]')];

    act(() => {
      setInput(inputs[0], "5000");
      setInput(inputs[1], "15");
    });
    await submit(mounted.host);
    await submit(mounted.host);

    expect(calls).toHaveLength(2);
    expect(calls[0].key).toBeTruthy();
    expect(calls[1].key).toBe(calls[0].key);
    expect(calls[1].body.answer).toEqual({ quantity: 5000, max_lead_days: 15 });
    expect(typeof (calls[1].body.answer as Record<string, unknown>).quantity).toBe("number");
    expect(mounted.host.textContent).toContain("回答已保存，Agent 将从当前步骤继续");
  });

  it("uploads supplemental artifacts to Java before submitting their authorized IDs", async () => {
    const posts: Array<{ url: string; body: unknown }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      posts.push({ url, body: init?.body });
      if (url.endsWith("/artifacts")) {
        return jsonResponse({
          artifact_id: "jb" + "a".repeat(32),
          filename: "补充报价.xlsx",
          content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          size_bytes: 3,
          sha256: "b".repeat(64),
        });
      }
      return jsonResponse({ operation_id: "operation-2", status: "accepted" });
    }));
    const mounted = mount(<HumanInteractionPanel interaction={interaction({
      answer_schema: { type: "file_upload", label: "修正版报价" },
      question: "请上传修正版报价",
    })} />);
    roots.push(mounted.root);
    const input = mounted.host.querySelector<HTMLInputElement>('input[type="file"]')!;
    const file = new File(["xlsx"], "补充报价.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });

    Object.defineProperty(input, "files", { configurable: true, value: [file] });
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    await submit(mounted.host);

    expect(posts[0].url.endsWith("/api/procurement/interactions/interaction-1/artifacts")).toBe(true);
    expect(posts[0].body).toBeInstanceOf(FormData);
    const answer = JSON.parse(String(posts[1].body)) as { answer: string[]; artifact_ids: string[] };
    expect(answer.answer).toEqual(["jb" + "a".repeat(32)]);
    expect(answer.artifact_ids).toEqual(answer.answer);
  });

  it("requires a second click to cancel a waiting task", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(interaction({ status: "CANCELLED" })));
    vi.stubGlobal("fetch", fetchMock);
    const mounted = mount(<HumanInteractionPanel interaction={interaction()} />);
    roots.push(mounted.root);
    const cancel = () => [...mounted.host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent?.includes("取消"))!;

    await click(cancel());
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cancel().textContent).toContain("再次点击确认取消");
    await click(cancel());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0]).endsWith("/api/procurement/interactions/interaction-1/cancel")).toBe(true);
  });

  it("distinguishes saved recovery failures from applied answers without claiming completion", () => {
    const failedClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    failedClient.setQueryData(["procurement-operation", "operation-failed"], {
      operation_id: "operation-failed",
      status: "failed",
      last_error: "Agent 暂时不可用",
    });
    const failed = mount(<HumanInteractionPanel interaction={interaction({
      status: "ANSWERED",
      operation_id: "operation-failed",
    })} />, failedClient);
    roots.push(failed.root);
    expect(failed.host.textContent).toContain("回答已保存，Agent 暂未恢复");
    expect(failed.host.textContent).toContain("重新派发");

    const applied = mount(<HumanInteractionPanel interaction={interaction({ status: "APPLIED" })} />);
    roots.push(applied.root);
    expect(applied.host.textContent).toContain("回答已应用");
    expect(applied.host.textContent).toContain("继续处理后续步骤");
    expect(applied.host.textContent).not.toContain("已完成");
  });
});

describe("structured interaction conversation boundary", () => {
  it("hides the legacy free-text resume when a structured interaction owns the wait", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(["procurement-run", "run-1"], { status: "require_human" });
    queryClient.setQueryData(["procurement-messages", "run-1"], []);
    queryClient.setQueryData(["procurement-tools", "run-1"], []);
    const request = {
      id: "task-1",
      reference: "RFQ-1",
      title: "快递袋采购",
      category: "packaging",
      item_name: "快递袋",
      quantity: 5000,
      unit: "个",
      specifications: {},
      constraints: {},
      status: "waiting_human",
      session_id: "session-1",
      analysis_run_id: "run-1",
      attachments: [],
      quote_count: 2,
      unresolved_field_count: 0,
      quotes: [],
      comparison: null,
      decision: null,
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    } satisfies ProcurementRequest;

    const mounted = mount(<ProcurementConversation
      request={request}
      structuredInteractionActive
      onResume={async () => undefined}
      onRecover={async () => undefined}
      onOpenComparison={() => undefined}
    />, queryClient);
    roots.push(mounted.root);

    expect(mounted.host.querySelector('[aria-label="补充澄清信息"]')).toBeNull();
    expect(mounted.host.textContent).not.toContain("恢复采购 Agent");
  });
});
