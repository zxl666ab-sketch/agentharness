import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSseEvents } from "./useSse";

type Props = {
  selectedRunId: string | null;
};

/**
 * Translate live events into targeted query invalidations without making the
 * whole workbench subscribe to the event array.
 */
export function SseInvalidator({ selectedRunId }: Props) {
  const events = useSseEvents();
  const queryClient = useQueryClient();
  const lastHandledSeq = useRef(0);

  useEffect(() => {
    if (!events.length) return;
    const fresh = events.filter((event) => event.global_seq > lastHandledSeq.current);
    if (!fresh.length) return;
    lastHandledSeq.current = Math.max(...fresh.map((event) => event.global_seq));

    const touchesRuns = fresh.some((event) =>
      [
        "run_started",
        "run_status",
        "run_completed",
        "run_failed",
        "run_cancelled",
        "run_interrupted",
        "child_run_started",
        "child_run_ended",
      ].includes(event.type)
    );
    if (touchesRuns) {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      const terminalSessions = new Set(
        fresh
          .filter(
            (event) =>
              !event.parent_run_id &&
              ["run_completed", "run_failed", "run_cancelled", "run_interrupted"].includes(
                event.type
              )
          )
          .map((event) => event.session_id)
      );
      for (const sessionId of terminalSessions) {
        void queryClient.invalidateQueries({ queryKey: ["transcript", sessionId] });
      }
    }

    if (!selectedRunId) return;
    const selected = fresh.filter((event) => event.run_id === selectedRunId);
    if (!selected.length) return;

    if (
      selected.some((event) =>
        ["run_status", "run_completed", "run_failed", "run_cancelled", "run_interrupted", "budget_warning"].includes(
          event.type
        )
      )
    ) {
      void queryClient.invalidateQueries({ queryKey: ["run", selectedRunId] });
    }
    if (
      selected.some((event) =>
        ["tool_call_end", "tool_result", "model_turn_end", "run_completed", "run_failed"].includes(
          event.type
        )
      )
    ) {
      void queryClient.invalidateQueries({ queryKey: ["messages", selectedRunId] });
    }
    if (
      selected.some((event) =>
        ["approval_requested", "approval_resolved"].includes(event.type)
      )
    ) {
      void queryClient.invalidateQueries({ queryKey: ["approvals", selectedRunId] });
    }
    if (
      selected.some((event) =>
        ["child_run_started", "child_run_ended"].includes(event.type)
      )
    ) {
      void queryClient.invalidateQueries({ queryKey: ["tree", selectedRunId] });
    }
    if (selected.some((event) => event.type === "checkpoint")) {
      void queryClient.invalidateQueries({ queryKey: ["checkpoint", selectedRunId] });
    }
  }, [events, queryClient, selectedRunId]);

  return null;
}
