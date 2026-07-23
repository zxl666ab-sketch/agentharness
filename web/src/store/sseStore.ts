/** Small independent SSE store — no external state library. */

import type { EventRow } from "../api/client";

type Listener = () => void;

export type SseStatus = "connecting" | "live" | "error" | "closed";

class SseStore {
  status: SseStatus = "closed";
  lastSeq = 0;
  events: EventRow[] = [];
  private source: EventSource | null = null;
  private listeners = new Set<Listener>();

  subscribe = (fn: Listener) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  private emit() {
    for (const fn of this.listeners) fn();
  }

  connect(after = 0) {
    this.disconnect();
    this.status = "connecting";
    this.lastSeq = Math.max(this.lastSeq, after);
    this.emit();
    const url = `/api/stream?after=${after}`;
    const es = new EventSource(url);
    this.source = es;

    es.onopen = () => {
      this.status = "live";
      this.emit();
    };

    es.onerror = () => {
      this.status = "error";
      this.emit();
      // browser will auto-reconnect EventSource; we keep lastSeq for query param on manual re-connect
    };

    es.onmessage = (msg) => {
      this.handleRaw(msg);
    };

    // Named events from server: event: <type>
    // EventSource fires onmessage only for default; listen broadly via addEventListener is hard for dynamic types.
    // Server also sends data lines — use a catch-all by overriding.
    const orig = es.addEventListener.bind(es);
    // Fallback: parse any message event
    es.addEventListener("message", (msg) => this.handleRaw(msg as MessageEvent));

    // Also try common event types
    const types = [
      "run_started",
      "run_status",
      "run_completed",
      "run_failed",
      "run_cancelled",
      "run_interrupted",
      "model_turn_start",
      "model_turn_end",
      "text_delta",
      "tool_call_start",
      "tool_call_end",
      "tool_result",
      "approval_requested",
      "approval_resolved",
      "checkpoint",
      "span_start",
      "span_end",
      "child_run_started",
      "child_run_ended",
      "budget_warning",
      "redaction",
      "error",
      "heartbeat",
    ];
    for (const t of types) {
      es.addEventListener(t, (msg) => this.handleRaw(msg as MessageEvent));
    }

    void orig;
  }

  private handleRaw(msg: MessageEvent) {
    if (!msg.data || msg.data === "heartbeat") return;
    try {
      const data = JSON.parse(msg.data) as EventRow;
      if (typeof data.global_seq !== "number") return;
      if (data.global_seq <= this.lastSeq) return;
      this.lastSeq = data.global_seq;
      this.events = [...this.events, data].slice(-2000);
      this.status = "live";
      this.emit();
    } catch {
      // ignore heartbeat comments / parse errors
    }
  }

  disconnect() {
    if (this.source) {
      this.source.close();
      this.source = null;
    }
    this.status = "closed";
    this.emit();
  }

  eventsForRun(runId: string): EventRow[] {
    return this.events.filter((e) => e.run_id === runId);
  }

  clear() {
    this.events = [];
    this.lastSeq = 0;
    this.emit();
  }
}

export const sseStore = new SseStore();
