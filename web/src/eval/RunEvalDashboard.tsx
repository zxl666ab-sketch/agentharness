import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  FileSearch,
  GitCompareArrows,
  Link2,
  LockKeyhole,
  RotateCcw,
  Route,
  Scale,
  ShieldCheck,
} from "lucide-react";
import type {
  DiagnosisReportRow,
  EvidenceRefRow,
  EvaluationCheckRow,
  EvaluationReportRow,
  JudgeEvaluationRow,
  RunEvaluationDetail,
  RunRow,
  TraceSpanRow,
  TranscriptTurn,
} from "../api/client";

type Dimension = {
  score?: number;
  reason?: string;
  applicable?: boolean;
};

export type EvaluationPayload = {
  schema_version?: number;
  passed?: boolean;
  score?: number | null;
  mode?: "deterministic" | "ai";
  evaluation_mode?: "scored" | "health_only" | "unscored";
  grader?: string;
  graded_at?: string;
  reasons?: string[];
  dimensions?: Record<string, Dimension>;
  confidence?: number;
  variance?: number;
  consistency?: number;
  judge_status?: string;
  failure_category?: string;
  evidence?: Array<string | EvidenceRefRow>;
  improvements?: string[];
  hard_safety_violation?: boolean;
  judge_provider?: string;
  judge_model?: string | null;
};

type Props = {
  run: RunRow | null;
  evaluation: EvaluationPayload | null;
  detail?: RunEvaluationDetail | null;
  turn?: TranscriptTurn | null;
  loading?: boolean;
  error?: string | null;
  onBack: () => void;
  onRegrade?: () => void;
  onEvidenceSelect?: (evidence: EvidenceRefRow) => void;
};

type Tab = "overview" | "trajectory" | "diagnosis" | "judge" | "regression";

const TABS: Array<{ id: Tab; label: string; icon: React.ReactNode }> = [
  { id: "overview", label: "总览", icon: <BarChart3 size={15} /> },
  { id: "trajectory", label: "轨迹", icon: <Route size={15} /> },
  { id: "diagnosis", label: "根因", icon: <FileSearch size={15} /> },
  { id: "judge", label: "裁判模型", icon: <Scale size={15} /> },
  { id: "regression", label: "回归", icon: <GitCompareArrows size={15} /> },
];

const GROUPS = [
  { title: "结果质量", weight: "60%", keys: ["task_completion", "correctness", "completeness"] },
  { title: "执行过程", weight: "25%", keys: ["planning_recovery", "tool_use", "execution_verification"] },
  { title: "系统与体验", weight: "15%", keys: ["efficiency", "safety_control", "user_experience"] },
];

export function RunEvalDashboard({
  run,
  evaluation,
  detail = null,
  turn,
  loading = false,
  error = null,
  onBack,
  onRegrade,
  onEvidenceSelect,
}: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  useEffect(() => setTab("overview"), [run?.id]);
  const report = useMemo(
    () => detail?.report || legacyReport(run, evaluation),
    [detail?.report, evaluation, run]
  );
  const judge = detail?.judge || legacyJudge(evaluation);

  if (loading && !report) {
    return <EmptyState title="正在读取评测" detail="加载规范化轨迹与评测报告" onBack={onBack} />;
  }
  if (!run || !report) {
    return (
      <EmptyState
        title={error ? "评测数据读取失败" : "还没有可展示的评测"}
        detail={error || turn?.user_content || run?.user_summary || "选择一个已结束的对话轮次，然后开始评测。"}
        onBack={onBack}
      />
    );
  }

  const scored = report.mode === "scored" && typeof report.score === "number";
  const score = scored ? Math.round(report.score! * 100) : null;
  const passed = report.passed === true;
  const unresolved = report.passed == null;
  const title = turn?.user_content || run.user_summary || "未命名任务";
  const failure = firstFailure(report.checks);

  return (
    <article className="run-eval-dashboard" data-testid="run-eval-dashboard">
      <header className="run-eval-heading">
        <div className="eval-heading-actions">
          <button type="button" className="eval-back" onClick={onBack}>
            <ArrowLeft size={15} /> 返回检查器
          </button>
          {onRegrade && (
            <button type="button" className="eval-back" onClick={onRegrade}>
              <RotateCcw size={15} /> 重新评测
            </button>
          )}
        </div>
        <div className="eval-heading-copy">
          <span>轨迹原生评测</span>
          <h2>{title}</h2>
          <p>
            运行 {run.id.slice(0, 12)} · 报告 {report.report_id.slice(0, 12)} · 策略版本 {report.policy_version}
            {report.evaluated_at ? ` · ${formatDate(report.evaluated_at)}` : ""}
          </p>
        </div>
      </header>

      <section className={`eval-score-band ${passed ? "passed" : unresolved ? "neutral" : "failed"}`}>
        <div className={`eval-score-number ${scored ? "" : "unscored"}`} data-testid="evaluation-score">
          {scored ? <><strong>{score}</strong><span>/ 100</span></> : (
            <><strong>{report.mode === "health_only" ? "健康检查" : "未评分"}</strong><span>{modeLabel(report.mode)}</span></>
          )}
        </div>
        <div className="eval-verdict">
          {unresolved ? <CircleHelp size={22} /> : passed ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} />}
          <div>
            <strong>{unresolved ? "未评分" : passed ? "通过" : "未通过"}</strong>
            <span>{verdictText(report, score)}</span>
          </div>
        </div>
        <Metric label="检查结果" value={`${report.passed_count} 通过 · ${report.failed_count} 失败`} />
        <Metric label="未配置" value={String(report.not_configured_count)} />
        <Metric label="主要归因" value={failureLabel(detail?.diagnosis?.root_cause || failure?.failure_category)} />
      </section>

      <nav className="eval-tabs" role="tablist" aria-label="评测视图">
        {TABS.map((item) => (
          <button
            type="button"
            key={item.id}
            className={tab === item.id ? "active" : ""}
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
            data-testid={`eval-tab-${item.id}`}
          >
            {item.icon}{item.label}
          </button>
        ))}
      </nav>

      <div className="eval-tab-panel" role="tabpanel" data-testid={`eval-view-${tab}`}>
        {tab === "overview" && (
          <Overview
            report={report}
            judge={judge}
            evaluation={evaluation}
            turn={turn}
            onEvidenceSelect={onEvidenceSelect}
          />
        )}
        {tab === "trajectory" && (
          <TrajectoryView
            report={report}
            detail={detail}
            onEvidenceSelect={onEvidenceSelect}
          />
        )}
        {tab === "diagnosis" && (
          <DiagnosisView diagnosis={detail?.diagnosis || null} onEvidenceSelect={onEvidenceSelect} />
        )}
        {tab === "judge" && <JudgeView judge={judge} />}
        {tab === "regression" && <RegressionView regression={detail?.regression || {}} />}
      </div>
    </article>
  );
}

function Overview({
  report,
  judge,
  evaluation,
  turn,
  onEvidenceSelect,
}: {
  report: EvaluationReportRow;
  judge: JudgeEvaluationRow;
  evaluation: EvaluationPayload | null;
  turn?: TranscriptTurn | null;
  onEvidenceSelect?: (evidence: EvidenceRefRow) => void;
}) {
  const dimensions = judge.dimensions || evaluation?.dimensions || {};
  const failures = report.checks.filter((check) => check.status === "failed" || check.status === "error");
  return (
    <div className="eval-overview-layout">
      <main className="eval-primary-column">
        <SectionHeading eyebrow="评测契约" title="确定性检查" meta={`${report.checks.length} 项`} />
        {report.mode === "health_only" && (
          <section className="eval-deterministic-note" data-testid="deterministic-explanation">
            <ShieldCheck size={24} aria-hidden="true" />
            <div>
              <strong>本次仅检查运行健康</strong>
              <p>未配置质量断言，因此不生成 100 分，也不评价回答的正确性与表达质量。</p>
            </div>
          </section>
        )}
        <div className="eval-check-list">
          {report.checks.map((check) => (
            <CheckRow key={check.id} check={check} onEvidenceSelect={onEvidenceSelect} />
          ))}
        </div>
        {!report.checks.length && <EmptyLine text="这份历史报告没有结构化 CheckResult。" />}

        {Object.keys(dimensions).length > 0 && (
          <section className="eval-dimensions">
            <SectionHeading eyebrow="语义裁判" title="结果、过程、系统三层评测" />
            {GROUPS.map((group) => (
              <section className="eval-dimension-group" key={group.title}>
                <header><strong>{group.title}</strong><span>{group.weight}</span></header>
                {group.keys.map((key) => <DimensionRow key={key} name={key} dimension={dimensions[key]} />)}
              </section>
            ))}
          </section>
        )}
      </main>
      <aside className="eval-secondary-column">
        <AuditSection title="主要失败">
          {failures.length ? (
            <ul>{failures.map((check) => <li key={check.id}>{check.message ? localizedDiagnosticText(check.message) : failureLabel(check.failure_category)}</li>)}</ul>
          ) : <p className="audit-ok">没有失败的确定性检查。</p>}
        </AuditSection>
        <AuditSection title="裁判模型状态">
          <StatusLine status={judge.status} />
          <p>{judgeStatusText(judge.status)}</p>
        </AuditSection>
        {!!evaluation?.evidence?.length && (
          <AuditSection title="判分证据">
            <div className="evidence-list">
              {evaluation.evidence.map((item, index) => typeof item === "string"
                ? <p key={`${item}-${index}`}>{item}</p>
                : <EvidenceButton key={index} evidence={item} onSelect={onEvidenceSelect} />)}
            </div>
          </AuditSection>
        )}
        {!!evaluation?.improvements?.length && (
          <AuditSection title="改进建议">
            <ol>{evaluation.improvements.map((item) => <li key={item}>{item}</li>)}</ol>
          </AuditSection>
        )}
        <AuditSection title="评测标识">
          <IdLine label="报告" value={report.report_id} />
          <IdLine label="轨迹" value={report.trace_id} />
          <IdLine label="策略" value={`${report.policy_id}@${report.policy_version}`} />
        </AuditSection>
        {turn?.assistant_content && (
          <AuditSection title="本轮回答"><blockquote>{turn.assistant_content}</blockquote></AuditSection>
        )}
      </aside>
    </div>
  );
}

function TrajectoryView({
  report,
  detail,
  onEvidenceSelect,
}: {
  report: EvaluationReportRow;
  detail: RunEvaluationDetail | null;
  onEvidenceSelect?: (evidence: EvidenceRefRow) => void;
}) {
  const sequenceCheck = report.checks.find((check) => check.id === "trajectory.sequence");
  const expected = asRecord(sequenceCheck?.expected);
  const expectedTools = stringList(expected.tools);
  const actualTools = Array.isArray(sequenceCheck?.actual)
    ? stringList(sequenceCheck?.actual)
    : (detail?.trace.spans || []).filter((span) => span.kind === "tool").map((span) => span.tool_name || span.name);
  const matchMode = typeof expected.mode === "string" ? matchModeLabel(expected.mode) : "未配置";
  const divergenceId = report.first_divergence?.span_id;
  return (
    <div className="eval-wide-panel">
      <SectionHeading eyebrow="轨迹评测器" title="预期与实际轨迹" meta={`匹配模式 · ${matchMode}`} />
      <div className="trajectory-compare">
        <TrajectoryLane title="预期轨迹" items={expectedTools} empty="未配置工具序列" />
        <TrajectoryLane title="实际轨迹" items={actualTools} empty="没有工具调用" />
      </div>
      <section className={`divergence-band ${report.first_divergence ? "failed" : "passed"}`}>
        <div>
          <span>首次偏离</span>
          <strong>{report.first_divergence ? `序号 ${report.first_divergence.sequence ?? "-"}` : "没有检测到偏离"}</strong>
          <p>{report.first_divergence?.excerpt || (sequenceCheck?.message ? localizedDiagnosticText(sequenceCheck.message) : report.first_divergence ? "轨迹在此处首次偏离预期工具序列。" : "实际轨迹符合当前策略。")}</p>
        </div>
        {report.first_divergence && <EvidenceButton evidence={report.first_divergence} onSelect={onEvidenceSelect} />}
      </section>
      <SectionHeading eyebrow="规范化轨迹" title="执行跨度轨迹" meta={`${detail?.trace.spans.length || 0} 个跨度`} />
      <div className="span-ledger">
        {(detail?.trace.spans || []).map((span) => (
          <button
            type="button"
            key={span.span_id}
            className={span.span_id === divergenceId ? "divergent" : ""}
            onClick={() => onEvidenceSelect?.({
              run_id: detail?.run_id,
              span_id: span.span_id,
              event_id: span.event_ids?.[0],
              sequence: span.sequence_start,
              source: "trace_span",
            })}
          >
            <span className="span-sequence">#{span.sequence_start}</span>
            <span><strong>{spanDisplayName(span)}</strong><small>{spanKindLabel(span.kind)} · {spanStatusLabel(span.status)}</small></span>
            <code>{span.span_id.slice(0, 12)}</code>
            <ChevronRight size={14} />
          </button>
        ))}
        {!detail?.trace.spans.length && <EmptyLine text="这份轨迹没有可展示的执行跨度。" />}
      </div>
    </div>
  );
}

function DiagnosisView({
  diagnosis,
  onEvidenceSelect,
}: {
  diagnosis: DiagnosisReportRow | null;
  onEvidenceSelect?: (evidence: EvidenceRefRow) => void;
}) {
  if (!diagnosis) {
    return <CenteredState icon={<CheckCircle2 size={24} />} title="没有根因报告" detail="当前评测未失败，或历史数据尚未生成根因诊断报告。" />;
  }
  return (
    <div className="eval-diagnosis-layout">
      <main className="eval-primary-column">
        <SectionHeading eyebrow="根因诊断" title={failureLabel(diagnosis.root_cause)} meta={`${Math.round(diagnosis.confidence * 100)}% 置信度`} />
        <div className="readonly-notice">
          <LockKeyhole size={17} />
          <div><strong>只读诊断</strong><span>诊断不会自动修改提示词、工具、技能或运行配置。</span></div>
        </div>
        <section className="probe-list">
          <h3>诊断探针证据链</h3>
          {diagnosis.probes.map((probe) => (
            <article key={`${probe.probe}-${probe.summary}`}>
              <header><strong>{probeLabel(probe.probe)}</strong><span>{probe.affected_configuration?.join(" · ") || "运行事实"}</span></header>
              <p>{localizedDiagnosticText(probe.summary)}</p>
              <EvidenceList evidence={probe.evidence || []} onSelect={onEvidenceSelect} />
            </article>
          ))}
          {!diagnosis.probes.length && <EmptyLine text="没有附加诊断探针结果。" />}
        </section>
      </main>
      <aside className="eval-secondary-column">
        <AuditSection title="首次偏离">
          {diagnosis.first_divergence ? <EvidenceButton evidence={diagnosis.first_divergence} onSelect={onEvidenceSelect} /> : <p>未定位到偏离跨度。</p>}
        </AuditSection>
        <AuditSection title="受影响配置">
          {diagnosis.affected_configuration.length ? <CodeList items={diagnosis.affected_configuration} /> : <p>未关联配置项。</p>}
        </AuditSection>
        <AuditSection title="修改建议">
          {diagnosis.recommendations.length ? <ol>{diagnosis.recommendations.map((item) => <li key={item}>{localizedDiagnosticText(item)}</li>)}</ol> : <p>没有建议。</p>}
        </AuditSection>
        <AuditSection title="全部证据">
          <EvidenceList evidence={diagnosis.evidence} onSelect={onEvidenceSelect} />
        </AuditSection>
      </aside>
    </div>
  );
}

function JudgeView({ judge }: { judge: JudgeEvaluationRow }) {
  const calibration = asRecord(judge.calibration);
  const syntheticOnly = calibration.synthetic_only === true;
  return (
    <div className="eval-wide-panel">
      <SectionHeading eyebrow="可信裁判" title="多次独立采样" meta={judgeStatusLabel(judge.status)} />
      <div className="judge-metrics">
        <Metric label="可信状态" value={judgeStatusLabel(judge.status)} />
        <Metric label="均值" value={formatScore(judge.mean_score)} />
        <Metric label="中位数" value={formatScore(judge.median_score)} />
        <Metric label="方差" value={formatDecimal(judge.variance)} />
        <Metric label="一致性" value={formatPercent(judge.consistency)} />
        <Metric label="人工一致率" value={formatPercent(numberOrNull(calibration.accuracy))} />
      </div>
      <div className={`judge-trust-line ${judge.status}`}>
        <ShieldCheck size={18} />
        <div>
          <strong>{judgeStatusLabel(judge.status)}</strong>
          <span>{syntheticOnly ? "当前校准只包含合成样本，不能视为真实人工校准。" : judgeStatusText(judge.status)}</span>
        </div>
        <span className="attack-state">攻击集 · {attackLabel(judge.attack_resistant)}</span>
      </div>
      <section className="judge-samples">
        <h3>采样明细</h3>
        {judge.samples.map((sample, index) => (
          <article key={sample.sample_id || String(index)}>
            <header>
              <strong>采样 {index + 1}</strong>
              <span>{sample.abstained ? "已弃权" : sample.error ? "错误" : formatScore(sample.score)}</span>
            </header>
            <p>{localizedDiagnosticText(sample.rationale || sample.error || "没有理由文本。")}</p>
            <small>置信度 {formatPercent(sample.confidence)}</small>
          </article>
        ))}
        {!judge.samples.length && <EmptyLine text="当前运行没有裁判模型采样；生产可信状态保持“未验证”。" />}
      </section>
    </div>
  );
}

function RegressionView({ regression }: { regression: RunEvaluationDetail["regression"] | Record<string, never> }) {
  const report = asRecord(regression.report);
  const decision = asRecord(regression.gate_decision);
  const caseMetrics = asRecord(report.case_metrics);
  const baseline = asRecord(caseMetrics.baseline);
  const candidate = asRecord(caseMetrics.candidate);
  const rerun = Object.keys(asRecord(regression.rerun_statistics)).length
    ? asRecord(regression.rerun_statistics)
    : asRecord(report.rerun_statistics);
  const distribution = asRecord(report.first_divergence_distribution);
  if (!Object.keys(report).length && !Object.keys(decision).length && !Object.keys(rerun).length) {
    return <CenteredState icon={<GitCompareArrows size={24} />} title="没有关联回归报告" detail="当前运行尚未作为基准版本或候选版本进入 CI 回归门禁。" />;
  }
  const gatePassed = typeof decision.passed === "boolean" ? decision.passed : null;
  return (
    <div className="eval-wide-panel">
      <SectionHeading eyebrow="回归门禁" title="基准版本与候选版本" meta={gatePassed == null ? "未执行门禁" : gatePassed ? "通过" : "未通过"} />
      <div className={`gate-decision ${gatePassed === false ? "failed" : gatePassed === true ? "passed" : "neutral"}`}>
        {gatePassed === false ? <AlertTriangle size={22} /> : <CheckCircle2 size={22} />}
        <div><strong>{gatePassed == null ? "没有门禁结论" : gatePassed ? "CI 回归门禁通过" : "CI 回归门禁已阻止回归"}</strong><span>{gateReasonLabel(stringValue(decision.reason)) || "等待回归比较。"}</span></div>
        {typeof decision.exit_code === "number" && <code>退出码 {decision.exit_code}</code>}
      </div>
      <div className="regression-compare">
        <MetricColumn title="基准版本" metrics={baseline} />
        <MetricColumn title="候选版本" metrics={candidate} />
        <section>
          <h3>变化</h3>
          <ValueLine label="分数差" value={formatSigned(numberOrNull(asRecord(regression.baseline_diff).score_delta))} />
          <ValueLine label="新失败" value={String(Array.isArray(report.new_failures) ? report.new_failures.length : 0)} />
          <ValueLine label="分数下降" value={String(Array.isArray(report.score_drops) ? report.score_drops.length : 0)} />
        </section>
      </div>
      <div className="regression-lower">
        <section>
          <h3>随机重跑</h3>
          <ValueLine label="样本数" value={formatNumber(rerun.sample_count)} />
          <ValueLine label="成功率" value={formatPercent(numberOrNull(rerun.success_rate))} />
          <ValueLine label="威尔逊置信区间" value={formatInterval(rerun.wilson_low, rerun.wilson_high)} />
          <ValueLine label="P50 / P95" value={`${formatMs(rerun.p50_latency_ms)} / ${formatMs(rerun.p95_latency_ms)}`} />
        </section>
        <section>
          <h3>首次偏离分布</h3>
          {Object.entries(distribution).map(([name, value]) => <ValueLine key={name} label={name} value={String(value)} />)}
          {!Object.keys(distribution).length && <p className="muted-copy">没有首次偏离。</p>}
        </section>
      </div>
    </div>
  );
}

function CheckRow({ check, onEvidenceSelect }: { check: EvaluationCheckRow; onEvidenceSelect?: (evidence: EvidenceRefRow) => void }) {
  return (
    <article className={`eval-check-row ${check.status}`}>
      <span className="check-status">{checkStatusLabel(check.status)}</span>
      <div><strong>{checkLabel(check.id)}</strong><small>{categoryLabel(check.category)} · {check.hard === false ? "软规则" : "硬规则"} · {check.id}</small></div>
      <p>{check.message ? localizedDiagnosticText(check.message) : comparisonText(check.expected, check.actual)}</p>
      {(check.evidence || []).slice(0, 1).map((evidence, index) => <EvidenceButton key={index} evidence={evidence} onSelect={onEvidenceSelect} compact />)}
    </article>
  );
}

function EvidenceList({ evidence, onSelect }: { evidence: EvidenceRefRow[]; onSelect?: (evidence: EvidenceRefRow) => void }) {
  return evidence.length ? <div className="evidence-list">{evidence.map((item, index) => <EvidenceButton key={`${item.span_id}-${item.event_id}-${index}`} evidence={item} onSelect={onSelect} />)}</div> : <p>没有引用证据。</p>;
}

function EvidenceButton({ evidence, onSelect, compact = false }: { evidence: EvidenceRefRow; onSelect?: (evidence: EvidenceRefRow) => void; compact?: boolean }) {
  const navigable = Boolean(evidence.event_id || evidence.span_id || evidence.sequence != null);
  return (
    <button
      type="button"
      className={`evidence-button ${compact ? "compact" : ""}`}
      disabled={!navigable || !onSelect}
      onClick={() => onSelect?.(evidence)}
      title={evidence.excerpt || "在检查器中打开证据"}
    >
      <Link2 size={13} />
      <span>{evidence.span_id ? `跨度 ${evidence.span_id.slice(0, 10)}` : evidence.event_id ? `事件 ${evidence.event_id.slice(0, 10)}` : evidence.path || evidenceSourceLabel(evidence.source) || "证据"}</span>
      {evidence.sequence != null && <code>#{evidence.sequence}</code>}
    </button>
  );
}

function TrajectoryLane({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return <section><h3>{title}</h3>{items.length ? <ol>{items.map((item, index) => <li key={`${item}-${index}`}><span>{index + 1}</span><strong>{item}</strong></li>)}</ol> : <p>{empty}</p>}</section>;
}

function DimensionRow({ name, dimension }: { name: string; dimension?: Dimension }) {
  if (!dimension || dimension.applicable === false) return <div className="eval-dimension-row na"><span>{dimensionLabel(name)}</span><em>不适用</em></div>;
  const score = Math.round((dimension.score || 0) * 100);
  return <div className="eval-dimension-row"><div><span>{dimensionLabel(name)}</span><small>{dimension.reason || "-"}</small></div><div className="eval-dimension-meter"><i style={{ width: `${score}%` }} /></div><strong>{score}</strong></div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="eval-band-metric"><span>{label}</span><strong>{value}</strong></div>;
}

function MetricColumn({ title, metrics }: { title: string; metrics: Record<string, unknown> }) {
  return <section><h3>{title}</h3><ValueLine label="用例数" value={formatNumber(metrics.case_count)} /><ValueLine label="通过率" value={formatPercent(numberOrNull(metrics.pass_rate))} /><ValueLine label="平均分" value={formatScore(numberOrNull(metrics.mean_score))} /><ValueLine label="轨迹合规" value={formatPercent(numberOrNull(metrics.trajectory_compliance))} /><ValueLine label="参数正确" value={formatPercent(numberOrNull(metrics.tool_argument_accuracy))} /></section>;
}

function AuditSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="eval-audit-section"><h3>{title}</h3>{children}</section>;
}

function SectionHeading({ eyebrow, title, meta }: { eyebrow: string; title: string; meta?: string }) {
  return <div className="eval-section-title"><div><span>{eyebrow}</span><h3>{title}</h3></div>{meta && <small>{meta}</small>}</div>;
}

function EmptyState({ title, detail, onBack }: { title: string; detail: string; onBack: () => void }) {
  return <section className="run-eval-empty" data-testid="run-eval-dashboard-empty"><div><span>轨迹原生评测</span><h2>{title}</h2><p>{detail}</p><button type="button" onClick={onBack}><ArrowLeft size={15} /> 返回检查器</button></div></section>;
}

function CenteredState({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) {
  return <section className="eval-centered-state">{icon}<div><strong>{title}</strong><p>{detail}</p></div></section>;
}

function EmptyLine({ text }: { text: string }) { return <p className="eval-empty-line">{text}</p>; }
function IdLine({ label, value }: { label: string; value: string }) { return <div className="eval-id-line"><span>{label}</span><code>{value}</code></div>; }
function ValueLine({ label, value }: { label: string; value: string }) { return <div className="eval-value-line"><span>{label}</span><strong>{value}</strong></div>; }
function CodeList({ items }: { items: string[] }) { return <div className="code-list">{items.map((item) => <code key={item}>{item}</code>)}</div>; }
function StatusLine({ status }: { status: string }) { return <span className={`judge-status ${status}`}>{judgeStatusLabel(status)}</span>; }

function legacyReport(run: RunRow | null, evaluation: EvaluationPayload | null): EvaluationReportRow | null {
  if (!run || !evaluation) return null;
  const mode = evaluation.mode === "ai" ? "scored" : evaluation.evaluation_mode || "health_only";
  return {
    schema_version: 2,
    report_id: "legacy-evaluation",
    trace_id: "legacy-trace",
    run_id: run.id,
    policy_id: "legacy",
    policy_version: "1",
    mode,
    passed: evaluation.passed ?? null,
    score: mode === "scored" && typeof evaluation.score === "number" ? evaluation.score : null,
    checks: [],
    hard_failures: 0,
    passed_count: evaluation.passed ? 1 : 0,
    failed_count: evaluation.passed === false ? 1 : 0,
    not_configured_count: mode === "health_only" ? 1 : 0,
    deterministic: evaluation.mode !== "ai",
    evaluated_at: evaluation.graded_at,
  };
}

function legacyJudge(evaluation: EvaluationPayload | null): JudgeEvaluationRow {
  return {
    status: evaluation?.judge_status || "unverified",
    samples: [],
    mean_score: evaluation?.mode === "ai" ? evaluation.score : null,
    variance: evaluation?.variance,
    consistency: evaluation?.consistency,
    confidence: evaluation?.confidence,
    dimensions: evaluation?.dimensions || {},
    provider: evaluation?.judge_provider,
    model: evaluation?.judge_model,
  };
}

function firstFailure(checks: EvaluationCheckRow[]): EvaluationCheckRow | undefined { return checks.find((check) => check.status === "failed" || check.status === "error"); }
function asRecord(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function stringList(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
function numberOrNull(value: unknown): number | null { return typeof value === "number" && Number.isFinite(value) ? value : null; }
function stringValue(value: unknown): string { return typeof value === "string" ? value : ""; }
function formatNumber(value: unknown): string { return typeof value === "number" ? value.toLocaleString() : "-"; }
function formatScore(value: unknown): string { const n = numberOrNull(value); return n == null ? "-" : `${Math.round(n * 100)} / 100`; }
function formatPercent(value: unknown): string { const n = numberOrNull(value); return n == null ? "-" : `${Math.round(n * 100)}%`; }
function formatDecimal(value: unknown): string { const n = numberOrNull(value); return n == null ? "-" : n.toFixed(4); }
function formatSigned(value: number | null): string { return value == null ? "-" : `${value > 0 ? "+" : ""}${value.toFixed(3)}`; }
function formatMs(value: unknown): string { const n = numberOrNull(value); return n == null ? "-" : `${Math.round(n)} ms`; }
function formatInterval(low: unknown, high: unknown): string { const l = numberOrNull(low); const h = numberOrNull(high); return l == null || h == null ? "-" : `[${l.toFixed(3)}, ${h.toFixed(3)}]`; }
function comparisonText(expected: unknown, actual: unknown): string { return `预期 ${compactValue(expected)} · 实际 ${compactValue(actual)}`; }
function compactValue(value: unknown): string {
  if (value == null) return "-";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") {
    return ({
      completed: "已完成",
      failed: "失败",
      cancelled: "已取消",
      interrupted: "已中断",
      "terminal status": "终态",
      "valid JSON": "有效的 JSON",
      "every tool call has one result": "每个工具调用都有且仅有一个结果",
      "no raw secret pattern": "不含原始敏感信息模式",
      "workspace-confined paths": "路径限制在工作区内",
      "approval before destructive tool": "破坏性工具调用前已审批",
    } as Record<string, string>)[value] || value;
  }
  if (Array.isArray(value)) return value.length ? value.map(compactValue).join(" → ") : "无";
  const record = asRecord(value);
  if (typeof record.mode === "string" && Array.isArray(record.tools)) {
    return `匹配方式：${matchModeLabel(record.mode)}；工具：${stringList(record.tools).join(" → ") || "无"}`;
  }
  if (typeof record.tool_calls === "number" && typeof record.unpaired === "number") {
    return `工具调用 ${record.tool_calls} 次；未配对 ${record.unpaired} 次`;
  }
  if (typeof record.exact === "number") return `精确 ${record.exact} 次`;
  if (typeof record.min === "number") return `至少 ${record.min}`;
  if (typeof record.max === "number") return `至多 ${record.max}`;
  if (typeof record.absent === "string") return `不得出现 ${record.absent}`;
  try {
    const text = JSON.stringify(value);
    return text.length > 90 ? `${text.slice(0, 87)}...` : text;
  } catch {
    return String(value);
  }
}
function modeLabel(mode: EvaluationReportRow["mode"]): string { return ({ scored: "有质量断言", health_only: "不生成质量分", unscored: "没有可评分事实" } as const)[mode]; }
function verdictText(report: EvaluationReportRow, score: number | null): string { if (report.mode === "health_only") return report.passed ? "运行健康检查通过" : "运行健康检查未通过"; if (report.mode === "unscored") return "没有足够事实进行评测"; if (report.passed === false && report.hard_failures > 0) return "确定性硬规则失败，分数不可覆盖"; return score != null && score >= 70 ? "达到 70 分质量线" : "未达到 70 分质量线"; }
function checkStatusLabel(status: EvaluationCheckRow["status"]): string { return ({ passed: "通过", failed: "失败", error: "错误", not_configured: "未配置" } as const)[status]; }
function judgeStatusLabel(status: string): string { return ({ trusted: "可信", unverified: "未验证", degraded: "已降级", abstained: "已弃权" } as Record<string, string>)[status] || status; }
function judgeStatusText(status: string): string { return ({ trusted: "已通过真实人工标注校准。", unverified: "缺少真实人工标注校准，不能声明为可信裁判模型。", degraded: "裁判模型发生故障，当前使用确定性规则结果。", abstained: "裁判模型无法给出可靠结论。" } as Record<string, string>)[status] || "裁判模型状态未知。"; }
function attackLabel(value: boolean | null | undefined): string { return value === true ? "通过" : value === false ? "失败" : "未验证"; }
function dimensionLabel(name: string): string { return ({ task_completion: "任务成功", correctness: "正确性", completeness: "完整交付", planning_recovery: "规划与恢复", tool_use: "工具使用", execution_verification: "执行验证", efficiency: "成本与效率", safety_control: "安全与可控", user_experience: "沟通体验" } as Record<string, string>)[name] || name; }
function failureLabel(value?: string | null): string { if (!value || value === "none") return "无主要失败"; return ({ wrong_tool_selection: "工具选择错误", invalid_tool_arguments: "工具参数错误", duplicate_tool_call: "重复工具调用", retry_loop: "重试循环", missing_required_step: "缺少必要步骤", tool_result_ignored: "忽略工具结果", premature_completion: "过早完成", verification_missing: "缺少验证", approval_deadlock: "审批死锁", context_drift: "上下文漂移", budget_exhaustion: "预算耗尽", provider_failure: "服务商故障", environment_failure: "环境故障", requirement_understanding: "需求理解", planning: "规划", knowledge_reasoning: "知识与推理", tool_selection: "工具选择", tool_arguments_execution: "工具参数或执行", environment_feedback: "环境反馈理解", missing_verification: "缺少结果验证", recovery_retry: "恢复与重试", communication_clarification: "沟通与澄清", safety_permission: "安全与权限", cost_latency: "成本与时延", execution_or_assertion: "运行或硬规则" } as Record<string, string>)[value] || value; }
function matchModeLabel(value: string): string { return ({ exact: "精确匹配", strict: "严格顺序", subset: "子序列匹配", unordered: "无序匹配" } as Record<string, string>)[value] || value; }
function spanKindLabel(value: string): string { return ({ run: "运行", model: "模型", tool: "工具结果", tool_call: "工具调用", approval: "审批", verification: "验证", delegate: "委派", checkpoint: "检查点", control: "控制", unknown: "未知" } as Record<string, string>)[value] || value; }
function spanStatusLabel(value: string): string { return ({ unset: "未设置", running: "运行中", completed: "已完成", failed: "失败", interrupted: "已中断" } as Record<string, string>)[value] || value; }
function spanDisplayName(span: TraceSpanRow): string {
  if (span.tool_name) return span.tool_name;
  if (span.name === "run") return "运行";
  if (span.name === "checkpoint") return "检查点";
  const modelTurn = span.name.match(/^model_turn:(\d+)$/);
  if (modelTurn) return `模型轮次 ${Number(modelTurn[1]) + 1}`;
  return spanKindLabel(span.kind);
}
function categoryLabel(value: string): string { return ({ final_state: "最终状态", output: "输出", trajectory: "执行轨迹", tool: "工具", tool_result: "工具结果", lifecycle: "生命周期", budget: "预算", safety: "安全", file: "文件", artifact: "产物", health: "运行健康", policy: "评测策略", regression: "回归" } as Record<string, string>)[value] || value; }
function checkLabel(value: string): string {
  const exact = ({
    "run.status": "运行终态",
    "trajectory.sequence": "工具调用轨迹",
    "health.terminal": "运行终态健康检查",
    "policy.assertions": "质量断言配置",
    "safety.redaction": "敏感信息脱敏",
    "safety.workspace": "工作区边界",
    "safety.approval": "破坏性操作审批",
    "output.json": "输出 JSON 格式",
    "output.json_schema": "输出结构约束",
  } as Record<string, string>)[value];
  if (exact) return exact;
  if (value.startsWith("output.contains")) return "输出包含指定内容";
  if (value.startsWith("output.contains_any")) return "输出包含任一指定内容";
  if (value.startsWith("output.forbidden")) return "输出不含禁用内容";
  if (value.startsWith("output.regex")) return "输出符合正则规则";
  if (value.startsWith("tool.required")) return "必需工具调用";
  if (value.startsWith("tool.forbidden")) return "禁用工具检查";
  if (value.startsWith("tool.")) return "工具契约检查";
  if (value.startsWith("budget.")) return "资源预算检查";
  if (value.startsWith("file.")) return "文件检查";
  if (value.startsWith("artifact.")) return "产物检查";
  return "评测检查";
}
function probeLabel(value: string): string { return ({ ToolSpecProbe: "工具规范探针", PromptProbe: "提示词探针", ContextManifestProbe: "上下文清单探针", SkillProbe: "技能探针", WorkspaceRuleProbe: "工作区规则探针", RuntimeConfigProbe: "运行配置探针", VersionProbe: "版本探针" } as Record<string, string>)[value] || value; }
function evidenceSourceLabel(value?: string): string { return ({ trace_span: "轨迹跨度", tool_spec: "工具规范", prompt: "提示词", context_manifest: "上下文清单", skill: "技能", workspace_rule: "工作区规则", runtime_config: "运行配置", versions: "版本信息" } as Record<string, string>)[value || ""] || ""; }
function gateReasonLabel(value: string): string { return ({ "all regression gates passed": "所有回归门禁均已通过", "regression gate failed": "回归门禁未通过", "new failure and score regression detected": "检测到新增失败和评分回退" } as Record<string, string>)[value] || value; }
function configurationValueLabel(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (normalized === "none" || normalized === "null" || normalized === "undefined" || normalized === "") return "未设置";
  if (normalized === "unknown") return "未知";
  return value;
}
function localizedDiagnosticText(value: string): string {
  const exact = ({
    "Selected skill fingerprints were captured in the context manifest.": "已从上下文清单中读取所选技能的指纹。",
    "Workspace rules cited by the model context were inspected read-only.": "已以只读方式检查模型上下文引用的工作区规则。",
    "Constrain tool selection with the task-specific ToolSpec and expected trajectory.": "使用任务专属工具规范和预期轨迹约束工具选择。",
    "Align the call arguments with the cited ToolSpec schema before retrying.": "重试前先让调用参数符合所引用的工具规范。",
    "Record successful call results and suppress duplicate calls with identical arguments.": "记录成功调用的结果，并阻止参数相同的重复调用。",
    "Stop retrying unchanged arguments; use the cited error and ToolSpec to change the next action.": "停止使用未变化的参数重试，并依据错误证据和工具规范调整下一步。",
    "Add the missing required step before producing a terminal answer.": "在生成最终回答前补齐缺失的必要步骤。",
    "Consume and cite the paired tool result before deciding or completing.": "作出决定或完成任务前，先读取并引用配对的工具结果。",
    "Require terminal evidence and successful verification before completion.": "完成任务前必须具备终态证据并通过验证。",
    "Run the configured verification policy before marking the run completed.": "将运行标记为完成前，先执行已配置的验证策略。",
    "Resolve or explicitly deny the pending approval and expose that decision to the agent.": "处理或明确拒绝待定审批，并将决定提供给代理。",
    "Pin the cited context fingerprint and investigate the first turn where it changed.": "固定所引用的上下文指纹，并检查它首次变化的模型轮次。",
    "Reduce retries/context/tool calls or explicitly raise the relevant budget.": "减少重试、上下文或工具调用，或明确提高相关预算。",
    "Retry only if the provider error is retryable; otherwise use the configured provider fallback.": "仅在服务商错误可重试时重试，否则使用已配置的备用服务商。",
    "Repair the cited workspace, tool runtime, or external dependency before rerunning.": "重新运行前先修复所引用的工作区、工具运行时或外部依赖。",
    "Inspect the cited first divergence and add a deterministic check for this failure pattern.": "检查所引用的首次偏离，并为该失败模式补充确定性检查。",
    "Follow the required tool trajectory and ordering.": "遵循要求的工具调用轨迹和顺序。",
    "No quality assertion is configured.": "未配置质量断言。",
    "The required write trajectory was not followed.": "未遵循要求的写入轨迹。",
    "Observed read_file where write_file was required.": "要求调用 write_file，但实际调用了 read_file。",
    "The answer cannot satisfy the trajectory rubric.": "该回答不符合轨迹评分标准。",
  } as Record<string, string>)[value];
  if (exact) return exact;
  const tool = value.match(/^Tool (.+) was called with (.+); schema fingerprint=(.+)\.$/);
  if (tool) return `工具 ${tool[1]} 的调用参数为 ${tool[2]}；规范指纹为 ${tool[3]}。`;
  const prompt = value.match(/^Original task prompt fingerprint=(.+)\.$/);
  if (prompt) return `原始任务提示词指纹：${prompt[1]}。`;
  const context = value.match(/^Context fingerprint=(.+)\.$/);
  if (context) return `上下文指纹：${context[1]}。`;
  const runtime = value.match(/^Runtime configuration fingerprint=(.+); provider=(.+), model=(.+), cwd=(.+)\.$/);
  if (runtime) return `运行配置指纹：${runtime[1]}；服务商：${configurationValueLabel(runtime[2])}；模型：${configurationValueLabel(runtime[3])}；工作目录：${configurationValueLabel(runtime[4])}。`;
  const versions = value.match(/^Trace schema=(.+); event schemas=(.+)\.$/);
  if (versions) return `轨迹结构版本：${versions[1]}；事件结构版本：${versions[2]}。`;
  const missing = value.match(/^missing substring: (.+)$/);
  if (missing) return `缺少必需内容：${missing[1]}`;
  return value;
}
function formatDate(value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString(); }
