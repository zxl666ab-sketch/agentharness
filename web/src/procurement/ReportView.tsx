import {
  CheckCircle2,
  Download,
  FileCheck2,
  FileSpreadsheet,
  FileText,
  Fingerprint,
  History,
  Printer,
  ShieldCheck,
} from "lucide-react";

import type { ProcurementAuditReport, ProcurementRequest } from "./types";

type Props = {
  request: ProcurementRequest;
  report: ProcurementAuditReport | null;
  loading: boolean;
  error?: string | null;
};

const EVENT_LABELS: Record<string, string> = {
  request_created: "采购需求已创建",
  request_created_from_conversation: "采购对话已创建",
  attachment_staged: "报价附件已暂存",
  agent_run_started: "采购 Agent 已启动",
  requirement_captured_by_agent: "采购需求已结构化",
  quote_imported: "供应商报价已上传",
  quotes_parsed_by_agent: "供应商报价已解析",
  clarification_requested: "Agent 已发起人工澄清",
  field_corrected: "报价字段已人工修正",
  comparison_created_by_agent: "确定性比价快照已生成",
  deterministic_pipeline_completed: "确定性分析流水线已完成",
  comparison_superseded: "旧比价快照审批已过期",
  supplier_selection_requested: "Agent 已请求人工选择供应商",
  supplier_approved: "供应商已人工批准",
  procurement_no_award: "采购员确认无合格报价",
  ai_interpretation: "AI 解读已生成",
  ai_review_suggestions: "AI 复核建议已生成",
};

const FIELD_LABELS: Record<string, string> = {
  supplier_name: "供应商",
  item_description: "物料描述",
  material: "材质",
  color: "颜色",
  print_colors: "印刷色数",
  currency: "币种",
  unit_price: "报价",
  price_basis: "计价数量",
  tax_rate: "税率",
  tax_included: "是否含税",
  shipping_fee: "运费",
  shipping_included: "是否含运费",
  moq: "起订量",
  lead_time_days: "交期",
  supports_invoice: "是否可开票",
  width_mm: "宽度",
  length_mm: "长度",
  height_mm: "高度",
  thickness_um: "厚度",
  payment_terms: "付款条件",
  valid_until: "报价有效期",
};

function timestamp(value?: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN");
}

function markdownCell(value: unknown) {
  return String(value ?? "-").replaceAll("|", "\\|").replaceAll("\n", " ");
}

function businessText(value: string) {
  return value.replace(/\bMOQ\s+(?=\d)/g, "起订量（MOQ）");
}

export function procurementReportMarkdown(report: ProcurementAuditReport) {
  const noAward = report.decision?.decision === "no_award";
  const selected = report.comparison?.result.quotes.find(
    (quote) => quote.quote_id === report.decision?.quote_id
  );
  const quoteRows = (report.comparison?.result.quotes || []).map((quote) =>
    `| ${markdownCell(quote.supplier_name)} | ${quote.eligible ? "符合" : "淘汰"} | ${markdownCell(quote.cost.landed_total_base)} ${quote.cost.base_currency} | ${markdownCell(quote.cost.landed_unit_base)} ${quote.cost.base_currency} | ${quote.exclusion_reasons.map((item) => businessText(item.message)).join("；") || "-"} |`
  );
  const sourceRows = report.quotes.map((quote) =>
    `| ${markdownCell(quote.supplier_name)} | ${markdownCell(quote.source_filename)} | ${quote.source_sha256} |`
  );
  const auditRows = report.audit_events.map((event) => {
    const field = event.type === "field_corrected"
      ? `（${FIELD_LABELS[String(event.payload.field || "")] || "报价字段"}）`
      : "";
    return `| ${timestamp(event.created_at)} | ${markdownCell(EVENT_LABELS[event.type] || `其他审计事件（${event.type}）`)}${field} | ${markdownCell(event.actor)} |`;
  });
  return [
    "# 采购审批报告",
    "",
    `- 采购编号：${report.request.reference}`,
    `- 采购任务：${report.request.title}`,
    `- 物料：${report.request.item_name}`,
    `- 采购数量：${report.request.quantity.toLocaleString("zh-CN")} 个`,
    `- 报告证据指纹：${report.evidence_sha256}`,
    "",
    "## 审批结论",
    "",
    `- 审批结论：${noAward ? "本轮无合格报价" : "选定供应商"}`,
    `- 选定供应商：${selected?.supplier_name || "-"}`,
    `- 总到货成本：${selected ? `${selected.cost.landed_total_base} ${selected.cost.base_currency}` : "-"}`,
    `- 到货单价：${selected ? `${selected.cost.landed_unit_base} ${selected.cost.base_currency}` : "-"}`,
    `- 审批人：${report.decision?.actor || "-"}`,
    `- 审批时间：${timestamp(report.decision?.created_at)}`,
    `- 审批备注：${report.decision?.note || "已核对报价原件、硬性条件与到货成本。"}`,
    "",
    "## 供应商比较",
    "",
    "| 供应商 | 资格结论 | 总到货成本 | 到货单价 | 淘汰原因 |",
    "| --- | --- | ---: | ---: | --- |",
    ...quoteRows,
    "",
    "## 报价原件",
    "",
    "| 供应商 | 文件名 | 原件 SHA-256 |",
    "| --- | --- | --- |",
    ...sourceRows,
    "",
    "## 计算与运行证据",
    "",
    `- 比价输入 SHA-256：${report.comparison?.input_sha256 || "-"}`,
    `- 确定性规则版本：${report.comparison?.result.ruleset_version || "-"}`,
    `- 分析运行 ID：${report.runtime.run_id || "-"}`,
    "",
    "## 审计时间线",
    "",
    "| 时间 | 事件 | 操作人 |",
    "| --- | --- | --- |",
    ...auditRows,
    "",
  ].join("\n");
}

function downloadReport(report: ProcurementAuditReport) {
  const blob = new Blob([`\uFEFF${procurementReportMarkdown(report)}`], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${report.request.reference}-采购审批报告.md`;
  link.click();
  URL.revokeObjectURL(url);
}

export function ReportView({ request, report, loading, error = null }: Props) {
  if (!request.decision || !request.comparison) {
    return (
      <section className="proc-empty-state">
        <span className="proc-empty-symbol"><FileCheck2 size={30} /></span>
        <h2>等待人工审批结论</h2>
        <p>供应商正式选定后，这里会固化采购结论、原件哈希和完整操作时间线。</p>
      </section>
    );
  }
  const selected = request.comparison.result.quotes.find(
    (quote) => quote.quote_id === request.decision?.quote_id
  );
  const noAward = request.decision.decision === "no_award";

  return (
    <div className="proc-report-view">
      <header className="proc-report-hero">
        <div className="proc-report-verdict"><CheckCircle2 size={24} /><span><small>审批结论</small><strong>{noAward ? "本轮无合格报价" : `已选定 ${selected?.supplier_name}`}</strong></span></div>
        <div className="proc-report-actions">
          <button className="proc-icon-button" type="button" title="打印报告" aria-label="打印报告" onClick={() => window.print()}><Printer size={17} /></button>
          <button className="proc-icon-button" type="button" title="下载中文采购报告" aria-label="下载中文采购报告" disabled={!report} onClick={() => report && downloadReport(report)}><Download size={17} /></button>
        </div>
        <p>{request.decision.note || (noAward ? "全部报价未通过硬性条件，确认本轮不选定供应商。" : "已核对报价原件、硬性条件与到货成本。")}</p>
        <div className="proc-report-metrics">
          <span><small>{noAward ? "合格报价" : "总到货成本"}</small><strong>{noAward ? "0 家" : selected ? `${selected.cost.landed_total_base} ${selected.cost.base_currency}` : "-"}</strong></span>
          <span><small>{noAward ? "淘汰报价" : "到货单价"}</small><strong>{noAward ? `${request.comparison.result.excluded_count} 家` : selected ? `${selected.cost.landed_unit_base} ${selected.cost.base_currency}` : "-"}</strong></span>
          <span><small>{noAward ? "后续动作" : "起订量 / 交期"}</small><strong>{noAward ? "重新询价" : selected ? `${selected.commercial.moq.toLocaleString("zh-CN")} / ${selected.commercial.lead_time_days} 天` : "-"}</strong></span>
          <span><small>审批人</small><strong>{request.decision.actor}</strong></span>
        </div>
      </header>

      {error ? <p className="proc-inline-error" role="alert">审批证据加载失败：{error}</p> : null}

      <section className="proc-report-section">
        <header><div><Fingerprint size={17} /><h2>证据指纹</h2></div><span>{loading ? "恢复中" : "已固化"}</span></header>
        <div className="proc-hash-grid">
          <span><small>采购报告</small><code title={report?.evidence_sha256 || "-"}>{report?.evidence_sha256 || "-"}</code></span>
          <span><small>比价输入</small><code title={request.comparison.input_sha256}>{request.comparison.input_sha256}</code></span>
          <span><small>规则版本</small><code title={request.comparison.result.ruleset_version}>{request.comparison.result.ruleset_version}</code></span>
          <span><small>分析运行 ID</small><code title={request.analysis_run_id || "-"}>{request.analysis_run_id || "-"}</code></span>
        </div>
      </section>

      <section className="proc-report-section">
        <header><div><ShieldCheck size={17} /><h2>报价原件与字段来源</h2></div><span>{request.quotes.length} 份</span></header>
        <div className="proc-report-sources">
          {request.quotes.map((quote) => (
            <a key={quote.id} href={`/api/artifacts/${quote.source_artifact_id}/raw`} target="_blank" rel="noreferrer">
              <span className={`proc-file-icon ${quote.source_kind}`}>
                {quote.source_kind === "xlsx" ? <FileSpreadsheet size={16} /> : <FileText size={16} />}
              </span>
              <span><strong>{quote.supplier_name}</strong><small>{quote.source_filename}</small></span>
              <code>{quote.source_sha256.slice(0, 16)}</code>
            </a>
          ))}
        </div>
      </section>

      <section className="proc-report-section">
        <header><div><History size={17} /><h2>采购审计时间线</h2></div><span>{report?.audit_events.length || 0} 条</span></header>
        <div className="proc-audit-timeline">
          {(report?.audit_events || []).map((event) => (
            <div key={event.id} className="proc-audit-event">
              <i />
              <span><strong>{EVENT_LABELS[event.type] || event.type}</strong><small>{event.actor} · {timestamp(event.created_at)}</small></span>
              {event.type === "field_corrected" ? <code>{FIELD_LABELS[String(event.payload.field || "")] || "报价字段"}</code> : null}
              {event.run_id ? <code>运行 {event.run_id.slice(0, 10)}</code> : null}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
