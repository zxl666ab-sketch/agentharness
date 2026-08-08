import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "./api/client";
import { checkBackendCompatibility } from "./api/compatibility";
import { ProcurementWorkbench } from "./procurement/ProcurementWorkbench";
import "./procurement/procurement.css";

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
  onRetry,
}: {
  title: string;
  detail?: string;
  loading?: boolean;
  onRetry?: () => void;
}) {
  return (
    <main className="proc-gate">
      <span>{loading ? <LoaderCircle className="spin" size={22} /> : <AlertTriangle size={22} />}</span>
      <h1>{title}</h1>
      {detail ? <p>{detail}</p> : null}
      {onRetry ? (
        <button className="proc-button" type="button" onClick={onRetry}>重试</button>
      ) : null}
    </main>
  );
}

export default function App() {
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"
  );
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    retry: 1,
    // Backend down? Retry automatically so the UI recovers without a reload.
    refetchInterval: (query) => (query.state.status === "error" ? 5000 : false),
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  if (health.isPending) return <Gate title="正在连接采购服务" loading />;
  if (health.isError || !health.data) {
    return (
      <Gate
        title="无法连接采购服务"
        detail={message(health.error)}
        onRetry={() => void health.refetch()}
      />
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
  return (
    <ProcurementWorkbench
      theme={theme}
      backendVersion={health.data.backend_version}
      maxGlobalSeq={health.data.max_global_seq}
      onToggleTheme={() => setTheme((value) => (value === "light" ? "dark" : "light"))}
    />
  );
}
