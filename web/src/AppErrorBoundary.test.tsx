import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppErrorBoundary } from "./AppErrorBoundary";

function Bomb(): never {
  throw new Error("boom");
}

function Stable(): JSX.Element {
  return <div>stable content</div>;
}

describe("AppErrorBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("渲染子内容", () => {
    const container = document.createElement("div");
    let root!: Root;
    act(() => {
      root = createRoot(container);
      root.render(
        <AppErrorBoundary>
          <Stable />
        </AppErrorBoundary>
      );
    });
    expect(container.innerHTML).toContain("stable content");
    act(() => root.unmount());
  });

  it("子组件抛错时显示兜底而不是崩溃", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const container = document.createElement("div");
    let root!: Root;
    act(() => {
      root = createRoot(container);
      root.render(
        <AppErrorBoundary>
          <Bomb />
        </AppErrorBoundary>
      );
    });
    expect(container.innerHTML).toContain("页面显示失败");
    expect(errorSpy).toHaveBeenCalled();
    act(() => root.unmount());
  });
});
