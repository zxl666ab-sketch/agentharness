import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, CheckCircle2, Clock3, Database, FlaskConical, Gauge, ShieldCheck } from "lucide-react";

import { api } from "../api/client";
import { RunReport } from "../components/RunReport";
import { procurementApi } from "./api";
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
  const evaluation = useQuery({
    queryKey: ["procurement-evaluation"],
    queryFn: procurementApi.evaluation,
    staleTime: 60_000,
  });
  const usage = runReport.data?.usage;
  const modelTurns = Number(usage?.model_turns || 0);
  const estimatedCost = usage?.estimated_cost_usd;
  const costStatus = usage?.cost_status;
  const costLabel = costStatus === "estimated" && typeof estimatedCost === "number"
    ? `$${estimatedCost.toFixed(4)}`
    : "成本未知";
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
          <div className="proc-runtime-report">
            <RunReport
              report={runReport.data || null}
              loading={runReport.isPending}
              error={runReport.isError ? String(runReport.error) : null}
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
