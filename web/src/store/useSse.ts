import { useCallback, useEffect, useSyncExternalStore } from "react";
import { sseStore, type SseStatus } from "./sseStore";
import type { EventRow } from "../api/client";

const EMPTY_EVENTS: EventRow[] = [];
const selectStatus = (store: typeof sseStore) => store.status;

export function useSseSelector<T>(
  selector: (store: typeof sseStore) => T,
  serverSnapshot: () => T
): T {
  const getSnapshot = useCallback(() => selector(sseStore), [selector]);
  return useSyncExternalStore(sseStore.subscribe, getSnapshot, serverSnapshot);
}

export function useSse(enabled = true, startAfter = 0) {
  const status = useSseSelector(selectStatus, closedStatus);

  useEffect(() => {
    if (!enabled) return;
    sseStore.connect(startAfter);
    return () => sseStore.disconnect();
  }, [enabled, startAfter]);

  return { status };
}

export function useSseEvents(): EventRow[] {
  return useSseSelector(selectEvents, emptyEvents);
}

export function useSseEventsForRun(runId: string | null): EventRow[] {
  const getSnapshot = useCallback(() => sseStore.eventsForRun(runId), [runId]);
  return useSyncExternalStore(sseStore.subscribe, getSnapshot, emptyEvents);
}

function selectEvents(store: typeof sseStore): EventRow[] {
  return store.events;
}

function closedStatus(): SseStatus {
  return "closed";
}

function emptyEvents(): EventRow[] {
  return EMPTY_EVENTS;
}
