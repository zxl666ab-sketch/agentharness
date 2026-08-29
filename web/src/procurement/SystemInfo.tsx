import { useQuery } from "@tanstack/react-query";
import { Cpu, Database, KeyRound, LoaderCircle, Server, Unplug } from "lucide-react";

import { procurementApi } from "./api";
import { CenterPage, EmptyState, ErrorState, PageHeader } from "../components/ui";

const GATEWAY_STATE_LABELS: Record<string, { label: string; tone: string }> = {
  open: { label: "熔断中", tone: "danger" },
  half_open: { label: "熔断探测", tone: "warning" },
  degraded: { label: "降级中", tone: "warning" },
  closed: { label: "正常", tone: "success" },
  active: { label: "正常", tone: "success" },
};

/** 技术字段中文化（键名仍保留英文原值 tooltip，方便排障）。 */
const COMPONENT_LABELS: Record<string, string> = {
  java_service: "Java 服务",
  agent_service: "Agent 服务",
  mysql: "数据库",
  db: "数据库",
  kafka: "消息队列 Kafka",
  redis: "缓存 Redis",
  agent_mode: "Agent 模式",
  scheduler: "调度器",
  dispatcher: "投递器",
  reconciler: "对账器",
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
    <CenterPage
      header={
        <PageHeader
          icon={<Server size={18} />}
          title="系统信息"
          subtitle="版本 / 组件状态 / 解析器 / 规则集 / 模型脱敏状态 / LLM 网关状态"
        />
      }
    >
      <div className="proc-reports-body">
        {query.isPending ? (
          <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在读取系统信息…</div>
        ) : null}
        {query.isError ? (
          <ErrorState
            title="系统信息加载失败"
            detail={query.error instanceof Error ? query.error.message : "未知错误"}
            onRetry={() => void query.refetch()}
          />
        ) : null}
        {platform ? (
          <div className="proc-system-grid">
            <section className="proc-report-block">
              <header><h3><Server size={15} /> 服务与版本</h3></header>
              <dl className="proc-facts-grid">
                <div><dt>服务</dt><dd>{platform.service}</dd></div>
                <div><dt>后端版本</dt><dd className="mono">{platform.backend_version}</dd></div>
                <div><dt>API Schema</dt><dd className="mono">{platform.api_schema_version}</dd></div>
                <div><dt>数据库</dt><dd>{String(platform.db.status || "—")}</dd></div>
              </dl>
            </section>

            <section className="proc-report-block">
              <header><h3><Database size={15} /> 组件状态</h3></header>
              <dl className="proc-facts-grid">
                {Object.entries(platform.components).map(([key, value]) => (
                  <div key={key}><dt title={key}>{COMPONENT_LABELS[key] || key}</dt><dd>{String(value)}</dd></div>
                ))}
                <div><dt>AI 任务数</dt><dd className="tnum">{String(platform.db.ai_tasks ?? "—")}</dd></div>
              </dl>
            </section>

            <section className="proc-report-block">
              <header><h3><Cpu size={15} /> 解析器与规则集</h3></header>
              <h4>报价解析器</h4>
              <div className="proc-tag-row">
                {platform.parsers.quote_parser_versions.length
                  ? platform.parsers.quote_parser_versions.map((item) => <span className="proc-tag mono" key={item}>{item}</span>)
                  : <EmptyState variant="inline" title="暂无解析器信息" />}
              </div>
              <h4>比价规则集</h4>
              <div className="proc-tag-row">
                {platform.rulesets.comparison_rulesets.length
                  ? platform.rulesets.comparison_rulesets.map((item) => <span className="proc-tag mono" key={item}>{item}</span>)
                  : <EmptyState variant="inline" title="暂无规则集信息" />}
              </div>
            </section>

            <section className="proc-report-block">
              <header><h3><KeyRound size={15} /> 模型配置（脱敏）</h3></header>
              <dl className="proc-facts-grid">
                <div><dt>Provider</dt><dd>{platform.model.provider}</dd></div>
                <div><dt>模型</dt><dd className="mono">{platform.model.model}</dd></div>
                <div><dt>API Key</dt><dd>{platform.model.api_key_configured ? `已配置${platform.model.api_key_preview ? `（${platform.model.api_key_preview}…）` : ""}` : "未配置"}</dd></div>
                <div><dt>推理强度</dt><dd>{platform.model.reasoning_effort}</dd></div>
              </dl>
              <p className="proc-eval-note">密钥只返回脱敏状态，不回显明文。</p>
            </section>

            <section className={`proc-report-block ${gatewayProviders.some((item) => item.state === "open" || item.state === "degraded" || item.state === "half_open") ? "gateway-attention" : ""}`}>
              <header><h3><Unplug size={15} /> LLM 网关（限流 / 熔断 / 降级）</h3></header>
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

            <section className="proc-report-block">
              <header><h3><Cpu size={15} /> 平台能力</h3></header>
              <div className="proc-tag-row">
                {platform.capabilities.map((item) => <span className="proc-tag" key={item}>{item}</span>)}
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </CenterPage>
  );
}
