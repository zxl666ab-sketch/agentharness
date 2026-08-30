import { afterEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";

import {
  ConversationResizer,
  CONV_DEFAULT_WIDTH,
  CONV_MAX_WIDTH,
  CONV_MIN_WIDTH,
} from "./ConversationResizer";

type Setup = {
  host: HTMLDivElement;
  root: Root;
  widths: number[];
  commits: number[];
  drags: boolean[];
  renderAt: (width: number) => React.ReactElement;
  rerender: (ui: React.ReactElement) => Promise<void>;
};

let mounted: Root | null = null;

async function setup(initialWidth = CONV_DEFAULT_WIDTH): Promise<Setup> {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  mounted = root;
  const state: Pick<Setup, "widths" | "commits" | "drags"> = { widths: [], commits: [], drags: [] };
  const render = (width: number) =>
    createElement(
      "div",
      { style: { position: "relative" } },
      createElement(ConversationResizer, {
        width,
        onChange: (value: number) => state.widths.push(value),
        onCommit: (value: number) => state.commits.push(value),
        onDraggingChange: (value: boolean) => state.drags.push(value),
      }),
    );
  await act(async () => { root.render(render(initialWidth)); });
  const renderAt = (width: number) => render(width);
  return {
    host,
    root,
    ...state,
    renderAt,
    rerender: async (ui: React.ReactElement) => { await act(async () => { root.render(ui); }); },
  };
}

const last = <T,>(list: T[]) => list[list.length - 1];

describe("ConversationResizer", () => {
  afterEach(async () => {
    const root = mounted;
    mounted = null;
    if (root) await act(async () => { root.unmount(); });
    document.body.innerHTML = "";
  });

  it("renders an accessible separator with clamped value", async () => {
    const ctx = await setup(CONV_MAX_WIDTH + 999);
    const el = ctx.host.querySelector<HTMLElement>('[role="separator"]')!;
    expect(el).toBeTruthy();
    expect(el.getAttribute("aria-orientation")).toBe("vertical");
    expect(el.getAttribute("aria-valuenow")).toBe(String(CONV_MAX_WIDTH));
  });

  it("adjusts with arrow keys, jumps with Home/End, and commits each step", async () => {
    const ctx = await setup(300);
    const el = ctx.host.querySelector<HTMLElement>('[role="separator"]')!;
    const fire = async (key: string, shift = false) => {
      await act(async () => { el.dispatchEvent(new KeyboardEvent("keydown", { key, shiftKey: shift, bubbles: true, cancelable: true })); });
    };
    await fire("ArrowRight");
    expect(last(ctx.commits)).toBe(316);
    await ctx.rerender(ctx.renderAt(last(ctx.commits)));
    await fire("ArrowLeft", true);
    expect(last(ctx.commits)).toBe(316 - 48);
    await ctx.rerender(ctx.renderAt(last(ctx.commits)));
    await fire("Home");
    expect(last(ctx.commits)).toBe(CONV_MIN_WIDTH);
    await ctx.rerender(ctx.renderAt(last(ctx.commits)));
    await fire("End");
    expect(last(ctx.commits)).toBe(CONV_MAX_WIDTH);
  });

  it("resets to the default width on double click", async () => {
    const ctx = await setup(460);
    const el = ctx.host.querySelector<HTMLElement>('[role="separator"]')!;
    await act(async () => { el.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })); });
    expect(last(ctx.commits)).toBe(CONV_DEFAULT_WIDTH);
  });

  it("tracks pointer drags and reports dragging state", async () => {
    const ctx = await setup(300);
    const el = ctx.host.querySelector<HTMLElement>('[data-testid="conversation-resizer"]')!;
    const rect = { left: 0, right: 500 } as DOMRect;
    vi.spyOn(el.parentElement as HTMLElement, "getBoundingClientRect").mockReturnValue(rect);
    if (!el.hasPointerCapture) el.hasPointerCapture = () => false;
    el.setPointerCapture = vi.fn();
    el.releasePointerCapture = vi.fn();

    const pointer = (type: string, clientX?: number) => {
      const event = new MouseEvent(type, { button: 0, bubbles: true, cancelable: true });
      Object.defineProperty(event, "pointerId", { value: 1 });
      if (clientX != null) Object.defineProperty(event, "clientX", { value: clientX });
      return event;
    };
    await act(async () => { el.dispatchEvent(pointer("pointerdown")); });
    expect(last(ctx.drags)).toBe(true);
    await act(async () => { el.dispatchEvent(pointer("pointermove", 260)); });
    // 宽 = 指针 x - 容器左缘：260 - 0 = 260
    await act(async () => { el.dispatchEvent(pointer("pointermove", 180)); });
    expect(ctx.widths).toEqual([260, CONV_MIN_WIDTH]);
    await act(async () => { el.dispatchEvent(pointer("pointerup")); });
    expect(last(ctx.drags)).toBe(false);
    // 提交的是拖到的最终宽度（下限 220）
    expect(last(ctx.commits)).toBe(CONV_MIN_WIDTH);
  });
});
