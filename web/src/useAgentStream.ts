import { useEffect, useState } from "react";

import type { EventRow } from "./api/client";

// Keep in sync with the backend EventType enum (src/agentharness/contracts.py).
export const AGENT_EVENT_TYPES = [
  "run_started",
  "run_status",
  "run_completed",
  "run_failed",
  "run_cancelled",
  "run_interrupted",
  "model_turn_start",
  "model_turn_end",
  "context_manifest",
  "context_compacted",
  "verification_started",
  "verification_result",
  "verification_feedback",
  "text_delta",
  "tool_call_start",
  "tool_call_validated",
  "tool_execution_queued",
  "tool_execution_started",
  "tool_retry",
  "tool_execution_cancelled",
  "tool_execution_indeterminate",
  "tool_recovery_resolved",
  "tool_stage_denied",
  "tool_call_duplicate",
  "human_action_injected",
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
  "provider_retry",
  "run_budget_stopped",
  "redaction",
  "heartbeat",
  "error",
];

export type StreamStatus = "connecting" | "live" | "error" | "closed";

export function useAgentStream(enabled: boolean, after: number) {
  const [status, setStatus] = useState<StreamStatus>("closed");
  const [events, setEvents] = useState<EventRow[]>([]);

  useEffect(() => {
    if (!enabled) return;
    let lastSequence = after;
    const source = new EventSource(`/api/stream?after=${after}`);
    setStatus("connecting");

    const receive = (message: MessageEvent) => {
      try {
        const event = JSON.parse(message.data) as EventRow;
        if (event.global_seq <= lastSequence) return;
        lastSequence = event.global_seq;
        setEvents((current) => [...current, event].slice(-1000));
        setStatus("live");
      } catch {
        // Heartbeats and malformed remote input never reach the view model.
      }
    };

    source.onopen = () => setStatus("live");
    source.onerror = () => setStatus("error");
    for (const type of AGENT_EVENT_TYPES) {
      source.addEventListener(type, receive as EventListener);
    }
    // Fallback: never silently drop an event type the backend adds before this
    // list is updated. global_seq dedup keeps it safe alongside the typed
    // listeners.
    source.onmessage = receive;

    return () => {
      source.close();
      setStatus("closed");
    };
  }, [after, enabled]);

  return { status, events };
}
