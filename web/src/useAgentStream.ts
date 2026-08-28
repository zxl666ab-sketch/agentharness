import { useCallback, useEffect, useRef, useState } from "react";

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
];

export type StreamStatus = "connecting" | "live" | "error" | "closed";

/** 事件缓冲上限：超出后丢弃最旧事件，内存占用恒定。 */
export const STREAM_EVENT_LIMIT = 1000;
/** 高频事件（text_delta 等）合并通知的节流窗口。 */
export const STREAM_NOTIFY_MS = 150;
/** 重连退避上限。 */
export const STREAM_RETRY_MAX_MS = 15_000;

export function agentStreamUrl(after: number) {
  return after > 0 ? `/api/stream?after=${after}` : "/api/stream";
}

/** 指数退避：1s/2s/4s/8s/15s 封顶。 */
export function streamRetryDelayMs(attempt: number) {
  return Math.min(STREAM_RETRY_MAX_MS, 1_000 * 2 ** Math.max(0, attempt));
}

export type AgentStream = {
  status: StreamStatus;
  /** 最近一条流事件（节流后快照）；消费端只读最后一条时不再承受每事件重渲染。 */
  latestEvent: EventRow | null;
  /** 断线后立即重建连接（清除退避等待）。 */
  reconnect: () => void;
};

/**
 * Agent SSE 流订阅。
 *
 * 性能契约：事件写入 ref 缓冲（截断到 STREAM_EVENT_LIMIT），按 STREAM_NOTIFY_MS
 * 节流后以 `latestEvent` 快照通知消费端，不再每条事件触发全树重渲染。
 * 重连契约：onerror 后手动 close，按内部 lastSequence 重建 `?after=` 游标
 * （服务端按 global_seq 单调续传），指数退避直到重新 live；卸载或
 * enabled=false 时停止并清理全部定时器。
 *
 * W-M1：AGENT_EVENT_TYPES 不含 "error"——按 SSE 规范，`event: error` 帧同样会
 * 先触发 EventSource.onerror；把它注册成普通事件只会让服务端的错误帧被伪装成
 * 断连引发重连风暴。真正的终态失败本就以 run_failed 事件送达。
 */
export function useAgentStream(enabled: boolean, after: number): AgentStream {
  const [status, setStatus] = useState<StreamStatus>("closed");
  const [latestEvent, setLatestEvent] = useState<EventRow | null>(null);
  const eventsRef = useRef<EventRow[]>([]);
  const dirtyRef = useRef(false);
  const notifyTimerRef = useRef<number | null>(null);
  const reconnectRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!enabled) {
      setStatus("closed");
      return;
    }
    let disposed = false;
    let attempt = 0;
    let source: EventSource | null = null;
    let retryTimer: number | null = null;
    let lastSequence = after;
    eventsRef.current = [];

    const flush = () => {
      notifyTimerRef.current = null;
      if (!dirtyRef.current) return;
      dirtyRef.current = false;
      const events = eventsRef.current;
      setLatestEvent(events.length ? events[events.length - 1] : null);
    };

    const scheduleNotify = () => {
      dirtyRef.current = true;
      if (notifyTimerRef.current !== null) return;
      notifyTimerRef.current = window.setTimeout(() => {
        notifyTimerRef.current = null;
        flush();
      }, STREAM_NOTIFY_MS);
    };

    const connect = () => {
      if (disposed) return;
      setStatus("connecting");
      const next = new EventSource(agentStreamUrl(lastSequence));
      source = next;
      next.onopen = () => {
        attempt = 0;
        setStatus("live");
      };
      next.onerror = () => {
        if (disposed || source !== next) return;
        // 手动接管重连：浏览器自动重连不带游标重建，退避后用 lastSequence 续传。
        next.close();
        source = null;
        setStatus("error");
        attempt += 1;
        retryTimer = window.setTimeout(() => {
          retryTimer = null;
          connect();
        }, streamRetryDelayMs(attempt));
      };
      const receive = (message: MessageEvent) => {
        try {
          const event = JSON.parse(message.data) as EventRow;
          if (event.global_seq <= lastSequence) return;
          lastSequence = event.global_seq;
          const events = [...eventsRef.current, event];
          eventsRef.current = events.length > STREAM_EVENT_LIMIT
            ? events.slice(events.length - STREAM_EVENT_LIMIT)
            : events;
          scheduleNotify();
          setStatus("live");
        } catch {
          // Heartbeats and malformed remote input never reach the view model.
        }
      };
      for (const type of AGENT_EVENT_TYPES) {
        next.addEventListener(type, receive as EventListener);
      }
    };

    const reconnectNow = () => {
      if (disposed) return;
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
        retryTimer = null;
      }
      if (source) {
        source.close();
        source = null;
      }
      attempt = 0;
      connect();
    };
    reconnectRef.current = reconnectNow;

    connect();

    return () => {
      disposed = true;
      reconnectRef.current = null;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      if (notifyTimerRef.current !== null) {
        window.clearTimeout(notifyTimerRef.current);
        notifyTimerRef.current = null;
      }
      dirtyRef.current = false;
      if (source) source.close();
      setStatus("closed");
    };
  }, [after, enabled]);

  const reconnect = useCallback(() => reconnectRef.current?.(), []);

  return { status, latestEvent, reconnect };
}
