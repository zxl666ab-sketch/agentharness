import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Bot, CheckCircle2, Clock3, FileUp, LoaderCircle, RefreshCw, Send, X } from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { procurementApi } from "./api";
import type {
  HumanInteraction,
  HumanInteractionArtifact,
  HumanInteractionField,
  HumanInteractionOption,
} from "./types";

type Props = {
  interaction: HumanInteraction;
  onChanged?: () => Promise<void> | void;
};

type FieldValue = string | boolean | string[];

function newKey() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `interaction-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function optionValue(option: HumanInteractionOption) {
  return typeof option === "string" ? option : option.value;
}

function optionLabel(option: HumanInteractionOption) {
  return typeof option === "string" ? option : option.label || option.value;
}

function optionDescription(option: HumanInteractionOption) {
  return typeof option === "string" ? null : option.description || option.source || null;
}

function initialValues(interaction: HumanInteraction): Record<string, FieldValue> {
  if (interaction.answer_schema.type !== "field_review") return {};
  const saved = interaction.answer && typeof interaction.answer === "object" && !Array.isArray(interaction.answer)
    ? interaction.answer as Record<string, unknown>
    : {};
  return Object.fromEntries((interaction.answer_schema.fields || []).map((field) => {
    const value = saved[field.name];
    if (field.type === "boolean") return [field.name, typeof value === "boolean" ? value : false];
    if (field.type === "multiple_choice") return [field.name, Array.isArray(value) ? value.map(String) : []];
    return [field.name, value == null ? "" : String(value)];
  }));
}

function buildAnswer(
  interaction: HumanInteraction,
  fieldValues: Record<string, FieldValue>,
  scalarValue: FieldValue,
  artifacts: HumanInteractionArtifact[],
) {
  const schema = interaction.answer_schema;
  if (schema.type === "field_review") return Object.fromEntries(
    (schema.fields || []).map((field) => {
      const value = fieldValues[field.name];
      return [field.name, field.type === "number" && value !== "" ? Number(value) : value];
    }),
  );
  if (schema.type === "file_upload") return artifacts.map((item) => item.artifact_id);
  return schema.type === "number" && scalarValue !== "" ? Number(scalarValue) : scalarValue;
}

function validate(
  interaction: HumanInteraction,
  answer: unknown,
  artifacts: HumanInteractionArtifact[],
) {
  const schema = interaction.answer_schema;
  if (schema.type === "field_review") {
    const values = answer as Record<string, FieldValue>;
    const missing = (schema.fields || []).find((field) => {
      if (!field.required) return false;
      const value = values[field.name];
      return value === "" || value == null || Array.isArray(value) && value.length === 0;
    });
    if (missing) return `请填写${missing.label}`;
  } else if (schema.type === "file_upload") {
    if (!artifacts.length) return "请先上传补充文件";
  } else if (answer === "" || answer == null || Array.isArray(answer) && !answer.length) {
    return "请填写或选择回答";
  }
  return null;
}

function ChoiceInput({
  name,
  options,
  multiple,
  value,
  disabled,
  onChange,
}: {
  name: string;
  options: HumanInteractionOption[];
  multiple: boolean;
  value: FieldValue;
  disabled: boolean;
  onChange: (value: FieldValue) => void;
}) {
  const selected = Array.isArray(value) ? value : [String(value || "")];
  return <div className="proc-interaction-choices">
    {options.map((option) => {
      const optionId = `${name}-${optionValue(option)}`;
      const checked = selected.includes(optionValue(option));
      return <label key={optionValue(option)} htmlFor={optionId}>
        <input
          id={optionId}
          name={name}
          type={multiple ? "checkbox" : "radio"}
          checked={checked}
          disabled={disabled}
          onChange={() => {
            if (!multiple) onChange(optionValue(option));
            else onChange(checked
              ? selected.filter((item) => item !== optionValue(option))
              : [...selected.filter(Boolean), optionValue(option)]);
          }}
        />
        <span><strong>{optionLabel(option)}</strong>{optionDescription(option) ? <small>{optionDescription(option)}</small> : null}</span>
      </label>;
    })}
  </div>;
}

function FieldInput({
  field,
  value,
  disabled,
  onChange,
}: {
  field: HumanInteractionField;
  value: FieldValue;
  disabled: boolean;
  onChange: (value: FieldValue) => void;
}) {
  if (field.type === "boolean") {
    return <select aria-label={field.label} value={String(value)} disabled={disabled} onChange={(event) => onChange(event.target.value === "true")}>
      <option value="true">是</option><option value="false">否</option>
    </select>;
  }
  if (field.type === "single_choice" || field.type === "multiple_choice") {
    return <ChoiceInput
      name={field.name}
      options={field.options || []}
      multiple={field.type === "multiple_choice"}
      value={value}
      disabled={disabled}
      onChange={onChange}
    />;
  }
  return <div className="proc-interaction-input-unit">
    <input
      aria-label={field.label}
      type={field.type === "number" ? "number" : field.type === "date" ? "date" : "text"}
      value={String(value ?? "")}
      required={field.required}
      disabled={disabled}
      step={field.type === "number" ? "any" : undefined}
      onChange={(event) => onChange(event.target.value)}
    />
    {field.unit ? <span>{field.unit}</span> : null}
  </div>;
}

export function HumanInteractionPanel({ interaction, onChanged }: Props) {
  const queryClient = useQueryClient();
  const [fieldValues, setFieldValues] = useState<Record<string, FieldValue>>(() => initialValues(interaction));
  const [scalarValue, setScalarValue] = useState<FieldValue>(() => {
    if (interaction.answer_schema.type === "boolean") return false;
    if (interaction.answer_schema.type === "multiple_choice") return [];
    return interaction.answer == null ? "" : String(interaction.answer);
  });
  const [note, setNote] = useState(interaction.answer_note || "");
  const [artifacts, setArtifacts] = useState<HumanInteractionArtifact[]>([]);
  const [busy, setBusy] = useState<"answer" | "upload" | "cancel" | "retry" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const submission = useRef<{ fingerprint: string; key: string } | null>(null);
  const answered = interaction.status === "ANSWERED";
  const operationQuery = useQuery({
    queryKey: ["procurement-operation", interaction.operation_id],
    queryFn: () => procurementApi.operation(interaction.operation_id!),
    enabled: answered && !!interaction.operation_id,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["completed", "failed", "cancelled"].includes(status) ? false : 1_500;
    },
  });
  const operation = operationQuery.data;
  const fields = interaction.answer_schema.fields || [];
  const answer = useMemo(
    () => buildAnswer(interaction, fieldValues, scalarValue, artifacts),
    [artifacts, fieldValues, interaction, scalarValue],
  );

  useEffect(() => {
    if (interaction.status === "APPLIED") {
      void queryClient.invalidateQueries({ queryKey: ["procurement-request", interaction.task_id] });
      void queryClient.invalidateQueries({ queryKey: ["procurement-requests"] });
    }
  }, [interaction.status, interaction.task_id, queryClient]);

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["procurement-interactions", interaction.task_id] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-request", interaction.task_id] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
    ]);
    await onChanged?.();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const validationError = validate(interaction, answer, artifacts);
    if (validationError) { setError(validationError); return; }
    const payload = { answer, note: note.trim(), artifact_ids: artifacts.map((item) => item.artifact_id) };
    const fingerprint = JSON.stringify(payload);
    if (!submission.current || submission.current.fingerprint !== fingerprint) {
      submission.current = { fingerprint, key: newKey() };
    }
    setBusy("answer");
    setError(null);
    try {
      await procurementApi.answerInteraction(interaction.id, payload, submission.current.key);
      setNotice("回答已保存，Agent 将从当前步骤继续。");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "回答提交失败");
    } finally { setBusy(null); }
  }

  async function upload(file: File) {
    if (file.size > 5 * 1024 * 1024) { setError(`${file.name} 超过 5 MB 上限`); return; }
    setBusy("upload");
    setError(null);
    try {
      const uploaded = await procurementApi.uploadInteractionArtifact(interaction.id, file);
      setArtifacts((current) => current.some((item) => item.artifact_id === uploaded.artifact_id) ? current : [...current, uploaded]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "补充文件上传失败");
    } finally { setBusy(null); }
  }

  async function cancel() {
    if (!confirmCancel) { setConfirmCancel(true); return; }
    setBusy("cancel");
    setError(null);
    try {
      await procurementApi.cancelInteraction(interaction.id, "采购员取消任务");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "取消任务失败");
    } finally { setBusy(null); }
  }

  async function retry() {
    setBusy("retry");
    setError(null);
    try {
      await procurementApi.retryInteraction(interaction.id);
      await queryClient.invalidateQueries({ queryKey: ["procurement-operation", interaction.operation_id] });
      setNotice("已重新派发，回答内容不会重复写入。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "重新派发失败");
    } finally { setBusy(null); }
  }

  const statusContent = interaction.status === "ANSWERED"
    ? operation?.status === "failed" || operation?.status === "cancelled"
      ? { icon: <AlertTriangle size={18} />, title: "回答已保存，Agent 暂未恢复", body: operation.last_error || "恢复任务派发失败，可以使用原回答重新派发。", tone: "danger" }
      : { icon: <LoaderCircle className="spin" size={18} />, title: "正在恢复 Agent", body: "回答已持久化，正在从原 Checkpoint 继续；无需重复填写。", tone: "working" }
    : interaction.status === "APPLIED"
      ? { icon: <CheckCircle2 size={18} />, title: "回答已应用", body: "Agent 已使用本次回答并继续处理后续步骤。", tone: "success" }
      : interaction.status === "STALE"
        ? { icon: <AlertTriangle size={18} />, title: "问题已失效", body: "采购输入已经变化，旧回答不会应用到新版本。", tone: "muted" }
        : interaction.status === "EXPIRED"
          ? { icon: <Clock3 size={18} />, title: "问题已过期", body: "系统没有使用猜测值，请重新生成问题或取消任务。", tone: "muted" }
          : interaction.status === "CANCELLED"
            ? { icon: <X size={18} />, title: "任务已取消", body: interaction.cancel_reason || "该问题不再接受回答。", tone: "muted" }
            : null;

  return <section className={`proc-interaction-card ${statusContent?.tone || "waiting"}`} aria-label="Agent 等待回答">
    <header>
      <span><Bot size={20} /></span>
      <div><small>Agent 主动提问 · {interaction.business_step}</small><h2>{interaction.question}</h2></div>
      <strong>{interaction.status === "WAITING" ? "等待你的回答" : statusContent?.title}</strong>
    </header>
    <div className="proc-interaction-context">
      <p><strong>为什么需要：</strong>{interaction.reason}</p>
      <p><strong>影响步骤：</strong>{interaction.business_step}</p>
      {interaction.related_fields.length ? <p><strong>关联字段：</strong>{interaction.related_fields.join("、")}</p> : null}
      {interaction.related_artifact_ids.length ? <p><strong>关联文件：</strong>{interaction.related_artifact_ids.length} 份</p> : null}
    </div>
    {interaction.status === "WAITING" ? <form onSubmit={(event) => void submit(event)}>
      {interaction.answer_schema.type === "field_review" ? <div className="proc-interaction-fields">
        {fields.map((field) => <label key={field.name}>
          <span>{field.label}{field.required ? <i>*</i> : null}</span>
          <FieldInput field={field} value={fieldValues[field.name] ?? ""} disabled={!!busy} onChange={(value) => setFieldValues((current) => ({ ...current, [field.name]: value }))} />
        </label>)}
      </div> : interaction.answer_schema.type === "single_choice" || interaction.answer_schema.type === "multiple_choice" ? <ChoiceInput
        name={interaction.id}
        options={interaction.answer_schema.options || []}
        multiple={interaction.answer_schema.type === "multiple_choice"}
        value={scalarValue}
        disabled={!!busy}
        onChange={setScalarValue}
      /> : interaction.answer_schema.type !== "file_upload" ? <label className="proc-interaction-scalar">
        <span>{interaction.answer_schema.label || "你的回答"}</span>
        {interaction.answer_schema.type === "boolean" ? <select value={String(scalarValue)} disabled={!!busy} onChange={(event) => setScalarValue(event.target.value === "true")}><option value="true">是</option><option value="false">否</option></select> : <input type={interaction.answer_schema.type === "number" ? "number" : interaction.answer_schema.type === "date" ? "date" : "text"} step={interaction.answer_schema.type === "number" ? "any" : undefined} value={String(scalarValue)} disabled={!!busy} onChange={(event) => setScalarValue(event.target.value)} />}
      </label> : null}
      <label className="proc-interaction-note"><span>补充说明（可选）</span><textarea value={note} maxLength={2_000} disabled={!!busy} onChange={(event) => setNote(event.target.value)} placeholder="补充会帮助 Agent 理解的业务背景" /></label>
      <div className="proc-interaction-artifacts">
        <label><FileUp size={15} />{busy === "upload" ? "正在上传" : "上传补充材料"}<input type="file" accept=".xlsx,.pdf" disabled={!!busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = ""; }} /></label>
        <small>文件先保存到 Java Artifact Store；支持 XLSX、PDF，单文件不超过 5 MB。</small>
        {artifacts.length ? <ul>{artifacts.map((item) => <li key={item.artifact_id}><span>{item.filename}</span><button type="button" aria-label={`移除 ${item.filename}`} onClick={() => setArtifacts((current) => current.filter((value) => value.artifact_id !== item.artifact_id))}><X size={13} /></button></li>)}</ul> : null}
      </div>
      {error ? <p className="proc-interaction-error" role="alert">{error}</p> : null}
      {notice ? <p className="proc-interaction-notice" role="status">{notice}</p> : null}
      <div className="proc-interaction-actions">
        <button className="proc-button primary" type="submit" disabled={!!busy}>{busy === "answer" ? <LoaderCircle className="spin" size={15} /> : <Send size={15} />}提交并继续</button>
        <button className="proc-button secondary" type="button" disabled={!!busy} onClick={() => setNotice("问题已持久保存，你可以稍后从工作台继续。")}>稍后处理</button>
        <button className={`proc-button ${confirmCancel ? "danger" : "ghost"}`} type="button" disabled={!!busy} onClick={() => void cancel()}>{busy === "cancel" ? "正在取消" : confirmCancel ? "再次点击确认取消" : "取消任务"}</button>
      </div>
    </form> : statusContent ? <div className="proc-interaction-status">
      <span>{statusContent.icon}</span><div><strong>{statusContent.title}</strong><p>{statusContent.body}</p></div>
      {interaction.status === "ANSWERED" && (operation?.status === "failed" || operation?.status === "cancelled") ? <button className="proc-button secondary" type="button" disabled={!!busy} onClick={() => void retry()}>{busy === "retry" ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}重新派发</button> : null}
    </div> : null}
    {interaction.status !== "WAITING" && error ? <p className="proc-interaction-error" role="alert">{error}</p> : null}
    {interaction.status !== "WAITING" && notice ? <p className="proc-interaction-notice" role="status">{notice}</p> : null}
  </section>;
}
