import { describe, expect, it } from "vitest";

import { visibleViewOrDefault } from "./roles";

describe("demo role view routing", () => {
  it("keeps allowed views and redirects hidden views to the workbench", () => {
    expect(visibleViewOrDefault("approver", "reviews")).toBe("reviews");
    expect(visibleViewOrDefault("approver", "orders")).toBe("workbench");
    expect(visibleViewOrDefault("buyer", "system")).toBe("workbench");
    expect(visibleViewOrDefault("admin", "system")).toBe("system");
  });
});
