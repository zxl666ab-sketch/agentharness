import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ExternalLink,
  FileSpreadsheet,
  FileText,
  Filter,
  LoaderCircle,
  Pencil,
  Play,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { procurementApi } from "./api";
import type {
  AiReviewSuggestion,
  FieldMeta,
  ProcurementMeta,
  ProcurementRequest,
  QuoteField,
} from "./types";

type Props = {
  request: ProcurementRequest;
  meta: ProcurementMeta;
  busy: string | null;
  error?: string | null;
  onUpload: (files: File[]) => Promise<void>;
  onCorrect: (
    quoteId: string,
    field: string,
    value: string | number | boolean | null
  ) => Promise<void>;
  onAnalyze: () => Promise<void>;
};

const FIELD_ORDER = [
  "supplier_name",
  "item_description",
  "material",
  "color",
  "print_colors",
  "currency",
  "unit_price",
  "price_basis",
  "tax_rate",
  "tax_included",
  "shipping_fee",
  "shipping_included",
  "moq",
  "lead_time_days",
  "supports_invoice",
  "width_mm",
  "length_mm",
  "height_mm",
  "thickness_um",
  "payment_terms",
  "valid_until",
];

function sourceIcon(kind: string) {
  return kind === "xlsx" ? <FileSpreadsheet size={16} /> : <FileText size={16} />;
}

function displayValue(value: QuoteField["value"], meta: FieldMeta): string {
  if (value == null) return "";
  if (meta.kind === "boolean") return value ? "true" : "false";
  if (meta.kind === "rate") {
    // 税率以小数存储（如 0.07），显示为百分比时避免浮点尾差（0.07*100=7.000000000000001）。
    return String(Number((Number(value) * 100).toFixed(4)));
  }
  return String(value);
}

function correctionValue(value: string, meta: FieldMeta) {
  if (meta.kind === "boolean") return value === "true";
  if (["decimal", "integer", "rate"].includes(meta.kind)) return value;
  return value.trim() || null;
}

function sourceLocator(value: string) {
  if (value === "filename") return "文件名";
  if (value === "document text") return "文档正文";
  if (value === "not found") return "未找到";
  const pdfLine = /^page (\d+), line (\d+)$/i.exec(value);
  if (pdfLine) return `第 ${pdfLine[1]} 页，第 ${pdfLine[2]} 行`;
  return value.replace(/^Quote!/, "报价单!");
}

function FieldEditor({
  name,
  meta,
  field,
  saving,
  hint,
  readOnly,
  onSave,
}: {
  name: string;
  meta: FieldMeta;
  field: QuoteField;
  saving: boolean;
  hint?: string;
  readOnly?: boolean;
  onSave: (value: string | number | boolean | null) => Promise<void>;
}) {
  const rendered = displayValue(field.value, meta);
  const needsReview = field.status === "needs_review";
  const [editing, setEditing] = useState(needsReview);
  const [value, setValue] = useState(rendered);
  useEffect(() => setValue(rendered), [rendered]);
  useEffect(() => {
    if (!needsReview) setEditing(false);
  }, [needsReview]);
  useEffect(() => {
    // Closing an already-open inline editor as soon as the field becomes
    // read-only (e.g. after approval) prevents a stale save.
    if (readOnly) setEditing(false);
  }, [readOnly]);
  const changed = value !== rendered;
  const hasValue = meta.kind === "boolean" || value.trim().length > 0;
  const canConfirmCurrentValue = needsReview && hasValue;
  const canSave = (changed || canConfirmCurrentValue) && !saving;
  const confidence = Math.round(field.confidence * 100);

  async function handleSave(value: string | number | boolean | null) {
    try {
      await onSave(value);
      // Close the inline editor after a successful save. The old effect only
      // closed it when needs_review changed, which never happens for a field
      // that was already accepted, leaving the editor open after saving.
      setEditing(false);
    } catch {
      // The parent surfaces the error; keep the editor open so the buyer can
      // correct the value and retry.
    }
  }

  return (
    <div data-field={name} className={`proc-field-row ${needsReview ? "needs-review" : ""}`}>
      <div className="proc-field-label">
        <strong>{meta.label}</strong>
        {meta.required ? <span title="必填">必填</span> : null}
      </div>
      <div className="proc-field-editor">
        {editing ? (
          <>
            {meta.kind === "boolean" ? (
              <select value={value} onChange={(event) => setValue(event.target.value)} aria-label={meta.label}>
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            ) : (
              <div className="proc-edit-input">
                <input
                  aria-label={meta.label}
                  type={meta.kind === "date" ? "date" : ["decimal", "integer", "rate"].includes(meta.kind) ? "number" : "text"}
                  step={meta.kind === "integer" ? "1" : "any"}
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                />
                {meta.kind === "rate" ? <span>%</span> : null}
              </div>
            )}
            <button
              type="button"
              className="proc-icon-button compact"
              title={canConfirmCurrentValue && !changed ? "确认当前值并完成复核" : "保存人工修正"}
              aria-label={`保存${meta.label}修正`}
              disabled={!canSave || readOnly}
              onClick={() => void handleSave(correctionValue(value, meta))}
            >
              {saving ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}
            </button>
            <button
              type="button"
              className="proc-icon-button compact"
              title="取消修正"
              aria-label={`取消${meta.label}修正`}
              onClick={() => {
                setValue(rendered);
                setEditing(false);
              }}
            >
              <X size={15} />
            </button>
          </>
        ) : (
          <>
            <span className="proc-readonly-value">{rendered || "—"}</span>
            {!readOnly ? (
              <button
                type="button"
                className="proc-icon-button compact"
                title="人工修正此字段"
                aria-label={`修正${meta.label}`}
                onClick={() => setEditing(true)}
              >
                <Pencil size={14} />
              </button>
            ) : null}
          </>
        )}
      </div>
      <div className="proc-field-evidence">
        <span className={`proc-confidence ${confidence < 80 ? "low" : field.status === "corrected" ? "corrected" : "high"}`}>
          {field.status === "corrected" ? "人工修正" : field.conflicts?.length ? "证据冲突" : `${confidence}%`}
        </span>
        <span title={field.source.excerpt}>{sourceLocator(field.source.locator)}</span>
        <small>{field.source.excerpt || "原文未找到"}</small>
        {field.conflicts?.length ? (
          <ul className="proc-field-conflicts">
            {field.conflicts.map((candidate, index) => (
              <li key={`${String(candidate.value)}-${candidate.source.locator}-${index}`}>
                <strong>{displayValue(candidate.value, meta) || "空值"}</strong>
                <span title={candidate.source.excerpt}>{sourceLocator(candidate.source.locator)}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      {hint ? <p className="proc-field-hint" role="note">{hint}</p> : null}
    </div>
  );
}

export function QuoteWorkspace({
  request,
  meta,
  busy,
  error,
  onUpload,
  onCorrect,
  onAnalyze,
}: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(request.quotes[0]?.id || null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [onlyReview, setOnlyReview] = useState(false);
  const [aiSuggestions, setAiSuggestions] = useState<AiReviewSuggestion[]>([]);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiHandled, setAiHandled] = useState<Set<string>>(new Set());
  const [aiFetched, setAiFetched] = useState(false);
  useEffect(() => {
    if (!request.quotes.some((quote) => quote.id === selectedId)) {
      setSelectedId(request.quotes[0]?.id || null);
    }
  }, [request.quotes, selectedId]);
  const selected = request.quotes.find((quote) => quote.id === selectedId) || null;
  const entries = useMemo(() => {
    if (!selected) return [];
    const shippingIncluded =
      selected.extracted.fields.shipping_included?.value === true;
    const reviewSet = new Set(selected.review_fields || []);
    return FIELD_ORDER.flatMap((name) => {
      const fieldMeta = meta.field_meta[name];
      if (!fieldMeta) return [];
      const field = selected.extracted.fields[name];
      if (!field) {
        // 报价中完全缺失、但服务端列为待复核的字段（例如需求要求高度的纸箱，
        // 解析器不会为可选的 height_mm 生成占位条目）：渲染成空值编辑器。
        if (!reviewSet.has(name)) return [];
        const missingField: QuoteField = {
          value: null,
          confidence: 0,
          status: "needs_review",
          source: {
            document_kind: selected.source_kind,
            locator: "not found",
            excerpt: "报价中未找到该字段",
            method: "missing",
          },
        };
        return [{ name, field: missingField, meta: fieldMeta, hint: undefined }];
      }
      if (onlyReview && field.status !== "needs_review") return [];
      const hint =
        name === "shipping_fee" &&
        shippingIncluded &&
        field.status === "corrected"
          ? "该报价“是否含运费”为“是”，运费已含在单价中，此修正不会额外计入到货成本；如需计入，请同时把“是否含运费”改为“否”。"
          : undefined;
      return [{ name, field, meta: fieldMeta, hint }];
    });
  }, [meta.field_meta, onlyReview, selected]);
  const informational = useMemo(() => {
    if (!selected) return [];
    return Object.entries(selected.extracted.informational_fields || {})
      .map(([key, field]) => ({ key, field }))
      .sort((a, b) => (a.field.label || a.key).localeCompare(b.field.label || b.key, "zh-CN"));
  }, [selected]);
  async function loadAiSuggestions() {
    if (!request.id || aiBusy) return;
    setAiBusy(true);
    setAiError(null);
    try {
      const result = await procurementApi.aiReviewSuggestions(request.id);
      setAiSuggestions(result.suggestions);
      setAiFetched(true);
    } catch (err) {
      setAiError(err instanceof Error ? err.message : String(err));
    } finally {
      setAiBusy(false);
    }
  }

  function aiSuggestionKey(suggestion: AiReviewSuggestion) {
    return `${suggestion.quote_id}:${suggestion.field}`;
  }

  async function acceptSuggestion(suggestion: AiReviewSuggestion) {
    const key = aiSuggestionKey(suggestion);
    if (aiHandled.has(key)) return;
    setAiHandled((current) => new Set(current).add(key));
    try {
      await onCorrect(suggestion.quote_id, suggestion.field, suggestion.suggested_value);
    } catch {
      setAiHandled((current) => {
        const next = new Set(current);
        next.delete(key);
        return next;
      });
    }
  }

  function dismissSuggestion(suggestion: AiReviewSuggestion) {
    setAiHandled((current) => new Set(current).add(aiSuggestionKey(suggestion)));
  }

  const visibleSuggestions = aiSuggestions.filter(
    (suggestion) => !aiHandled.has(aiSuggestionKey(suggestion))
  );


  const canAnalyze =
    request.quote_count >= 2 &&
    request.unresolved_field_count === 0 &&
    request.status !== "approved" &&
    request.status !== "no_award" &&
    request.status !== "analyzed";

  return (
    <div className="proc-workspace-grid">
      <section className="proc-quote-list" aria-label="供应商报价列表">
        <header className="proc-panel-head">
          <div><h2>供应商报价</h2><span>{request.quote_count} 家</span></div>
          <label className={`proc-upload-button ${busy === "upload" ? "disabled" : ""}`}>
            {busy === "upload" ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}
            <span>{busy === "upload" ? "上传并解析中" : "上传报价"}</span>
            <input
              data-testid="quote-upload"
              type="file"
              accept=".xlsx,.pdf"
              multiple
              disabled={busy === "upload" || request.status === "approved" || request.status === "no_award"}
              onChange={(event) => {
                const files = Array.from(event.target.files || []);
                event.target.value = "";
                const allowed = meta.allowed_extensions || [".xlsx", ".pdf"];
                const invalid = files.find(
                  (file) => !allowed.some((ext) => file.name.toLowerCase().endsWith(ext))
                );
                if (invalid) {
                  setLocalError(`不支持的文件：${invalid.name}`);
                  return;
                }
                const oversized = files.find((file) => file.size > meta.max_file_bytes);
                if (oversized) {
                  setLocalError(
                    `${oversized.name} 超过单文件 ${Math.round(meta.max_file_bytes / 1024 / 1024)} MB 上限`
                  );
                  return;
                }
                if (request.quote_count + files.length > meta.max_quotes_per_request) {
                  setLocalError(`每个采购任务最多上传 ${meta.max_quotes_per_request} 份报价`);
                  return;
                }
                setLocalError(null);
                if (files.length) void onUpload(files);
              }}
            />
          </label>
        </header>
        {localError ? <p className="proc-conversation-error" role="alert">{localError}</p> : null}

        {request.quotes.length ? (
          <div className="proc-quote-items">
            {request.quotes.map((quote) => (
              <button
                type="button"
                className={`proc-quote-item ${selectedId === quote.id ? "selected" : ""}`}
                key={quote.id}
                onClick={() => setSelectedId(quote.id)}
              >
                <span className={`proc-file-icon ${quote.source_kind}`}>{sourceIcon(quote.source_kind)}</span>
                <span className="proc-quote-copy">
                  <strong>{quote.supplier_name}</strong>
                  <small>{quote.source_filename}</small>
                </span>
                {quote.review_count ? (
                  <span className="proc-review-count"><AlertTriangle size={13} />{quote.review_count}</span>
                ) : (
                  <CheckCircle2 className="proc-ready-icon" size={16} />
                )}
              </button>
            ))}
          </div>
        ) : (
          <div className="proc-empty-compact">
            <Upload size={26} />
            <strong>尚未上传报价</strong>
            <span>支持 XLSX 与文本型 PDF</span>
          </div>
        )}

        <div className="proc-analysis-bar">
          <div>
            <strong>{request.unresolved_field_count ? `${request.unresolved_field_count} 项待复核` : request.quote_count === 0 && request.status === "draft" ? "待 Agent 结构化需求" : "字段已就绪"}</strong>
              <span>{request.quote_count < 2 ? "至少需要 2 家报价" : "金额将由确定性规则核算"}</span>
          </div>
          <button
            className="proc-button primary"
            type="button"
            disabled={!canAnalyze || busy === "analyze"}
            onClick={() => void onAnalyze()}
          >
            {busy === "analyze" ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
            {busy === "analyze" ? "分析中" : request.status === "analyzed" ? "已比价" : "开始比价"}
          </button>
        </div>
        {error ? <p className="proc-inline-error" role="alert">{error}</p> : null}
      </section>

      <section className="proc-quote-review" aria-label="报价字段复核">
        {selected ? (
          <>
            <header className="proc-panel-head review-head">
              <div>
                <h2>{selected.supplier_name}</h2>
                <span>{selected.review_count ? "待人工复核" : "字段可用于比价"}</span>
              </div>
              <div className="proc-review-tools">
                <label className="proc-filter-toggle" title="仅显示待复核字段">
                  <Filter size={14} />
                  <input type="checkbox" checked={onlyReview} onChange={(event) => setOnlyReview(event.target.checked)} />
                  <span>仅待复核</span>
                </label>
                <button
                  type="button"
                  className="proc-button secondary compact"
                  disabled={aiBusy}
                  title="AI 复核建议（只读，采纳后按人工修正写入）"
                  onClick={() => void loadAiSuggestions()}
                >
                  {aiBusy ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
                  AI 建议
                </button>
                <a
                  className="proc-icon-button"
                  href={`/api/artifacts/${selected.source_artifact_id}/raw`}
                  target="_blank"
                  rel="noreferrer"
                  title="查看报价原件证据"
                  aria-label="查看报价原件证据"
                >
                  <ExternalLink size={16} />
                </a>
              </div>
            </header>
            <div className="proc-source-strip">
              <span><ShieldCheck size={14} />原件 SHA-256</span>
              <code title={selected.source_sha256}>{selected.source_sha256.slice(0, 20)}</code>
              <span>解析用时 {Math.round(selected.processing_ms)} ms</span>
            </div>
            {aiError ? <p className="proc-inline-error" role="alert">{aiError}</p> : null}
            {(aiBusy || aiFetched || visibleSuggestions.length) ? (
              <section className="proc-ai-suggestions" aria-label="AI 复核建议">
                <header>
                  <span><Sparkles size={14} /><strong>AI 复核建议</strong></span>
                  <small>仅建议，采纳后按人工修正写入并记录审计</small>
                </header>
                {aiBusy ? <p className="proc-ai-loading"><LoaderCircle className="spin" size={14} />正在生成建议…</p> : null}
                {aiFetched && !visibleSuggestions.length ? <p className="proc-ai-empty">本次未生成可安全采纳的建议：证据不足或涉及金额的字段不会被建议，请人工填写。</p> : null}
                {visibleSuggestions.length ? (
                  <ul>
                  {visibleSuggestions.map((suggestion) => (
                    <li key={`${suggestion.quote_id}:${suggestion.field}`}>
                      <div>
                        <strong>{request.quotes.find((quote) => quote.id === suggestion.quote_id)?.supplier_name || "报价"} · {meta.field_meta[suggestion.field]?.label || suggestion.field}</strong>
                        <span>建议值：<b>{String(suggestion.suggested_value)}</b></span>
                        {suggestion.reason ? <small>{suggestion.reason}</small> : null}
                      </div>
                      <div className="proc-ai-suggestion-actions">
                        <button type="button" className="proc-button primary compact" onClick={() => void acceptSuggestion(suggestion)}>采纳</button>
                        <button type="button" className="proc-button secondary compact" onClick={() => dismissSuggestion(suggestion)}>忽略</button>
                      </div>
                    </li>
                  ))}
                  </ul>
                ) : null}
              </section>
            ) : null}
            <div className="proc-field-table">
              <div className="proc-field-table-head"><span>字段</span><span>抽取值 / 修正值</span><span>来源证据</span></div>
              {entries.map(({ name, field, meta: fieldMeta, hint }) => (
                <FieldEditor
                  key={name}
                  name={name}
                  meta={fieldMeta}
                  field={field}
                  hint={hint}
                  saving={busy === `field:${selected.id}:${name}`}
                  readOnly={request.status === "approved" || request.status === "no_award"}
                  onSave={(value) => onCorrect(selected.id, name, value)}
                />
              ))}
              {!entries.length ? (
                <div className="proc-no-review"><CheckCircle2 size={18} />没有待复核字段</div>
              ) : null}
            </div>
            {informational.length ? (
              <details className="proc-info-fields">
                <summary>
                  <span>其他字段（未纳入计算，来自原件）</span>
                  <span className="proc-info-count">{informational.length}</span>
                </summary>
                <div className="proc-info-table">
                  <div className="proc-field-table-head"><span>字段</span><span>抽取值</span><span>来源证据</span></div>
                  {informational.map(({ key, field }) => (
                    <div key={key} className="proc-field-row">
                      <div className="proc-field-label"><strong>{field.label || key}</strong><span>参考</span></div>
                      <div className="proc-field-editor"><span className="proc-info-value">{String(field.value ?? "")}</span></div>
                      <div className="proc-field-evidence">
                        <span className="proc-confidence high">{Math.round(field.confidence * 100)}%</span>
                        <span title={field.source.excerpt}>{sourceLocator(field.source.locator)}</span>
                        <small>{field.source.excerpt || "原文未找到"}</small>
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            ) : null}
          </>
        ) : (
          <div className="proc-empty-panel">
            <FileSpreadsheet size={30} />
            <h2>报价字段与来源证据</h2>
            <p>上传供应商报价后在这里逐项复核。</p>
          </div>
        )}
      </section>
    </div>
  );
}
