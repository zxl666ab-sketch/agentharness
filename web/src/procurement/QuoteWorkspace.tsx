import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ExternalLink,
  FileSpreadsheet,
  FileText,
  Filter,
  LoaderCircle,
  Play,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type {
  FieldMeta,
  ProcurementMeta,
  ProcurementQuote,
  ProcurementRequest,
  QuoteField,
  RequirementSpecification,
} from "./types";

type Props = {
  request: ProcurementRequest;
  meta: ProcurementMeta;
  busy: string | null;
  error?: string | null;
  /** 默认是否只显示待复核字段（P1-4 默认 true） */
  defaultOnlyReview?: boolean;
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

const V2_FIELD_ORDER = [
  "supplier_name",
  "item_description",
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
  "payment_terms",
  "valid_until",
];

function sourceIcon(kind: string) {
  return kind === "xlsx" ? <FileSpreadsheet size={16} /> : <FileText size={16} />;
}

function displayValue(value: QuoteField["value"], meta: FieldMeta): string {
  if (value == null) return "";
  if (meta.kind === "boolean") return value ? "true" : "false";
  if (meta.kind === "rate") return String(Number(value) * 100);
  return String(value);
}

function correctionValue(value: string, meta: FieldMeta) {
  if (meta.kind === "boolean") return value === "true";
  if (["decimal", "integer", "rate"].includes(meta.kind)) return value;
  return value.trim() || null;
}

function dynamicFieldMeta(spec: RequirementSpecification): FieldMeta {
  return {
    label: spec.label,
    kind: spec.type === "number" ? "decimal" : spec.type === "boolean" ? "boolean" : "text",
    required: spec.priority === "hard",
  };
}

const DYNAMIC_STANDARD_FIELD_ALIASES: Record<string, string[]> = {
  width: ["width_mm"],
  宽度: ["width_mm"],
  widthmm: ["width_mm"],
  length: ["length_mm"],
  长度: ["length_mm"],
  lengthmm: ["length_mm"],
  height: ["height_mm"],
  高度: ["height_mm"],
  heightmm: ["height_mm"],
  size: ["width_mm", "length_mm"],
  尺寸: ["width_mm", "length_mm"],
  成品尺寸: ["width_mm", "length_mm"],
  规格尺寸: ["width_mm", "length_mm"],
  thickness: ["thickness_um"],
  厚度: ["thickness_um"],
  材料厚度: ["thickness_um"],
  thicknessum: ["thickness_um"],
  material: ["material"],
  材质: ["material"],
  面材: ["material"],
  color: ["color"],
  颜色: ["color"],
  底色: ["color"],
  printcolors: ["print_colors"],
  印刷色数: ["print_colors"],
  layers: ["layers"],
  layercount: ["layers"],
  瓦楞层数: ["layers"],
  瓦楞纸层数: ["layers"],
  moq: ["moq"],
  最小起订量: ["moq"],
};

function normalizedDynamicField(value: unknown) {
  return String(value || "")
    .trim()
    .toLocaleLowerCase()
    .replace(/[^0-9a-z\u4e00-\u9fff]+/g, "");
}

function dynamicStandardFieldCandidates(name: string, spec: RequirementSpecification) {
  for (const value of [name, spec.label]) {
    const candidates = DYNAMIC_STANDARD_FIELD_ALIASES[normalizedDynamicField(value)];
    if (candidates) return candidates;
  }
  return [];
}

function missingDynamicField(quote: ProcurementQuote): QuoteField {
  return {
    value: null,
    confidence: 0,
    status: "needs_review",
    source: {
      document_kind: quote.source_kind,
      locator: "not found",
      excerpt: "",
      method: "missing",
    },
  };
}

function dynamicQuoteField(
  name: string,
  spec: RequirementSpecification,
  quote: ProcurementQuote,
): QuoteField {
  const direct = quote.extracted.specifications?.[name];
  if (direct) return direct;

  const candidates = dynamicStandardFieldCandidates(name, spec);
  const fields = candidates
    .map((candidate) => quote.extracted.fields[candidate])
    .filter((field): field is QuoteField => Boolean(field));
  if (candidates.length === 1 && fields.length === 1) return fields[0];
  if (candidates.length > 1 && fields.length === candidates.length) {
    const first = fields[0];
    const status = fields.some((field) => field.status === "needs_review")
      ? "needs_review"
      : fields.some((field) => field.status === "corrected")
        ? "corrected"
        : "accepted";
    return {
      value: fields.map((field) => String(field.value ?? "")).join("×"),
      confidence: Math.min(...fields.map((field) => field.confidence)),
      status,
      source: {
        document_kind: first.source.document_kind,
        locator: fields.map((field) => field.source.locator).join("；"),
        excerpt: fields.map((field) => field.source.excerpt).filter(Boolean).join("；"),
        method: "standard_field_mapping",
      },
    };
  }
  return missingDynamicField(quote);
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
  onSave,
}: {
  name: string;
  meta: FieldMeta;
  field: QuoteField;
  saving: boolean;
  onSave: (value: string | number | boolean | null) => Promise<void>;
}) {
  const rendered = displayValue(field.value, meta);
  const [value, setValue] = useState(rendered);
  useEffect(() => setValue(rendered), [rendered]);
  const changed = value !== rendered;
  const hasValue = meta.kind === "boolean" || value.trim().length > 0;
  const canConfirmCurrentValue = field.status === "needs_review" && hasValue;
  const confidence = Math.round(field.confidence * 100);

  return (
    <div data-field={name} className={`proc-field-row ${field.status === "needs_review" ? "needs-review" : ""}`}>
      <div className="proc-field-label">
        <strong>{meta.label}</strong>
        {meta.required ? <span title="必填">必填</span> : null}
      </div>
      <div className="proc-field-editor">
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
          disabled={(!changed && !canConfirmCurrentValue) || saving}
          onClick={() => void onSave(correctionValue(value, meta))}
        >
          {saving ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}
        </button>
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
    </div>
  );
}

export function QuoteWorkspace({
  request,
  meta,
  busy,
  error,
  defaultOnlyReview = true,
  onUpload,
  onCorrect,
  onAnalyze,
}: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(request.quotes[0]?.id || null);
  // 默认只显示待复核字段（P1-4 信息密度治理），可手动切回全部字段
  const [onlyReview, setOnlyReview] = useState(defaultOnlyReview);
  useEffect(() => {
    if (!request.quotes.some((quote) => quote.id === selectedId)) {
      setSelectedId(request.quotes[0]?.id || null);
    }
  }, [request.quotes, selectedId]);
  const selected = request.quotes.find((quote) => quote.id === selectedId) || null;
  const entries = useMemo(() => {
    if (!selected) return [];
    const fixedFieldOrder = request.schema_version === 2 ? V2_FIELD_ORDER : FIELD_ORDER;
    const fixedEntries = fixedFieldOrder.flatMap((name) => {
      const field = selected.extracted.fields[name];
      const fieldMeta = meta.field_meta[name];
      if (!field || !fieldMeta || (onlyReview && field.status !== "needs_review")) return [];
      return [{ name, field, meta: fieldMeta }];
    });
    const dynamicEntries = Object.entries(request.specifications || {}).flatMap(([name, raw]) => {
      if (!raw || typeof raw !== "object" || !("type" in raw)) return [];
      const spec = raw as RequirementSpecification;
      const candidates = dynamicStandardFieldCandidates(name, spec);
      if (candidates.length === 1 && fixedFieldOrder.includes(candidates[0])) return [];
      const field = dynamicQuoteField(name, spec, selected);
      if (onlyReview && field.status !== "needs_review") return [];
      return [{ name, field, meta: dynamicFieldMeta(spec) }];
    });
    return [...fixedEntries, ...dynamicEntries];
  }, [meta.field_meta, onlyReview, request.schema_version, request.specifications, selected]);
  const canAnalyze =
    request.requirement_confirmed &&
    request.quote_count >= 2 &&
    request.unresolved_field_count === 0 &&
    request.status !== "approved" &&
    request.status !== "no_award";
  const analyzeDisabledReason = request.status === "approved" || request.status === "no_award"
    ? "本任务已结束，不能再发起比价"
    : !request.requirement_confirmed
      ? "请先保存采购需求的人工确认"
      : request.quote_count < 2
        ? "至少需要 2 家报价才能比价"
        : request.unresolved_field_count > 0
          ? "还有 " + request.unresolved_field_count + " 项报价字段待复核"
          : null;
  const reviewSummary = !request.requirement_confirmed
    ? request.unresolved_field_count
      ? `需求待人工确认，${request.unresolved_field_count} 项待复核`
      : "需求待人工确认"
    : request.unresolved_field_count
      ? `${request.unresolved_field_count} 项待复核`
      : "字段已就绪";
  const reviewGuidance = !request.requirement_confirmed
    ? request.unresolved_field_count
      ? "请先保存采购需求人工确认，并逐项复核报价字段"
      : "请先保存采购需求人工确认"
    : request.quote_count < 2
      ? "至少需要 2 家报价"
      : "金额将由确定性规则核算";

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
                if (files.length) void onUpload(files);
              }}
            />
          </label>
        </header>

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
            <strong>{reviewSummary}</strong>
            <span>{reviewGuidance}</span>
          </div>
          <button
            className="proc-button primary"
            type="button"
            disabled={!canAnalyze || busy === "analyze"}
            title={analyzeDisabledReason || "字段已就绪，开始确定性比价"}
            onClick={() => void onAnalyze()}
          >
            {busy === "analyze" ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
            {busy === "analyze" ? "分析中" : "开始比价"}
          </button>
        </div>
        {error ? <p className="proc-inline-error" role="alert">{error}</p> : null}
      </section>

      <section className="proc-quote-review" aria-label="报价字段复核">
        {selected ? (
          <>
            <header className="proc-panel-head review-head">
              <div>
                <h2>报价字段与来源证据</h2>
                <span>{selected.review_count ? "待人工复核" : "字段可用于比价"}</span>
              </div>
              <div className="proc-review-tools">
                <label className="proc-filter-toggle" title="仅显示待复核字段">
                  <Filter size={14} />
                  <input type="checkbox" checked={onlyReview} onChange={(event) => setOnlyReview(event.target.checked)} />
                  <span>仅待复核</span>
                </label>
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
            <details className="proc-evidence-panel" aria-label="报价证据详情">
              <summary>
                <span className="proc-evidence-badge"><ShieldCheck size={14} />证据已验证</span>
                <small>原件指纹与解析用时</small>
              </summary>
              <div className="proc-source-strip">
                <span><ShieldCheck size={14} />原件 SHA-256</span>
                <code title={selected.source_sha256}>{selected.source_sha256.slice(0, 20)}</code>
                <span>解析用时 {Math.round(selected.processing_ms)} ms</span>
                <span>快照 v{selected.extracted.schema_version} · {selected.parser_version}</span>
              </div>
            </details>
            <div className="proc-field-table">
              <div className="proc-field-table-head"><span>字段</span><span>抽取值 / 修正值</span><span>来源证据</span></div>
              {entries.map(({ name, field, meta: fieldMeta }) => (
                <FieldEditor
                  key={name}
                  name={name}
                  meta={fieldMeta}
                  field={field}
                  saving={busy === `field:${selected.id}:${name}`}
                  onSave={(value) => onCorrect(selected.id, name, value)}
                />
              ))}
              {!entries.length ? (
                <div className="proc-no-review"><CheckCircle2 size={18} />没有待复核字段</div>
              ) : null}
            </div>
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
