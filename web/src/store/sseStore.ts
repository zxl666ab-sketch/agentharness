/** Small independent SSE store — no external state library. */

import type { EventRow } from "../api/client";

type Listener = () => void;

export type SseStatus = "connecting" | "live" | "error" | "closed";

export class SseStore {
  status: SseStatus = "closed";
  lastSeq = 0;
  events: EventRow[] = [];
  private source: EventSource | null = null;
  private listeners = new Set<Listener>();
  private reconnectTimer: number | null = null;
  private intentionalClose = false;
  // Requested resume point of the live connection; used to dedupe redundant connect() calls.
  private connectedAfter: number | null = null;
  private pendingEmit = false;
  private runEventSnapshots = new Map<string, EventRow[]>();

  subscribe = (fn: Listener) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  private emit() {
    for (const fn of this.listeners) fn();
  }

  private scheduleEmit() {
    if (typeof globalThis.requestAnimationFrame !== "function") {
      this.emit();
      return;
    }
    if (this.pendingEmit) return;
    this.pendingEmit = true;
    globalThis.requestAnimationFrame(() => {
      this.pendingEmit = false;
      this.emit();
    });
  }

  connect(after = 0) {
    // Dedupe: an active (connecting/live) stream at the same resume point needs no rebuild.
    // Prevents StrictMode double-invoke and repeated connect() from restarting the stream
    // and re-replaying history.
    if (
      this.source &&
      this.connectedAfter === after &&
      (this.status === "connecting" || this.status === "live")
    ) {
      return;
    }
    this.disconnect(false);
    this.intentionalClose = false;
    this.connectedAfter = after;
    this.status = "connecting";
    this.lastSeq = Math.max(this.lastSeq, after);
    this.scheduleEmit();
    const url = `/api/stream?after=${this.lastSeq}`;
    const es = new EventSource(url);
    this.source = es;

    es.onopen = () => {
      this.status = "live";
      this.scheduleEmit();
    };

    es.onerror = () => {
      if (this.intentionalClose) return;
      this.status = "error";
      this.scheduleEmit();
      // Close broken stream and resume from lastSeq (avoids after=0 replay storms).
      try {
        es.close();
      } catch {
        /* ignore */
      }
      if (this.source === es) this.source = null;
      if (this.reconnectTimer != null) window.clearTimeout(this.reconnectTimer);
      const resumeFrom = this.lastSeq;
      this.reconnectTimer = window.setTimeout(() => {
        this.reconnectTimer = null;
        if (!this.intentionalClose) this.connect(resumeFrom);
      }, 1500);
    };

    es.onmessage = (msg) => {
      this.handleRaw(msg);
    };

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
  }

  private handleRaw(msg: MessageEvent) {
    if (!msg.data || msg.data === "heartbeat") return;
    try {
      const data = JSON.parse(msg.data) as EventRow;
      if (typeof data.global_seq !== "number") return;
      if (data.global_seq <= this.lastSeq) return;
      this.lastSeq = data.global_seq;
      const evicted = this.events.length >= 2000 ? this.events[0] : undefined;
      this.events = [...this.events, data].slice(-2000);
      this.runEventSnapshots.delete(data.run_id);
      if (evicted) this.runEventSnapshots.delete(evicted.run_id);
      this.status = "live";
      this.scheduleEmit();
    } catch {
      // ignore heartbeat comments / parse errors
    }
  }

  disconnect(markClosed = true) {
    this.intentionalClose = true;
    if (this.reconnectTimer != null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.source) {
      this.source.close();
      this.source = null;
    }
    this.connectedAfter = null;
    if (markClosed) {
      this.status = "closed";
      this.scheduleEmit();
    }
  }

  eventsForRun(runId: string | null): EventRow[] {
    if (!runId) return EMPTY_EVENTS;
    const cached = this.runEventSnapshots.get(runId);
    if (cached) return cached;
    const events = this.events.filter((event) => event.run_id === runId);
    this.runEventSnapshots.set(runId, events);
    return events;
  }

  clear() {
    this.events = [];
    this.lastSeq = 0;
    this.runEventSnapshots.clear();
    this.scheduleEmit();
  }
}

const EMPTY_EVENTS: EventRow[] = [];

export const sseStore = new SseStore();
