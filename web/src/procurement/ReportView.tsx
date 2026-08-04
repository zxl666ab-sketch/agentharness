import {
  Ban,
  ClipboardList,
  CheckCircle2,
  Copy,
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
  onReopen?: (copyQuotes: boolean) => Promise<void>;
};

const EVENT_LABELS: Record<string, string> = {
  request_created: "采购需求已创建",
  request_created_from_conversation: "采购对话已创建",
  attachment_staged: "报价附件已暂存",
  agent_run_started: "采购 Agent 已启动",
  requirement_captured_by_agent: "采购需求已结构化",
  requirement_corrected: "采购需求已人工确认",
  requirement_confirmed: "采购需求已人工确认",
  quote_imported: "供应商报价已上传",
  quotes_parsed_by_agent: "供应商报价已解析",
  clarification_requested: "Agent 已发起人工澄清",
  field_corrected: "报价字段已人工修正",
  comparison_created_by_agent: "确定性比价快照已生成",
  deterministic_pipeline_completed: "确定性分析流水线已完成",
  comparison_superseded: "旧比价快照审批已过期",
  supplier_selection_requested: "Agent 已请求人工选择供应商",
  supplier_approved: "供应商已人工批准",
  supplier_no_award: "本轮已流标",
  execution_artifacts_created: "审批后执行草稿已生成",
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

function quantityText(value: number | string) {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : String(value);
}

export function procurementReportMarkdown(report: ProcurementAuditReport) {
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
    `- 采购数量：${quantityText(report.request.quantity)} ${report.request.unit}`,
    `- 报告证据指纹：${report.evidence_sha256}`,
    "",
    "## 审批结论",
    "",
    `- 决策：${report.decision?.decision === "no_award" ? "本轮流标" : "供应商已选定"}`,
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
    "## 审批后执行草稿",
    "",
    ...(report.execution_artifacts || []).map(
      (artifact) => `- ${markdownCell(artifact.filename)}：Artifact ${artifact.artifact_id}，SHA-256 ${artifact.sha256}`,
    ),
    "",
    "## 供应商历史证据",
    "",
    ...(report.supplier_history?.suppliers || []).map(
      (supplier) => `- ${markdownCell(supplier.supplier_name)}：${supplier.approved_purchase_count} 次本地已批准采购；${markdownCell(supplier.evidence)}`,
    ),
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

export function ReportView({ request, report, loading, error = null, onReopen }: Props) {
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
        <div className="proc-report-verdict">
          {noAward ? <Ban size={24} /> : <CheckCircle2 size={24} />}
          <span><small>采购结论</small><strong>{noAward ? "本轮流标" : `已选定 ${selected?.supplier_name || "供应商"}`}</strong></span>
        </div>
        <div className="proc-report-actions">
          <button className="proc-icon-button" type="button" title="打印报告" aria-label="打印报告" onClick={() => window.print()}><Printer size={17} /></button>
          <button className="proc-icon-button" type="button" title="下载中文采购报告" aria-label="下载中文采购报告" disabled={!report} onClick={() => report && downloadReport(report)}><Download size={17} /></button>
          <button className="proc-button secondary" type="button" disabled={!onReopen} onClick={() => onReopen && void onReopen(false)}><Copy size={15} />复制需求</button>
          <button className="proc-button secondary" type="button" disabled={!onReopen} onClick={() => onReopen && void onReopen(true)}><Copy size={15} />复制需求及报价</button>
        </div>
        <p>{request.decision.note || (noAward ? "当前快照没有合格报价。" : "已核对报价原件、硬性条件与到货成本。")}</p>
        <div className="proc-report-metrics">
          {noAward ? <span><small>流标报价</small><strong>{request.comparison.result.quotes.length} 家</strong></span> : null}
          {noAward ? <span><small>合格报价</small><strong>0 家</strong></span> : null}
          <span><small>总到货成本</small><strong>{selected ? `${selected.cost.landed_total_base} ${selected.cost.base_currency}` : "-"}</strong></span>
          <span><small>到货单价</small><strong>{selected ? `${selected.cost.landed_unit_base} ${selected.cost.base_currency}` : "-"}</strong></span>
          {!noAward ? <span><small>起订量 / 交期</small><strong>{selected ? `${selected.commercial.moq.toLocaleString("zh-CN")} / ${selected.commercial.lead_time_days} 天` : "-"}</strong></span> : null}
          <span><small>操作人</small><strong>{request.decision.actor}</strong></span>
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
        <header><div><ClipboardList size={17} /><h2>审批后执行草稿</h2></div><span>{report?.execution_artifacts?.length || 0} 份</span></header>
        <div className="proc-report-artifacts">
          {(report?.execution_artifacts || []).map((artifact) => (
            <a key={artifact.artifact_id} href={`/api/artifacts/${artifact.artifact_id}/raw`} target="_blank" rel="noreferrer">
              <span className="proc-file-icon txt"><FileText size={16} /></span>
              <span><strong>{artifact.kind === "purchase_order_draft" ? "采购订单草稿" : "供应商确认邮件"}</strong><small>{artifact.filename}</small></span>
              <Download size={15} />
            </a>
          ))}
          {!report?.execution_artifacts?.length ? <p className="proc-report-empty">{noAward ? "流标不会生成订单或供应商邮件草稿。" : "审批完成后生成订单和邮件草稿。"}</p> : null}
        </div>
      </section>

      <section className="proc-report-section">
        <header><div><History size={17} /><h2>供应商历史证据</h2></div><span>{report?.supplier_history?.suppliers.length || 0} 家</span></header>
        <div className="proc-history-list">
          {(report?.supplier_history?.suppliers || []).map((supplier) => (
            <div key={supplier.quote_id}>
              <strong>{supplier.supplier_name}</strong>
              <span>{supplier.approved_purchase_count} 次本地已批准采购 · {supplier.evidence}</span>
              {supplier.records.length ? <small>{supplier.records.map((record) => `${record.request_reference} · ${new Date(record.decision_at).toLocaleDateString("zh-CN")}`).join("；")}</small> : null}
            </div>
          ))}
          {!report?.supplier_history?.suppliers.length ? <p className="proc-report-empty">暂无可引用的本地历史采购记录。</p> : null}
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
