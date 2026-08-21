import { describe, expect, it } from "vitest";
import { cn } from "./utils";

describe("cn utility", () => {
  it("merges multiple class strings", () => {
    expect(cn("px-4", "py-2", "text-sm")).toBe("px-4 py-2 text-sm");
  });

  it("handles conditional class objects and falsy values", () => {
    const isHidden = false;
    const isActive = true;
    expect(
      cn("base-class", isHidden && "hidden", isActive && "active", null, undefined, "")
    ).toBe("base-class active");
  });

  it("resolves conflicting Tailwind utility classes properly via tailwind-merge", () => {
    expect(cn("px-2 py-1", "px-4")).toBe("py-1 px-4");
    expect(cn("text-red-500", "text-blue-500")).toBe("text-blue-500");
    expect(cn("bg-surface", "bg-surface-elevated")).toBe("bg-surface-elevated");
  });

  it("handles nested arrays and object dictionaries", () => {
    expect(
      cn(["btn", ["btn-primary", { "btn-disabled": false, "btn-active": true }]])
    ).toBe("btn btn-primary btn-active");
  });
});
