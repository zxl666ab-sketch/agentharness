import { Check, ChevronDown, ChevronRight, ClipboardEdit, LoaderCircle, Plus, ShieldCheck, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";

import type { CreateProcurementRequest, ProcurementRequest, RequirementSpecification } from "./types";

type Props = {
  request: ProcurementRequest;
  busy: boolean;
  error?: string | null;
  onSave: (payload: CreateProcurementRequest) => Promise<void>;
};

type FormState = {
  title: string;
  itemName: string;
  itemUnit: string;
  quantity: string;
  width: string;
  length: string;
  thickness: string;
  material: string;
  color: string;
  printColors: string;
  maxLeadDays: string;
  invoiceRequired: boolean;
  sizeTolerance: string;
  thicknessTolerance: string;
  maxUnitCost: string;
  destination: string;
  requiredDeliveryDate: string;
  requiredDeliveryDateAuto: boolean;
  rates: string;
};

type DynamicSpecForm = {
  key: string;
  label: string;
  type: "number" | "text" | "boolean";
  value: string | boolean;
  unit: string;
  match: "exact" | "tolerance" | "range" | "gte" | "lte";
  priority: "hard" | "preference";
  tolerance: string;
  min: string;
  max: string;
};

function asText(value: unknown) {
  return value == null ? "" : String(value);
}

function datePart(value: unknown) {
  return /^\d{4}-\d{2}-\d{2}/.exec(asText(value))?.[0] || "";
}

function addDays(value: string, daysText: string) {
  const days = Number(daysText);
  if (!value || !Number.isInteger(days) || days < 1) return "";
  const result = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(result.getTime())) return "";
  result.setUTCDate(result.getUTCDate() + days);
  return result.toISOString().slice(0, 10);
}

function derivedDeliveryDate(request: ProcurementRequest, maxLeadDays: string) {
  return addDays(datePart(request.created_at), maxLeadDays);
}

function initialState(request: ProcurementRequest): FormState {
  const specifications = request.specifications as Record<string, unknown>;
  const constraints = request.constraints as Record<string, unknown>;
  const rates = (constraints.fx_rates || {}) as Record<string, unknown>;
  const maxLeadDays = asText(constraints.max_lead_days);
  const explicitDeliveryDate = asText(constraints.required_delivery_date);
  const automaticDeliveryDate = derivedDeliveryDate(request, maxLeadDays);
  const requiredDeliveryDate = explicitDeliveryDate || automaticDeliveryDate;
  return {
    title: request.title,
    itemName: request.item_name,
    itemUnit: request.unit,
    quantity: asText(request.quantity),
    width: asText(specifications.width_mm),
    length: asText(specifications.length_mm),
    thickness: asText(specifications.thickness_um),
    material: asText(specifications.material),
    color: asText(specifications.color),
    printColors: asText(specifications.print_colors),
    maxLeadDays,
    invoiceRequired: constraints.invoice_required !== false,
    sizeTolerance: asText(constraints.size_tolerance_mm),
    thicknessTolerance: asText(constraints.thickness_tolerance_um),
    maxUnitCost: asText(constraints.max_landed_unit_cost),
    destination: asText(constraints.destination),
    requiredDeliveryDate,
    requiredDeliveryDateAuto: !explicitDeliveryDate || explicitDeliveryDate === automaticDeliveryDate,
    rates: Object.entries(rates).map(([currency, value]) => `${currency}=${value}`).join(", "),
  };
}

function initialDynamicSpecs(request: ProcurementRequest): DynamicSpecForm[] {
  return Object.entries(request.specifications || {}).map(([key, raw]) => {
    const value = raw && typeof raw === "object" && "type" in raw
      ? raw as { label?: string; type?: DynamicSpecForm["type"]; value?: unknown; unit?: string; match?: DynamicSpecForm["match"]; priority?: DynamicSpecForm["priority"]; tolerance?: unknown; min?: unknown; max?: unknown }
      : { label: key, type: "text" as const, value: String(raw ?? ""), match: "exact" as const, priority: "hard" as const };
    return {
      key,
      label: asText(value.label || key),
      type: value.type || "text",
      value: value.type === "boolean" ? Boolean(value.value) : asText(value.value),
      unit: asText(value.unit),
      match: value.match || "exact",
      priority: value.priority || "hard",
      tolerance: asText(value.tolerance),
      min: asText(value.min),
      max: asText(value.max),
    };
  });
}

function dynamicSpecPayload(rows: DynamicSpecForm[]): Record<string, RequirementSpecification> {
  const result: Record<string, RequirementSpecification> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    const item: RequirementSpecification = {
      label: row.label.trim() || key,
      type: row.type,
      match: row.match,
      priority: row.priority,
    };
    if (row.type === "boolean") item.value = row.value === true || row.value === "true";
    else item.value = String(row.value).trim();
    if (row.type === "number") {
      item.unit = row.unit.trim();
      if (row.match === "tolerance") item.tolerance = row.tolerance.trim();
      if (row.match === "range") {
        item.min = row.min.trim();
        item.max = row.max.trim();
        delete item.value;
      }
    }
    result[key] = item;
  }
  return result;
}

function numberValue(value: string, label: string, integer = false) {
  const parsed = Number(value.trim());
  if (!Number.isFinite(parsed) || (integer && !Number.isInteger(parsed))) {
    throw new Error(`${label}必须是有效${integer ? "整数" : "数字"}`);
  }
  return parsed;
}

function parseRates(value: string, baseCurrency: string) {
  const rates: Record<string, number> = {};
  for (const entry of value.split(",").map((item) => item.trim()).filter(Boolean)) {
    const [rawCurrency, rawRate] = entry.split("=").map((item) => item.trim());
    if (!rawCurrency || !rawRate || !/^[A-Za-z]{3}$/.test(rawCurrency)) {
      throw new Error("汇率格式应为 USD=7.2，多个币种用逗号分隔");
    }
    rates[rawCurrency.toUpperCase()] = numberValue(rawRate, "汇率");
  }
  rates[baseCurrency] = 1;
  return rates;
}

export function RequirementReview({ request, busy, error, onSave }: Props) {
  const [form, setForm] = useState<FormState>(() => initialState(request));
  const [dynamicSpecs, setDynamicSpecs] = useState<DynamicSpecForm[]>(() => initialDynamicSpecs(request));
  const [localError, setLocalError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(request.status === "approved");
  const constraints = request.constraints as Record<string, unknown>;
  const baseCurrency = asText(constraints.base_currency || "CNY").toUpperCase();
  const terminal = request.status === "approved" || request.status === "no_award";

  useEffect(() => {
    setForm(initialState(request));
    setDynamicSpecs(initialDynamicSpecs(request));
  }, [request]);

  useEffect(() => {
    setCollapsed(request.status === "approved" || request.status === "no_award");
  }, [request.id, request.status]);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({
      ...current,
      [key]: value,
      ...(key === "requiredDeliveryDate" ? { requiredDeliveryDateAuto: false } : {}),
    }));
    setLocalError(null);
    setNotice(null);
  }

  function updateMaxLeadDays(value: string) {
    setForm((current) => ({
      ...current,
      maxLeadDays: value,
      requiredDeliveryDate: current.requiredDeliveryDateAuto
        ? derivedDeliveryDate(request, value)
        : current.requiredDeliveryDate,
    }));
    setLocalError(null);
    setNotice(null);
  }

  function updateDynamicSpec(index: number, field: keyof DynamicSpecForm, value: string | boolean) {
    setDynamicSpecs((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row));
    setLocalError(null);
    setNotice(null);
  }

  function addDynamicSpec() {
    setDynamicSpecs((current) => [
      ...current,
      {
        key: `spec_${current.length + 1}`,
        label: "",
        type: "text",
        value: "",
        unit: "",
        match: "exact",
        priority: "hard",
        tolerance: "",
        min: "",
        max: "",
      },
    ]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const maxUnitCost = form.maxUnitCost.trim();
      const basePayload = {
        title: form.title.trim(),
        category: request.schema_version === 2 ? request.category : "ecommerce_packaging",
        item_name: form.itemName.trim(),
        quantity: request.schema_version === 2 ? form.quantity.trim() : numberValue(form.quantity, "采购数量", true),
        unit: request.schema_version === 2 ? form.itemUnit.trim() : "piece",
        constraints: {
          base_currency: baseCurrency,
          fx_rates: parseRates(form.rates, baseCurrency),
          max_lead_days: numberValue(form.maxLeadDays, "最长交期", true),
          invoice_required: form.invoiceRequired,
          ...(request.schema_version === 2 ? {} : {
            size_tolerance_mm: numberValue(form.sizeTolerance, "尺寸公差"),
            thickness_tolerance_um: numberValue(form.thicknessTolerance, "厚度公差"),
          }),
          ...(maxUnitCost ? { max_landed_unit_cost: numberValue(maxUnitCost, "到货单价上限") } : {}),
          destination: form.destination.trim(),
          ...(form.requiredDeliveryDate.trim() ? { required_delivery_date: form.requiredDeliveryDate } : {}),
        },
      };
      const payload: CreateProcurementRequest = request.schema_version === 2
        ? { schema_version: 2, ...basePayload, specifications: dynamicSpecPayload(dynamicSpecs) }
        : {
            ...basePayload,
            specifications: {
              width_mm: numberValue(form.width, "宽度"),
              length_mm: numberValue(form.length, "长度"),
              thickness_um: numberValue(form.thickness, "厚度"),
              material: form.material.trim(),
              color: form.color.trim(),
              print_colors: numberValue(form.printColors, "印刷色数", true),
            },
          };
      setLocalError(null);
      await onSave(payload);
      setNotice("采购需求已人工确认，旧比价快照已失效。");
    } catch (value) {
      setLocalError(value instanceof Error ? value.message : String(value));
    }
  }

  return (
    <section className="proc-requirement-review" aria-label="采购需求人工复核">
      <header className="proc-panel-head">
        <div><ClipboardEdit size={16} /><h2>采购需求结构化复核</h2><span>AI 结果可修改</span></div>
        <div className="proc-requirement-head-actions">
          <span className="proc-requirement-proof"><ShieldCheck size={14} />人工确认后才进入比价</span>
          <button
            className="proc-collapse-button"
            type="button"
            aria-expanded={!collapsed}
            aria-controls={`proc-requirement-review-${request.id}`}
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
            {collapsed ? "展开" : "收起"}
          </button>
        </div>
      </header>
      {!collapsed ? (
        <form id={`proc-requirement-review-${request.id}`} className="proc-request-form" onSubmit={(event) => void submit(event)}>
          <fieldset>
            <legend>采购目标</legend>
            <label className="proc-field proc-span-2"><span>任务名称</span><input value={form.title} onChange={(event) => update("title", event.target.value)} disabled={busy || terminal} /></label>
            <label className="proc-field"><span>物料名称</span><input value={form.itemName} onChange={(event) => update("itemName", event.target.value)} disabled={busy || terminal} /></label>
            <label className="proc-field"><span>采购数量</span><input type={request.schema_version === 2 ? "text" : "number"} inputMode="decimal" min="1" step="any" value={form.quantity} onChange={(event) => update("quantity", event.target.value)} disabled={busy || terminal} /></label>
          </fieldset>
          {request.schema_version === 2 ? (
            <fieldset>
              <legend>动态规格与交付</legend>
              <label className="proc-field"><span>采购品类</span><input value={request.category} readOnly disabled={terminal || busy} /></label>
              <label className="proc-field"><span>采购单位</span><input value={form.itemUnit} onChange={(event) => update("itemUnit", event.target.value)} disabled={terminal || busy} /></label>
              <div className="proc-dynamic-spec-list proc-span-2">
                {dynamicSpecs.map((row, index) => (
                  <div className="proc-dynamic-spec-row" key={`${row.key}-${index}`}>
                    <div className="proc-dynamic-spec-main">
                      <label className="proc-field"><span>规格名称</span><input value={row.label} placeholder="例如：长度" onChange={(event) => updateDynamicSpec(index, "label", event.target.value)} disabled={terminal || busy} /></label>
                      <label className="proc-field"><span>规格值</span>{row.type === "boolean" ? <span className="proc-dynamic-spec-bool"><input type="checkbox" checked={row.value === true} onChange={(event) => updateDynamicSpec(index, "value", event.target.checked)} disabled={terminal || busy} /><span>{row.value === true ? "是" : "否"}</span></span> : <input value={String(row.value)} onChange={(event) => updateDynamicSpec(index, "value", event.target.value)} disabled={terminal || busy} />}</label>
                      {row.type === "number" ? <label className="proc-field"><span>单位</span><input value={row.unit} placeholder="如 mm、m" onChange={(event) => updateDynamicSpec(index, "unit", event.target.value)} disabled={terminal || busy} /></label> : null}
                      <label className="proc-field"><span>匹配要求</span><select value={row.match} onChange={(event) => updateDynamicSpec(index, "match", event.target.value as DynamicSpecForm["match"])} disabled={terminal || busy}><option value="exact">完全一致</option><option value="tolerance">允许公差</option><option value="range">范围</option><option value="gte">不小于</option><option value="lte">不大于</option></select></label>
                      <label className="proc-field"><span>优先级</span><select value={row.priority} onChange={(event) => updateDynamicSpec(index, "priority", event.target.value as DynamicSpecForm["priority"])} disabled={terminal || busy}><option value="hard">必须满足</option><option value="preference">偏好</option></select></label>
                      <button className="proc-icon-button compact" type="button" title="删除规格" aria-label={`删除规格 ${row.label || row.key}`} onClick={() => setDynamicSpecs((current) => current.filter((_item, rowIndex) => rowIndex !== index))} disabled={terminal || busy}><X size={14} /></button>
                    </div>
                    <details className="proc-dynamic-spec-advanced">
                      <summary>高级设置</summary>
                      <div className="proc-dynamic-spec-advanced-grid">
                        <label className="proc-field"><span>内部属性键</span><input value={row.key} onChange={(event) => updateDynamicSpec(index, "key", event.target.value)} disabled={terminal || busy} /></label>
                        <label className="proc-field"><span>数据类型</span><select value={row.type} onChange={(event) => updateDynamicSpec(index, "type", event.target.value as DynamicSpecForm["type"])} disabled={terminal || busy}><option value="text">文本</option><option value="number">数值</option><option value="boolean">布尔</option></select></label>
                        {row.type === "number" && row.match === "tolerance" ? <label className="proc-field"><span>公差</span><input value={row.tolerance} onChange={(event) => updateDynamicSpec(index, "tolerance", event.target.value)} disabled={terminal || busy} /></label> : null}
                        {row.type === "number" && row.match === "range" ? <><label className="proc-field"><span>最小值</span><input value={row.min} onChange={(event) => updateDynamicSpec(index, "min", event.target.value)} disabled={terminal || busy} /></label><label className="proc-field"><span>最大值</span><input value={row.max} onChange={(event) => updateDynamicSpec(index, "max", event.target.value)} disabled={terminal || busy} /></label></> : null}
                      </div>
                    </details>
                  </div>
                ))}
                <button className="proc-button secondary" type="button" onClick={addDynamicSpec} disabled={terminal || busy}><Plus size={14} />新增规格</button>
              </div>
              <label className="proc-field"><span>最长交期（天）</span><input type="number" min="1" step="1" value={form.maxLeadDays} onChange={(event) => updateMaxLeadDays(event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>送货地点</span><input value={form.destination} onChange={(event) => update("destination", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>要求到货日期{form.requiredDeliveryDateAuto ? "（自动推导）" : "（人工填写）"}</span><input type="date" value={form.requiredDeliveryDate} onChange={(event) => update("requiredDeliveryDate", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-check"><input type="checkbox" checked={form.invoiceRequired} onChange={(event) => update("invoiceRequired", event.target.checked)} disabled={terminal || busy} /><span><strong>需要开票</strong><small>作为供应商硬性约束参与资格判断</small></span></label>
            </fieldset>
          ) : (
            <fieldset>
              <legend>规格与交付</legend>
              <label className="proc-field"><span>宽度（mm）</span><input type="number" min="0" step="any" value={form.width} onChange={(event) => update("width", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>长度（mm）</span><input type="number" min="0" step="any" value={form.length} onChange={(event) => update("length", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>厚度（µm）</span><input type="number" min="0" step="any" value={form.thickness} onChange={(event) => update("thickness", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>材质</span><input value={form.material} onChange={(event) => update("material", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>颜色</span><input value={form.color} onChange={(event) => update("color", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>印刷色数</span><input type="number" min="0" max="12" step="1" value={form.printColors} onChange={(event) => update("printColors", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>最长交期（天）</span><input type="number" min="1" step="1" value={form.maxLeadDays} onChange={(event) => updateMaxLeadDays(event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>送货地点</span><input value={form.destination} onChange={(event) => update("destination", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>要求到货日期{form.requiredDeliveryDateAuto ? "（自动推导）" : "（人工填写）"}</span><input type="date" value={form.requiredDeliveryDate} onChange={(event) => update("requiredDeliveryDate", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-check"><input type="checkbox" checked={form.invoiceRequired} onChange={(event) => update("invoiceRequired", event.target.checked)} disabled={terminal || busy} /><span><strong>需要开票</strong><small>作为供应商硬性约束参与资格判断</small></span></label>
            </fieldset>
          )}
          <fieldset>
            <legend>决策约束</legend>
            {request.schema_version !== 2 ? <>
              <label className="proc-field"><span>尺寸公差（mm）</span><input type="number" min="0" step="any" value={form.sizeTolerance} onChange={(event) => update("sizeTolerance", event.target.value)} disabled={terminal || busy} /></label>
              <label className="proc-field"><span>厚度公差（µm）</span><input type="number" min="0" step="any" value={form.thicknessTolerance} onChange={(event) => update("thicknessTolerance", event.target.value)} disabled={terminal || busy} /></label>
            </> : null}
            <label className="proc-field"><span>到货单价上限（{baseCurrency}）</span><input type="number" min="0" step="any" value={form.maxUnitCost} onChange={(event) => update("maxUnitCost", event.target.value)} disabled={terminal || busy} /></label>
            <label className="proc-field proc-span-2"><span>汇率（外币=本位币，例如 USD=7.2）</span><input value={form.rates} onChange={(event) => update("rates", event.target.value)} disabled={terminal || busy} /></label>
          </fieldset>
          {localError || error ? <p className="proc-inline-error" role="alert">{localError || error}</p> : null}
          {notice ? <p className="proc-inline-success" role="status"><Check size={15} />{notice}</p> : null}
          <div className="proc-modal-actions">
            <button className="proc-button primary" type="submit" disabled={busy || terminal}>
              {busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}
              {busy ? "保存中" : "保存人工确认"}
            </button>
          </div>
        </form>
      ) : (
        <div className="proc-requirement-collapsed" role="status">
          <Check size={15} />
          <span>采购需求已确认，点击“展开”可查看或核对结构化字段。</span>
        </div>
      )}
    </section>
  );
}
