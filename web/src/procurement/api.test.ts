import { afterEach, describe, expect, it, vi } from "vitest";

import { procurementApi } from "./api";

/**
 * 回归：DELETE /suppliers/{id} 按 OpenAPI 契约返回 204 空响应体。
 * 此前 requestJson 无条件 response.json()，空 body 抛
 * "Unexpected end of JSON input"——删除成功却被 UI 报成失败、列表不刷新。
 */
describe("procurement api requestJson empty-body handling", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resolves 204 with empty body without a JSON parse error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    await expect(procurementApi.deleteSupplier("sup-1")).resolves.toBeUndefined();
  });

  it("resolves 200 with empty body (defensive) without a JSON parse error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 200 })));
    await expect(procurementApi.deleteSupplier("sup-1")).resolves.toBeUndefined();
  });

  it("still parses a real JSON success body", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ deleted: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));
    await expect(procurementApi.deleteSupplier("sup-1")).resolves.toEqual({ deleted: true });
  });

  it("keeps surfacing server errors in Chinese with field_errors mapping", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      code: "validation_failed",
      message: "请求字段校验失败",
      field_errors: [{ field: "constraints.maxLeadDays", code: "Min", message: "must be greater than 0" }],
    }), { status: 422, headers: { "Content-Type": "application/json" } })));
    await expect(procurementApi.deleteSupplier("sup-1")).rejects.toThrow("最长交期");
  });
});
