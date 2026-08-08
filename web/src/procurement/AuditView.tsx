import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, CheckCircle2, Clock3, Database, FlaskConical, Gauge, ShieldCheck, Wrench, Zap } from "lucide-react";

import { api } from "../api/client";
import { RunReport } from "../components/RunReport";
import { friendlyProcurementError, procurementApi } from "./api";
import type { ProcurementRequest } from "./types";

type Props = { request: ProcurementRequest };

function percent(value: number) {
  return `${(value * 100).toFixed(value === 1 ? 0 : 1)}%`;
}

export function AuditView({ request }: Props) {
  const runId = request.analysis_run_id || null;
  const runReport = useQuery({
    queryKey: ["run-report", runId],
    queryFn: () => api.report(runId!),
    enabled: !!runId,
  });
  const checkpointQuery = useQuery({
    queryKey: ["run-checkpoint", runId],
    queryFn: () => api.checkpoint(runId!),
    enabled: !!runId,
  });
  const timelineQuery = useQuery({
    queryKey: ["run-timeline", runId],
    queryFn: () => api.timeline(runId!),
    enabled: !!runId,
  });
  const evaluation = useQuery({
    queryKey: ["procurement-evaluation"],
    queryFn: procurementApi.evaluation,
    staleTime: 5 * 60_000,
  });
  const usage = runReport.data?.usage;
  const modelTurns = Number(usage?.model_turns || 0);
  const estimatedCost = usage?.estimated_cost_usd;
  const costStatus = usage?.cost_status;
  const costLabel = costStatus === "estimated" && typeof estimatedCost === "number"
    ? `$${estimatedCost.toFixed(4)}`
    : "成本未知";
  const auditReport = useQuery({
    queryKey: ["procurement-audit-report", request.id],
    queryFn: () => procurementApi.report(request.id),
    enabled: !!request.id,
  });
  const reviews = (auditReport.data?.audit_events || []).filter(
    (event) => event.type === "ai_review"
  );
  const checkpointStatus = String(checkpointQuery.data?.status || "");
  const checkpointLabel = checkpointStatus === "completed"
    ? "已完成并持久化"
    : checkpointStatus === "require_human"
      ? "等待人工恢复"
      : checkpointQuery.data
        ? "已持久化"
        : checkpointQuery.isError
          ? "读取失败"
          : "读取中";
  const approvalLabel = request.decision
    ? request.decision.decision === "no_award"
      ? "已确认无合格报价"
      : "供应商已批准"
    : "待审批";

  return (
    <div className="proc-audit-view">
      <section className="proc-eval-band">
        <header><div><FlaskConical size={17} /><h2>冻结真值集评测</h2></div><span>{evaluation.data?.dataset_label || "评测加载中"}</span></header>
        {evaluation.data ? (
          <>
            <div className="proc-eval-metrics">
              <span><small>辅助方案字段抽取</small><strong>{percent(evaluation.data.metrics.field_extraction.accuracy)}</strong></span>
              <span><small>物料匹配</small><strong>{percent(evaluation.data.metrics.item_matching.accuracy)}</strong></span>
              <span><small>金额计算</small><strong>{percent(evaluation.data.metrics.cost_calculation.accuracy)}</strong></span>
              <span><small>硬约束漏检 ↓</small><strong title="漏检率越低越好">{percent(evaluation.data.metrics.hard_constraint_miss.miss_rate)}</strong></span>
              <span><small>不合格报价错误入选</small><strong>{evaluation.data.metrics.incorrect_eligible_selection.count}</strong></span>
              <span><small>推荐准确率</small><strong>{percent(evaluation.data.metrics.recommendation_accuracy.rate)}</strong></span>
              <span><small>报价人工复核率</small><strong>{percent(evaluation.data.metrics.manual_review.quote_rate)}</strong></span>
            </div>
            <div className="proc-eval-table-wrap">
              <table className="proc-eval-table">
                <caption>冻结真值集评测：方案对比</caption>
                <thead>
                  <tr>
                    <th>方案</th><th>字段抽取</th><th>物料匹配</th><th>金额计算</th><th>硬约束漏检</th><th>错误入选</th><th>报价复核率</th><th>程序平均耗时</th><th>模型成本</th>
                  </tr>
                </thead>
                <tbody>
                  {([evaluation.data.approaches.deterministic_baseline, evaluation.data.approaches.agent_assisted]).map((approach) => (
                    <tr key={approach.label}>
                      <th>{approach.label}</th>
                      <td>{percent(approach.metrics.field_extraction.accuracy)}</td>
                      <td>{percent(approach.metrics.item_matching.accuracy)}</td>
                      <td>{percent(approach.metrics.cost_calculation.accuracy)}</td>
                      <td>{percent(approach.metrics.hard_constraint_miss.miss_rate)}</td>
                      <td>{approach.metrics.incorrect_eligible_selection.count}</td>
                      <td>{percent(approach.metrics.manual_review.quote_rate)}</td>
                      <td>{approach.metrics.processing.average_ms_per_quote} ms</td>
                      <td>${approach.metrics.model_usage.estimated_cost_usd.toFixed(2)}</td>
                    </tr>
                  ))}
                  <tr className="pending">
                    <th>{evaluation.data.approaches.human.label}</th>
                    <td colSpan={8}>{evaluation.data.approaches.human.note}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="proc-eval-proof">
              <span><CheckCircle2 size={14} />{evaluation.data.case_count} 份报价 / {evaluation.data.anomaly_coverage.count} 类异常 / 原始结果可复算</span>
              <code title={evaluation.data.truth_sha256}>{evaluation.data.truth_sha256.slice(0, 20)}</code>
            </div>
          </>
        ) : (
          <div className="proc-loading-line">
            {evaluation.isError ? <AlertTriangle size={15} /> : <Clock3 size={15} />}
            {evaluation.isError ? "冻结评测加载失败" : "正在运行冻结评测"}
            {evaluation.isError ? (
              <button
                type="button"
                className="proc-button"
                style={{ marginLeft: 10 }}
                onClick={() => evaluation.refetch()}
              >
                重试
              </button>
            ) : null}
          </div>
        )}
      </section>

      {runId ? (
        <>
          <section className="proc-runtime-links">
            <div><Activity size={17} /><span><small>分析运行 ID</small><code>{runId}</code></span></div>
            <div><Database size={17} /><span><small>持久化检查点</small><strong>{checkpointLabel}</strong></span></div>
            <div><ShieldCheck size={17} /><span><small>人工审批</small><strong>{approvalLabel}</strong></span></div>
            <div><Gauge size={17} /><span><small>模型回合 / 成本</small><strong>{modelTurns} · {costLabel}</strong></span></div>
          </section>
          {runReport.data?.versions ? (
            <section className="proc-version-strip" aria-label="本次运行配置">
              <span><small>Prompt</small><code title={runReport.data.versions.prompt_sha256 || ""}>{runReport.data.versions.prompt_version || "—"}</code></span>
              <span><small>工具 Schema</small><code title={runReport.data.versions.tool_schema_sha256 || ""}>{runReport.data.versions.tool_schema_version || "—"}</code></span>
              <span><small>解析器</small><strong>{runReport.data.versions.parser_version || "—"}</strong></span>
              <span><small>规则集</small><strong>{runReport.data.versions.ruleset_version || "—"}</strong></span>
              <span><small>模型</small><strong>{runReport.data.versions.model || "—"}</strong></span>
            </section>
          ) : null}
          <section className="proc-review-section">
            <header>
              <div><ShieldCheck size={17} /><h2>独立评审</h2></div>
              <span>{reviews.length ? `${reviews.length} 条记录` : "未启用或暂无记录"}</span>
            </header>
            {reviews.length ? (
              <ul className="proc-review-list">
                {reviews.map((review) => {
                  const verdict = String(review.payload.verdict || "error");
                  return (
                    <li key={review.id} className={`proc-review-item ${verdict}`}>
                      <strong>
                        {verdict === "pass" ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
                        {verdict === "pass" ? "通过" : verdict === "fail" ? "未通过" : "评审失败"}
                      </strong>
                      <p>{String(review.payload.reason || "（无理由）")}</p>
                      <small>
                        模型 {String(review.payload.model || "—")} · 策略 {String(review.payload.policy || "—")}
                        {review.payload.before_approval === true ? " · 审批前" : ""} · 审批 {String(review.payload.approval_id || "").slice(0, 8)} ·{" "}
                        {new Date(review.created_at).toLocaleString("zh-CN")}
                      </small>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="proc-review-empty">
                在「API / 模型配置」开启「审批前启用独立评审」并完成审批后，这里会显示第二个模型交叉验证的 pass/fail 与理由（不阻塞审批，仅记录证据）。
              </p>
            )}
          </section>
          <details className="proc-timeline-detail">
            <summary>
              <Activity size={15} />运行时间线
              <span>
                {timelineQuery.isPending
                  ? "加载中…"
                  : timelineQuery.data
                    ? `${timelineQuery.data.total} 条 · 工具 ${timelineQuery.data.tool_count}`
                    : "—"}
              </span>
            </summary>
            <div className="proc-timeline-body">
              {timelineQuery.isError ? (
                <p className="proc-inline-error" role="alert">时间线加载失败</p>
              ) : null}
              {timelineQuery.data ? (
                <>
                  {timelineQuery.data.truncated ? (
                    <p className="proc-timeline-note">仅显示最近 {timelineQuery.data.items.length} 条（共 {timelineQuery.data.total} 条）。</p>
                  ) : null}
                  <ul className="proc-timeline-list">
                    {timelineQuery.data.items.map((item) => (
                      <li key={`${item.kind}-${item.id}`} className={`proc-timeline-item ${item.kind}`}>
                        <span className="proc-timeline-icon">
                          {item.kind === "tool" ? <Wrench size={13} /> : <Zap size={13} />}
                        </span>
                        <span className="proc-timeline-main">
                          {item.kind === "tool" ? (
                            <>
                              <strong>{item.tool_name}</strong>
                              <small>
                                {item.status} · {item.duration_ms != null ? `${Math.round(item.duration_ms)} ms` : "—"}
                                {item.attempt_count ? ` · ${item.attempt_count} 次尝试` : ""}
                                {item.error_code ? ` · ${item.error_code}` : ""}
                              </small>
                            </>
                          ) : (
                            <>
                              <strong>{item.type}</strong>
                              {item.summary?.status ? <small>{String(item.summary.status)}</small> : null}
                              {item.summary?.tool_name ? <small>{String(item.summary.tool_name)}</small> : null}
                            </>
                          )}
                        </span>
                        <time>{item.at ? new Date(item.at).toLocaleTimeString("zh-CN") : "—"}</time>
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </div>
          </details>
          <div className="proc-runtime-report">
            <RunReport
              report={runReport.data || null}
              loading={runReport.isPending}
              error={runReport.isError ? friendlyProcurementError(String(runReport.error)) : null}
            />
          </div>
          {checkpointQuery.data ? (
            <details className="proc-checkpoint-detail">
              <summary>持久化检查点原始证据</summary>
              <pre>{JSON.stringify(checkpointQuery.data, null, 2)}</pre>
            </details>
          ) : null}
        </>
      ) : (
        <section className="proc-empty-state compact">
          <Activity size={28} />
          <h2>尚未生成分析运行</h2>
          <p>完成确定性比价后会关联分析运行、持久化检查点、人工审批与证据文件。</p>
        </section>
      )}
    </div>
  );
}
