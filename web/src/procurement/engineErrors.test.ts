import { describe, expect, it } from "vitest";

import { humanizeEngineError } from "./engineErrors";

describe("humanizeEngineError", () => {
  it("maps LLM connectivity failures to actionable Chinese guidance", () => {
    expect(humanizeEngineError("初始采购资料复核未进入预期状态：Connection error."))
      .toContain("连接模型服务失败");
    expect(humanizeEngineError("Connection error")).toContain("OPENAI_BASE_URL");
  });

  it("keeps the established mappings and passes unknown messages through", () => {
    expect(humanizeEngineError("stale_approval")).toContain("审批已失效");
    expect(humanizeEngineError("some brand new failure")).toBe("some brand new failure");
    expect(humanizeEngineError(null)).toBeNull();
    expect(humanizeEngineError(undefined)).toBeUndefined();
  });
});
