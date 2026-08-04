import { useEffect, useState } from "react";

import type { EventRow } from "./api/client";

export const AGENT_EVENT_TYPES = [
  "run_started",
  "run_status",
  "run_completed",
  "run_failed",
  "run_cancelled",
  "run_interrupted",
  "text_delta",
  "tool_call_start",
  "tool_call_validated",
  "tool_execution_queued",
  "tool_execution_started",
  "tool_retry",
  "tool_execution_cancelled",
  "tool_execution_indeterminate",
  "tool_recovery_resolved",
  "tool_call_end",
  "tool_result",
  "approval_requested",
  "approval_resolved",
  "verification_started",
  "verification_result",
  "verification_feedback",
  "context_compacted",
  "provider_retry",
  "budget_warning",
  "error",
];

export type StreamStatus = "connecting" | "live" | "error" | "closed";

export function agentStreamUrl(after: number) {
  return after > 0 ? `/api/stream?after=${after}` : "/api/stream";
}

export function useAgentStream(enabled: boolean, after: number) {
  const [status, setStatus] = useState<StreamStatus>("closed");
  const [events, setEvents] = useState<EventRow[]>([]);

  useEffect(() => {
    if (!enabled) return;
    let lastSequence = after;
    const source = new EventSource(agentStreamUrl(after));
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

    return () => {
      source.close();
      setStatus("closed");
    };
  }, [after, enabled]);

  return { status, events };
}
