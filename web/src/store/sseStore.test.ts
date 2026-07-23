import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SseStore } from "./sseStore";
import type { EventRow } from "../api/client";

/** Minimal controllable EventSource stand-in for jsdom (which lacks one). */
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  readyState = 0;
  onopen: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  private typed = new Map<string, (ev: MessageEvent) => void>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: (ev: MessageEvent) => void) {
    this.typed.set(type, fn);
  }

  close() {
    this.readyState = 2;
  }

  open() {
    this.readyState = 1;
    this.onopen?.({});
  }

  /** Deliver a typed event exactly as the server would (id: <seq>, event: <type>). */
  deliver(row: EventRow) {
    const msg = { data: JSON.stringify(row) } as MessageEvent;
    const handler = this.typed.get(row.type);
    if (handler) handler(msg);
    else this.onmessage?.(msg);
  }

  fail() {
    this.onerror?.({});
  }
}

function makeEvent(seq: number, type = "run_started"): EventRow {
  return {
    schema_version: 1,
    event_id: `e${seq}`,
    global_seq: seq,
    run_seq: seq,
    session_id: "s1",
    root_run_id: "r1",
    run_id: "r1",
    type,
    timestamp: new Date(seq).toISOString(),
    payload: {},
  };
}

describe("sseStore", () => {
  let sseStore: SseStore;

  beforeEach(() => {
    vi.useFakeTimers();
    MockEventSource.instances = [];
    (globalThis as unknown as { EventSource: unknown }).EventSource =
      MockEventSource as unknown;
    sseStore = new SseStore();
  });

  afterEach(() => {
    sseStore.disconnect();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("batches an event burst into one animation-frame notification", () => {
    let frame: FrameRequestCallback | null = null;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frame = callback;
      return 1;
    });
    let notifications = 0;
    const unsubscribe = sseStore.subscribe(() => {
      notifications += 1;
    });
    const receive = (
      sseStore as unknown as { handleRaw: (message: MessageEvent) => void }
    ).handleRaw.bind(sseStore);

    for (let sequence = 1; sequence <= 200; sequence += 1) {
      receive(
        new MessageEvent("message", {
          data: JSON.stringify(makeEvent(sequence, "text_delta")),
        })
      );
    }

    expect(sseStore.events).toHaveLength(200);
    expect(notifications).toBe(0);
    expect(frame).not.toBeNull();
    (frame as FrameRequestCallback)(performance.now());
    expect(notifications).toBe(1);
    unsubscribe();
  });

  it("keeps unrelated run snapshots referentially stable", () => {
    const receive = (
      sseStore as unknown as { handleRaw: (message: MessageEvent) => void }
    ).handleRaw.bind(sseStore);
    receive(
      new MessageEvent("message", {
        data: JSON.stringify(makeEvent(1, "run_started")),
      })
    );
    const first = sseStore.eventsForRun("r1");
    receive(
      new MessageEvent("message", {
        data: JSON.stringify({ ...makeEvent(2, "text_delta"), run_id: "r2" }),
      })
    );

    expect(sseStore.eventsForRun("r1")).toBe(first);
    expect(sseStore.eventsForRun("r2")).toHaveLength(1);
  });

  it("dedupes redundant connect() calls at the same resume point", () => {
    sseStore.connect(10);
    sseStore.connect(10);
    sseStore.connect(10);
    // Only one underlying EventSource should be created.
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain("after=10");
  });

  it("rebuilds when the resume point changes", () => {
    sseStore.connect(10);
    sseStore.connect(25);
    expect(MockEventSource.instances).toHaveLength(2);
    expect(MockEventSource.instances[1].url).toContain("after=25");
  });

  it("does not emit duplicate run_started across a drop + reconnect", () => {
    const seen: number[] = [];
    const unsub = sseStore.subscribe(() => {
      const last = sseStore.events[sseStore.events.length - 1];
      if (last) seen.push(last.global_seq);
    });

    sseStore.connect(0);
    const first = MockEventSource.instances[0];
    first.open();
    first.deliver(makeEvent(1, "run_started"));
    first.deliver(makeEvent(2, "run_completed"));

    // Simulate a network drop; store schedules a reconnect after 1500ms.
    first.fail();
    vi.advanceTimersByTime(1600);

    const second = MockEventSource.instances[MockEventSource.instances.length - 1];
    expect(second).not.toBe(first);
    // Server replays from lastSeq: it re-sends seq 1 and 2 (inclusive replay window).
    second.open();
    second.deliver(makeEvent(1, "run_started"));
    second.deliver(makeEvent(2, "run_completed"));
    // New event after the reconnect.
    second.deliver(makeEvent(3, "run_started"));

    unsub();

    // Replayed seq 1/2 must be dropped by the lastSeq guard; only 1,2,3 ever stored once.
    const runStarted = sseStore.events.filter((e) => e.type === "run_started");
    expect(runStarted.map((e) => e.global_seq)).toEqual([1, 3]);
    // Resume URL used lastSeq, never after=0 (no history replay storm).
    expect(second.url).toContain("after=2");
  });
});
