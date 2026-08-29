import { useRef } from "react";

import type { UseQueryResult } from "@tanstack/react-query";
import { LoaderCircle, Save, Settings, ShieldCheck } from "lucide-react";

import { Button, Drawer } from "../components/ui";
import type { ProcurementModelConfig, ProcurementModelConfigUpdate } from "./types";
import { errorText } from "./useWorkbenchActions";

type Props = {
  query: UseQueryResult<ProcurementModelConfig, Error>;
  form: ProcurementModelConfigUpdate;
  busy: boolean;
  error: string | null;
  notice: string | null;
  onClose: () => void;
  onFieldChange: <K extends keyof ProcurementModelConfigUpdate>(
    field: K,
    value: ProcurementModelConfigUpdate[K]
  ) => void;
  onSave: () => Promise<void>;
};

/** API / 模型配置抽屉（Phase 3：统一 Drawer 骨架）。 */
export function ConfigDrawer({ query, form, busy, error, notice, onClose, onFieldChange, onSave }: Props) {
  const firstFieldRef = useRef<HTMLSelectElement | null>(null);
  return (
    <Drawer
      titleId="proc-config-title"
      title="API / 模型配置"
      subtitle="仅影响之后新启动的采购 Agent 运行"
      icon={<Settings size={17} />}
      closeLabel="关闭配置"
      onClose={onClose}
      initialFocusRef={firstFieldRef}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button variant="primary" icon={<Save size={15} />} loading={busy} disabled={query.isPending || query.isError} onClick={() => void onSave()}>
            保存配置
          </Button>
        </>
      }
    >
      {query.isPending ? <div className="proc-config-loading"><LoaderCircle className="spin" size={18} />正在读取当前配置…</div> : null}
      {query.isError ? (
        <div className="proc-config-error" role="alert">
          <strong>配置读取失败</strong>
          <span>{errorText(query.error)}</span>
          <Button variant="secondary" size="sm" onClick={() => void query.refetch()}>重新读取</Button>
        </div>
      ) : null}
      {!query.isPending && !query.isError ? (
        <>
          <section className="proc-config-section">
            <div className="proc-config-section-title"><strong>模型服务</strong><span>选择离线演示或 OpenAI 兼容接口</span></div>
                <label className="proc-field proc-span-2">
                  <span>Provider</span>
                  <select ref={firstFieldRef} value={form.provider} onChange={(event) => {
                    const provider = event.target.value as ProcurementModelConfigUpdate["provider"];
                    onFieldChange("provider", provider);
                    if (provider === "procurement_fake") onFieldChange("model", "procurement-fake-v1");
                  }}>
                    <option value="procurement_fake">离线演示（Fake Provider）</option>
                    <option value="openai">OpenAI 兼容 API</option>
                  </select>
                </label>
                <label className="proc-field proc-span-2">
                  <span>模型名称</span>
                  <input value={form.model} disabled={form.provider === "procurement_fake"} onChange={(event) => onFieldChange("model", event.target.value)} placeholder="例如 gpt-4o-mini" />
                </label>
                <label className="proc-field proc-span-2">
                  <span>API Base URL <small>可选，留空使用官方地址</small></span>
                  <input value={form.base_url} disabled={form.provider === "procurement_fake"} onChange={(event) => onFieldChange("base_url", event.target.value)} placeholder="例如 https://api.openai.com/v1" />
                </label>
                <label className="proc-field proc-span-2">
                  <span>API Key <small>{query.data?.api_key_preview ? `当前 ${query.data.api_key_preview}，留空保持不变` : "不会回显已保存的密钥"}</small></span>
                  <input type="password" autoComplete="new-password" disabled={form.provider === "procurement_fake"} value={form.api_key || ""} onChange={(event) => onFieldChange("api_key", event.target.value)} placeholder={query.data?.api_key_configured ? "留空保持当前密钥" : "请输入 API Key（可选）"} />
                </label>
                <label className="proc-field">
                  <span>API 模式</span>
                  <select value={form.api_mode} disabled={form.provider === "procurement_fake"} onChange={(event) => onFieldChange("api_mode", event.target.value as ProcurementModelConfigUpdate["api_mode"])}>
                    <option value="auto">自动判断</option>
                    <option value="responses">Responses API</option>
                    <option value="chat">Chat Completions</option>
                  </select>
                </label>
                <label className="proc-field">
                  <span>推理强度</span>
                  <select value={form.reasoning_effort} disabled={form.provider === "procurement_fake"} onChange={(event) => onFieldChange("reasoning_effort", event.target.value as ProcurementModelConfigUpdate["reasoning_effort"])}>
                    <option value="auto">自动</option>
                    <option value="none">none</option>
                    <option value="minimal">minimal</option>
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                    <option value="max">max</option>
                  </select>
                </label>
              </section>

              <section className={`proc-config-section ${form.provider === "procurement_fake" ? "disabled" : ""}`}>
                <div className="proc-config-section-title"><strong>成本保护</strong><span>按模型价格估算并限制单次 Run</span></div>
                <label className="proc-field">
                  <span>输入价格（USD / 1M tokens）</span>
                  <input type="number" min="0" step="0.01" disabled={form.provider === "procurement_fake"} value={form.input_price_per_million_usd ?? ""} onChange={(event) => onFieldChange("input_price_per_million_usd", event.target.value === "" ? null : Number(event.target.value))} />
                </label>
                <label className="proc-field">
                  <span>输出价格（USD / 1M tokens）</span>
                  <input type="number" min="0" step="0.01" disabled={form.provider === "procurement_fake"} value={form.output_price_per_million_usd ?? ""} onChange={(event) => onFieldChange("output_price_per_million_usd", event.target.value === "" ? null : Number(event.target.value))} />
                </label>
                <label className="proc-field">
                  <span>缓存输入价格（USD / 1M tokens）</span>
                  <input type="number" min="0" step="0.01" disabled={form.provider === "procurement_fake"} value={form.cached_input_price_per_million_usd ?? ""} onChange={(event) => onFieldChange("cached_input_price_per_million_usd", event.target.value === "" ? null : Number(event.target.value))} />
                </label>
                <label className="proc-field">
                  <span>单次 Run 费用上限（USD）</span>
                  <input type="number" min="0" step="0.01" disabled={form.provider === "procurement_fake"} value={form.max_cost_usd ?? ""} onChange={(event) => onFieldChange("max_cost_usd", event.target.value === "" ? null : Number(event.target.value))} placeholder="留空表示不限" />
                </label>
              </section>

          <p className="proc-config-security"><span><ShieldCheck size={15} />密钥保存在本机采购服务配置中，GET 接口只返回脱敏状态。</span></p>
          {error ? <p className="proc-form-error" role="alert">{error}</p> : null}
          {notice ? <p className="proc-form-success" role="status">{notice}</p> : null}
        </>
      ) : null}
    </Drawer>
  );
}
