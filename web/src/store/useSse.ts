import { useEffect, useState, useSyncExternalStore } from "react";
import { sseStore, type SseStatus } from "./sseStore";
import type { EventRow } from "../api/client";

export function useSse(enabled = true) {
  const status = useSyncExternalStore(
    sseStore.subscribe,
    () => sseStore.status,
    () => "closed" as SseStatus
  );
  const lastSeq = useSyncExternalStore(
    sseStore.subscribe,
    () => sseStore.lastSeq,
    () => 0
  );
  const events = useSyncExternalStore(
    sseStore.subscribe,
    () => sseStore.events,
    () => [] as EventRow[]
  );

  useEffect(() => {
    if (!enabled) return;
    sseStore.connect(0);
    return () => sseStore.disconnect();
  }, [enabled]);

  return { status, lastSeq, events };
}

export function useForceUpdate() {
  const [, set] = useState(0);
  return () => set((n) => n + 1);
}
