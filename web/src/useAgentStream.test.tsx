import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  STREAM_NOTIFY_MS,
  STREAM_RETRY_MAX_MS,
  agentStreamUrl,
  streamRetryDelayMs,
  useAgentStream,
} from "./useAgentStream";

/** jsdom 无 EventSource：可编程假实现，记录实例并支持手动派发。 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((event: MessageEvent) => void)[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, data: unknown) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data: JSON.stringify(data) }));
    }
  }
}

type Hook = ReturnType<typeof useAgentStream>;

function mountHook(enabled: boolean, after: number): { hook: Hook; root: Root; host: HTMLElement } {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let current: Hook;
  function Probe() {
    current = useAgentStream(enabled, after);
    return null;
  }
  act(() => root.render(<Probe />));
  return {
    get hook() {
      return current;
    },
    root,
    host,
  };
}

function emitRunEvent(source: FakeEventSource, seq: number) {
  act(() => {
    source.emit("run_status", {
      event_id: `e${seq}`,
      global_seq: seq,
      run_seq: seq,
      session_id: "s",
      run_id: "r",
      type: "run_status",
      timestamp: "t",
      payload: {},
    });
  });
}

describe("agent stream cursor", () => {
  it("starts live without replaying the complete event history", () => {
    expect(agentStreamUrl(0)).toBe("/api/stream");
  });

  it("keeps an explicit positive cursor for replay and reconnect tests", () => {
    expect(agentStreamUrl(42)).toBe("/api/stream?after=42");
  });

  it("backs off exponentially and caps at the retry ceiling", () => {
    expect(streamRetryDelayMs(0)).toBe(1_000);
    expect(streamRetryDelayMs(1)).toBe(2_000);
    expect(streamRetryDelayMs(3)).toBe(8_000);
    expect(streamRetryDelayMs(10)).toBe(STREAM_RETRY_MAX_MS);
  });
});

describe("useAgentStream", () => {
  let root: Root | null = null;
  let host: HTMLElement | null = null;

  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    FakeEventSource.instances = [];
  });

  afterEach(() => {
    if (root && host) {
      act(() => root.unmount());
    }
    root = null;
    host = null;
    FakeEventSource.instances = [];
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("notifies at most once per throttle window with the newest event", () => {
    vi.useFakeTimers();
    const mounted = mountHook(true, 0);
    root = mounted.root;
    host = mounted.host;
    const source = FakeEventSource.instances[0];
    act(() => source.onopen?.());
    expect(mounted.hook.status).toBe("live");

    emitRunEvent(source, 1);
    emitRunEvent(source, 2);
    emitRunEvent(source, 3);
    // 节流窗口内：消费端尚未看到任何事件（不逐条重渲染）
    expect(mounted.hook.latestEvent).toBeNull();
    act(() => vi.advanceTimersByTime(STREAM_NOTIFY_MS + 1));
    expect(mounted.hook.latestEvent?.global_seq).toBe(3);

    // 乱序/重复 seq 被游标去重
    emitRunEvent(source, 3);
    act(() => vi.advanceTimersByTime(STREAM_NOTIFY_MS + 1));
    expect(mounted.hook.latestEvent?.global_seq).toBe(3);
  });

  it("reconnects with the last seen cursor after a connection error", () => {
    vi.useFakeTimers();
    const mounted = mountHook(true, 0);
    root = mounted.root;
    host = mounted.host;
    const first = FakeEventSource.instances[0];
    act(() => first.onopen?.());
    emitRunEvent(first, 7);

    act(() => first.onerror?.());
    expect(first.closed).toBe(true);
    expect(mounted.hook.status).toBe("error");
    act(() => vi.advanceTimersByTime(streamRetryDelayMs(1)));
    const second = FakeEventSource.instances[1];
    expect(second).toBeDefined();
    expect(second.url).toBe(agentStreamUrl(7)); // 游标续传，不重放全量历史

    // 重连后旧游标之前的事件被丢弃
    act(() => second.onopen?.());
    emitRunEvent(second, 7);
    act(() => vi.advanceTimersByTime(STREAM_NOTIFY_MS + 1));
    expect(mounted.hook.latestEvent?.global_seq).toBe(7);
    emitRunEvent(second, 8);
    act(() => vi.advanceTimersByTime(STREAM_NOTIFY_MS + 1));
    expect(mounted.hook.latestEvent?.global_seq).toBe(8);
  });

  it("stops the retry loop and closes the source on unmount", () => {
    vi.useFakeTimers();
    const mounted = mountHook(true, 0);
    root = mounted.root;
    host = mounted.host;
    const source = FakeEventSource.instances[0];
    act(() => source.onerror?.());
    const instancesBefore = FakeEventSource.instances.length;

    act(() => mounted.root.unmount());
    root = null;
    host = null;
    expect(source.closed).toBe(true);

    // 卸载后退避计时器不再建立新连接
    act(() => vi.advanceTimersByTime(STREAM_RETRY_MAX_MS * 2));
    expect(FakeEventSource.instances.length).toBe(instancesBefore);
  });

  it("reconnect() clears the pending backoff and reconnects immediately", () => {
    vi.useFakeTimers();
    const mounted = mountHook(true, 0);
    root = mounted.root;
    host = mounted.host;
    const first = FakeEventSource.instances[0];
    act(() => first.onerror?.());

    act(() => mounted.hook.reconnect());
    const second = FakeEventSource.instances[1];
    expect(second).toBeDefined();
    expect(second.closed).toBe(false);
  });
});
