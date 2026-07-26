import { MessageSquareText, Plus, Search, X } from "lucide-react";
import { useMemo, useState } from "react";

import type { SessionRow } from "../api/client";
import { statusLabel } from "../viewModel";

type Props = {
  sessions: SessionRow[];
  selectedSessionId: string | null;
  onNew: () => void;
  onSelect: (sessionId: string, runId?: string | null) => void;
};

export function SessionSidebar({
  sessions,
  selectedSessionId,
  onNew,
  onSelect,
}: Props) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return sessions;
    return sessions.filter((session) =>
      (session.display_title || session.title || "未命名任务")
        .toLocaleLowerCase()
        .includes(normalized)
    );
  }, [query, sessions]);

  return (
    <aside id="session-sidebar" className="session-sidebar" aria-label="任务列表">
      <div className="sidebar-heading">
        <div className="sidebar-title">
          <span>Agent workspace</span>
          <h2>任务</h2>
        </div>
        <button
          className="new-task-button"
          type="button"
          aria-label="新建任务"
          onClick={onNew}
        >
          <Plus size={16} />
          <span>新建</span>
        </button>
      </div>

      <div className="session-search" role="search">
        <Search size={15} />
        <input
          aria-label="搜索任务"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索任务"
        />
        {query ? (
          <button type="button" aria-label="清除搜索" onClick={() => setQuery("")}>
            <X size={14} />
          </button>
        ) : null}
      </div>

      <div className="session-list-heading">
        <span>{query ? `${filtered.length} 个结果` : `${sessions.length} 个任务`}</span>
        <span>最近更新</span>
      </div>

      <div className="session-list" data-testid="session-list">
        {filtered.map((session) => {
          const title = session.display_title || session.title || "未命名任务";
          const selected = session.id === selectedSessionId;
          return (
            <button
              key={session.id}
              className={`session-item${selected ? " selected" : ""}`}
              aria-current={selected ? "page" : undefined}
              onClick={() => onSelect(session.id, session.latest_run_id)}
            >
              <span className={`status-dot ${session.latest_status || "pending"}`} />
              <span className="session-copy">
                <strong>{title}</strong>
                <small>
                  <MessageSquareText size={12} />
                  {session.latest_error || `${session.run_count || 0} 轮对话`}
                </small>
              </span>
              <span className={`session-status ${session.latest_status || "pending"}`}>
                {statusLabel(session.latest_status)}
              </span>
              <time dateTime={session.updated_at}>{formatRelativeTime(session.updated_at)}</time>
            </button>
          );
        })}
        {!filtered.length ? (
          <div className="empty-list">
            <MessageSquareText size={20} />
            <strong>{query ? "没有匹配的任务" : "还没有任务"}</strong>
            <span>{query ? "换个关键词试试" : "点击“新建”开始第一次运行"}</span>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function formatRelativeTime(value: string): string {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "";
  const difference = Math.max(0, Date.now() - timestamp);
  const minutes = Math.floor(difference / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(
    new Date(timestamp)
  );
}
