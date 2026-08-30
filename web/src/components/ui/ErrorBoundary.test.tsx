import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { ErrorBoundary } from "./ErrorBoundary";

function Boom(): never {
  throw new Error("模拟渲染崩溃");
}

describe("ErrorBoundary", () => {
  let host: HTMLDivElement;
  let root: Root | null = null;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    root = null;
    host?.remove();
    errorSpy?.mockRestore();
    vi.restoreAllMocks();
  });

  it("renders a recoverable fallback instead of unmounting the tree", async () => {
    host = document.createElement("div");
    document.body.append(host);
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    root = createRoot(host);
    await act(async () => {
      root!.render(
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>,
      );
    });

    expect(host.textContent).toContain("页面渲染出错");
    expect(host.textContent).toContain("模拟渲染崩溃");
    expect(host.textContent).toContain("尝试恢复");
    expect(host.textContent).toContain("刷新页面");

    // 点击「尝试恢复」会重置错误态并重试渲染（此处子组件仍抛错，但边界保持挂载）
    const recover = [...host.querySelectorAll("button")].find((b) => b.textContent === "尝试恢复")!;
    await act(async () => { recover.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(host.textContent).toContain("页面渲染出错");
  });

  it("renders children untouched when nothing throws", async () => {
    host = document.createElement("div");
    document.body.append(host);
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    root = createRoot(host);
    await act(async () => {
      root!.render(<ErrorBoundary><p>正常内容</p></ErrorBoundary>);
    });
    expect(host.textContent).toContain("正常内容");
    expect(host.textContent).not.toContain("页面渲染出错");
  });
});
