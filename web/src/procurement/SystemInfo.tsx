import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Cpu, Database, KeyRound, LoaderCircle, Server, Unplug } from "lucide-react";

import { procurementApi } from "./api";

const GATEWAY_STATE_LABELS: Record<string, { label: string; tone: string }> = {
  open: { label: "熔断中", tone: "danger" },
  half_open: { label: "熔断探测", tone: "warning" },
  degraded: { label: "降级中", tone: "warning" },
  closed: { label: "正常", tone: "success" },
  active: { label: "正常", tone: "success" },
};

export function SystemInfo() {
  const query = useQuery({
    queryKey: ["procurement-platform"],
    queryFn: procurementApi.platform,
    refetchInterval: 30_000,
  });
  const platform = query.data;
  const gatewayProviders = platform?.gateway?.providers || [];

  return (
    <section className="proc-main">
      <header className="proc-page-head">
        <div>
          <h1>系统信息</h1>
          <p>版本 / 组件状态 / 解析器 / 规则集 / 模型脱敏状态 / LLM 网关状态</p>
        </div>
      </header>
      <div className="proc-reports-body">
        {query.isPending ? (
          <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在读取系统信息…</div>
        ) : null}
        {query.isError ? (
          <section className="proc-empty-state compact" role="alert">
            <AlertTriangle size={26} />
            <h2>系统信息加载失败</h2>
            <p>{query.error instanceof Error ? query.error.message : "未知错误"}</p>
            <button className="proc-button secondary" type="button" onClick={() => void query.refetch()}>重新加载</button>
          </section>
        ) : null}
        {platform ? (
          <div className="proc-system-grid">
            <section className="proc-report-section">
              <header><div><Server size={15} /><h3>服务与版本</h3></div></header>
              <dl className="proc-facts-grid">
                <div><dt>服务</dt><dd>{platform.service}</dd></div>
                <div><dt>后端版本</dt><dd>{platform.backend_version}</dd></div>
                <div><dt>API Schema</dt><dd>{platform.api_schema_version}</dd></div>
                <div><dt>数据库</dt><dd>{String(platform.db.status || "—")}</dd></div>
              </dl>
            </section>

            <section className="proc-report-section">
              <header><div><Database size={15} /><h3>组件状态</h3></div></header>
              <dl className="proc-facts-grid">
                {Object.entries(platform.components).map(([key, value]) => (
                  <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>
                ))}
                <div><dt>AI 任务数</dt><dd>{String(platform.db.ai_tasks ?? "—")}</dd></div>
              </dl>
            </section>

            <section className="proc-report-section">
              <header><div><Cpu size={15} /><h3>解析器与规则集</h3></div></header>
              <h4>报价解析器</h4>
              <div className="proc-tag-row">
                {platform.parsers.quote_parser_versions.length
                  ? platform.parsers.quote_parser_versions.map((item) => <span className="proc-tag" key={item}>{item}</span>)
                  : <span className="proc-muted">暂无</span>}
              </div>
              <h4>比价规则集</h4>
              <div className="proc-tag-row">
                {platform.rulesets.comparison_rulesets.length
                  ? platform.rulesets.comparison_rulesets.map((item) => <span className="proc-tag" key={item}>{item}</span>)
                  : <span className="proc-muted">暂无</span>}
              </div>
            </section>

            <section className="proc-report-section">
              <header><div><KeyRound size={15} /><h3>模型配置（脱敏）</h3></div></header>
              <dl className="proc-facts-grid">
                <div><dt>Provider</dt><dd>{platform.model.provider}</dd></div>
                <div><dt>模型</dt><dd>{platform.model.model}</dd></div>
                <div><dt>API Key</dt><dd>{platform.model.api_key_configured ? `已配置${platform.model.api_key_preview ? `（${platform.model.api_key_preview}…）` : ""}` : "未配置"}</dd></div>
                <div><dt>推理强度</dt><dd>{platform.model.reasoning_effort}</dd></div>
              </dl>
              <p className="proc-eval-note">密钥只返回脱敏状态，不回显明文。</p>
            </section>

            <section className={`proc-report-section ${gatewayProviders.some((item) => item.state === "open" || item.state === "degraded" || item.state === "half_open") ? "gateway-attention" : ""}`}>
              <header><div><Unplug size={15} /><h3>LLM 网关（限流 / 熔断 / 降级）</h3></div></header>
              {gatewayProviders.length ? (
                <dl className="proc-facts-grid">
                  {gatewayProviders.map((provider) => {
                    const state = GATEWAY_STATE_LABELS[provider.state] || { label: provider.state, tone: "neutral" };
                    return (
                      <div key={provider.provider}>
                        <dt>{provider.provider}</dt>
                        <dd>
                          <span className={`proc-status ${state.tone}`}><i />{state.label}</span>
                          {provider.state === "open" && provider.remaining_open_s != null
                            ? <small>剩余 {Math.ceil(provider.remaining_open_s)}s</small>
                            : null}
                          {provider.stats ? <small>失败 {provider.stats.failures ?? 0} / 限流 {provider.stats.rate_limited ?? 0} / 降级 {provider.stats.degraded ?? 0}</small> : null}
                        </dd>
                      </div>
                    );
                  })}
                </dl>
              ) : (
                <p className="proc-muted">暂无网关状态（Agent 心跳未上报）</p>
              )}
              <p className="proc-eval-note">熔断/限流/降级事件来自 Python Agent，状态脱敏。</p>
            </section>

            <section className="proc-report-section">
              <header><div><Cpu size={15} /><h3>平台能力</h3></div></header>
              <div className="proc-tag-row">
                {platform.capabilities.map((item) => <span className="proc-tag" key={item}>{item}</span>)}
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </section>
  );
}
