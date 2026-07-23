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
  private reconnectTimer: number | null = null;
  private intentionalClose = false;

  subscribe = (fn: Listener) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  private emit() {
    for (const fn of this.listeners) fn();
  }

  connect(after = 0) {
    this.disconnect(false);
    this.intentionalClose = false;
    this.status = "connecting";
    this.lastSeq = Math.max(this.lastSeq, after);
    this.emit();
    const url = `/api/stream?after=${this.lastSeq}`;
    const es = new EventSource(url);
    this.source = es;

    es.onopen = () => {
      this.status = "live";
      this.emit();
    };

    es.onerror = () => {
      if (this.intentionalClose) return;
      this.status = "error";
      this.emit();
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
      this.events = [...this.events, data].slice(-2000);
      this.status = "live";
      this.emit();
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
    if (markClosed) {
      this.status = "closed";
      this.emit();
    }
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
