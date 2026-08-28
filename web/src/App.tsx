import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, LoaderCircle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { api } from "./api/client";
import { checkBackendCompatibility } from "./api/compatibility";
import { ProcurementWorkbench } from "./procurement/ProcurementWorkbench";
import "./procurement/procurement.css";

export { eventLabel } from "./viewModel";

function message(error: unknown) {
  if (error instanceof Error && error.message === "Failed to fetch") {
    return "网络连接失败，请确认采购服务已启动";
  }
  return error instanceof Error ? error.message : String(error || "未知错误");
}

function Gate({
  title,
  detail,
  loading = false,
  children,
}: { title: string; detail?: string; loading?: boolean; children?: ReactNode }) {
  return (
    <main className="proc-gate">
      <span>{loading ? <LoaderCircle className="spin" size={22} /> : <AlertTriangle size={22} />}</span>
      <h1>{title}</h1>
      {detail ? <p>{detail}</p> : null}
      {children}
    </main>
  );
}

export default function App() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    // 与 index.html 内联脚本同键：优先用户显式选择，其次系统偏好
    try {
      const stored = localStorage.getItem("caijiatai.theme");
      if (stored === "dark" || stored === "light") return stored;
    } catch {
      /* localStorage 不可用时回退系统偏好 */
    }
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    retry: 1,
    // 尚未拿到健康数据（含失败重试期）时每 5s 自动重试；健康后停止，避免常态轮询
    refetchInterval: (query) => (query.state.data ? false : 5_000),
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("caijiatai.theme", theme);
    } catch {
      /* 隐私模式等场景下持久化失败可忽略 */
    }
  }, [theme]);

  if (health.isPending) return <Gate title="正在连接采购服务" loading />;
  if (health.isError || !health.data) {
    return (
      <Gate title="无法连接采购服务" detail={message(health.error)}>
        <button
          type="button"
          className="mt-4 px-4 py-2 rounded-lg bg-accent text-white text-sm font-semibold hover:bg-accent-hover transition-colors disabled:opacity-60"
          onClick={() => void health.refetch()}
          disabled={health.isFetching}
        >
          {health.isFetching ? "重试中…" : "重试连接"}
        </button>
      </Gate>
    );
  }
  const compatibility = checkBackendCompatibility(health.data);
  if (!compatibility.compatible) {
    return (
      <Gate
        title="网页与采购服务版本不一致"
        detail={`需要 ${compatibility.expected}，当前 ${compatibility.actual}`}
      />
    );
  }
  // LIVE-1：Java /api/health 的 status=ok 只代表采购服务进程存活；Agent 心跳
  // 过期（agent_available=false / agent_status.status="down"）时分析类任务会
  // 停滞，必须在 UI 上明示，而不是让徽章继续显示"服务在线"。
  const agentDown = health.data.agent_available === false
    || health.data.agent_status?.status === "down";
  return (
    <ProcurementWorkbench
      theme={theme}
      backendVersion={health.data.backend_version}
      agentDown={agentDown}
      onToggleTheme={() => setTheme((value) => (value === "light" ? "dark" : "light"))}
    />
  );
}
