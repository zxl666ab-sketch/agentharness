import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardPaste,
  Copy,
  ExternalLink,
  FileJson,
  Filter,
  Upload,
} from "lucide-react";
import {
  compareReports,
  filterFailed,
  parseEvalReport,
  type BaselineCompareSummary,
  type EvalReport,
} from "./parseReport";

type Props = {
  onOpenRun: (runId: string) => void;
};

const STORAGE_KEY = "agentharness.eval.lastReportJson";
const STORAGE_NAME_KEY = "agentharness.eval.lastReportName";

const QUICK_CMD = `uv run agentharness eval evals/smoke.yaml --data-dir output/eval-data --report-json output/eval-smoke.json`;

export function EvalReportView({ onOpenRun }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [report, setReport] = useState<EvalReport | null>(null);
  const [baseline, setBaseline] = useState<EvalReport | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [copied, setCopied] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const baseFileRef = useRef<HTMLInputElement>(null);

  // Restore last successful report so reopening Eval is not empty.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = parseEvalReport(raw);
      if (parsed.ok) {
        setReport(parsed.report);
        setFileName(localStorage.getItem(STORAGE_NAME_KEY) || "上次加载的报告");
        setHint("已恢复上次加载的报告。可继续「查看轨迹」或重新选择文件。");
      }
    } catch {
      // ignore storage errors
    }
  }, []);

  const compare: BaselineCompareSummary | null = useMemo(() => {
    if (!report || !baseline) return null;
    return compareReports(report, baseline);
  }, [report, baseline]);

  const rows = useMemo(() => {
    if (!report) return [];
    return filterFailed(report.results, onlyFailed);
  }, [report, onlyFailed]);

  const failedCount = report
    ? report.results.filter((r) => !r.passed).length
    : 0;

  const loadText = useCallback(
    (text: string, asBaseline: boolean, name?: string) => {
      const parsed = parseEvalReport(text);
      if (!parsed.ok) {
        setError(parsed.error);
        setHint(null);
        return;
      }
      setError(null);
      if (asBaseline) {
        setBaseline(parsed.report);
        setHint(`已加载 baseline：${parsed.report.suite}（${parsed.report.total} cases）`);
        return;
      }
      setReport(parsed.report);
      const label = name || "粘贴的报告";
      setFileName(label);
      setHint(
        `已加载「${parsed.report.suite}」· ${parsed.report.passed}/${parsed.report.total} 通过。` +
          (failedCountOr(parsed.report) > 0
            ? " 失败行点右侧「查看轨迹」。"
            : " 全部通过。")
      );
      try {
        localStorage.setItem(STORAGE_KEY, text);
        localStorage.setItem(STORAGE_NAME_KEY, label);
      } catch {
        // ignore
      }
    },
    []
  );

  async function onFile(file: File | undefined, asBaseline: boolean) {
    if (!file) return;
    const text = await file.text();
    loadText(text, asBaseline, file.name);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    void onFile(file, false);
  }

  async function copyCmd() {
    try {
      await navigator.clipboard.writeText(QUICK_CMD);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("复制失败，请手动选中命令复制");
    }
  }

  return (
    <div
      className={`eval-view ${dragOver ? "drag-over" : ""}`}
      data-testid="eval-view"
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
    >
      <div className="eval-toolbar">
        <div className="eval-toolbar-actions">
          <button
            type="button"
            className="eval-button primary"
            onClick={() => fileRef.current?.click()}
            data-testid="eval-load-primary"
          >
            <Upload size={14} aria-hidden="true" />
            选择 JSON 报告
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(e) => {
              void onFile(e.target.files?.[0], false);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            className="eval-button"
            onClick={() => baseFileRef.current?.click()}
            title="可选：对比上一份报告"
          >
            <FileJson size={14} aria-hidden="true" />
            对比 baseline
          </button>
          <input
            ref={baseFileRef}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(e) => {
              void onFile(e.target.files?.[0], true);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            className="eval-button"
            onClick={() => setPasteOpen((v) => !v)}
          >
            <ClipboardPaste size={14} aria-hidden="true" />
            粘贴
          </button>
          {report && (
            <label className="eval-filter">
              <Filter size={14} aria-hidden="true" />
              <input
                type="checkbox"
                checked={onlyFailed}
                onChange={(e) => setOnlyFailed(e.target.checked)}
              />
              仅失败{failedCount ? ` (${failedCount})` : ""}
            </label>
          )}
        </div>
        {report && (
          <span className="eval-suite-name" data-testid="eval-suite-name">
            {fileName ? `${fileName} · ` : ""}
            {report.suite}
          </span>
        )}
      </div>

      {pasteOpen && (
        <div className="eval-paste">
          <textarea
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
            placeholder="把 output/eval-smoke.json 的全部内容粘到这里…"
            rows={8}
            data-testid="eval-paste"
          />
          <div className="eval-paste-actions">
            <button
              type="button"
              className="eval-button primary"
              onClick={() => {
                loadText(pasteText, false, "粘贴的报告");
                setPasteOpen(false);
              }}
            >
              加载这段 JSON
            </button>
            <button
              type="button"
              className="eval-button"
              onClick={() => setPasteOpen(false)}
            >
              取消
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="eval-error" role="alert" data-testid="eval-error">
          <AlertTriangle size={14} aria-hidden="true" />
          {error}
        </div>
      )}

      {hint && !error && (
        <div className="eval-hint" data-testid="eval-hint">
          <CheckCircle2 size={14} aria-hidden="true" />
          {hint}
        </div>
      )}

      {!report && (
        <div
          className={`eval-empty ${dragOver ? "is-drag" : ""}`}
          data-testid="eval-empty"
          onClick={() => fileRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") fileRef.current?.click();
          }}
        >
          <div className="eval-empty-hero">
            <Upload size={28} aria-hidden="true" />
            <h2>把 eval 报告拖到这里，或点此选择文件</h2>
            <p>本页只读 JSON，不自动跑评测。先在终端生成报告，再打开即可。</p>
          </div>

          <ol className="eval-steps">
            <li>
              <strong>终端生成报告</strong>
              <div className="eval-cmd-row">
                <code className="eval-cmd">{QUICK_CMD}</code>
                <button
                  type="button"
                  className="eval-button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void copyCmd();
                  }}
                >
                  <Copy size={13} aria-hidden="true" />
                  {copied ? "已复制" : "复制"}
                </button>
              </div>
            </li>
            <li>
              <strong>Web 用同一数据目录启动</strong>
              <code className="eval-cmd">
                uv run agentharness web --data-dir output/eval-data
              </code>
            </li>
            <li>
              <strong>回到本页，点上方绿色按钮选</strong>
              <code>output/eval-smoke.json</code>
            </li>
          </ol>

          <p className="eval-empty-foot">
            看轨迹必须 eval 与 web 的 <code>--data-dir</code> 一致；默认临时目录跑完就删。
          </p>
        </div>
      )}

      {report && (
        <>
          <div className="eval-metrics" data-testid="eval-metrics">
            <Metric label="用例数" value={String(report.total)} />
            <Metric label="通过" value={String(report.passed)} tone="ok" />
            <Metric
              label="失败"
              value={String(report.total - report.passed)}
              tone={report.passed < report.total ? "bad" : undefined}
            />
            <Metric
              label="通过率"
              value={`${(report.pass_rate * 100).toFixed(1)}%`}
            />
            <Metric label="均分" value={report.mean_score.toFixed(3)} />
            <Metric
              label="总 tokens"
              value={report.total_tokens.toLocaleString()}
            />
            <Metric
              label="均延迟"
              value={`${report.mean_latency_s.toFixed(3)}s`}
            />
          </div>

          {compare && (
            <div className="eval-baseline" data-testid="eval-baseline">
              <h3>相对 baseline</h3>
              <ul>
                <li>
                  新增失败:{" "}
                  {compare.new_failures.length
                    ? compare.new_failures.join(", ")
                    : "无"}
                </li>
                <li>
                  score 下降:{" "}
                  {compare.score_drops.length
                    ? compare.score_drops
                        .map(
                          (d) =>
                            `${d.case_id} (${d.baseline.toFixed(2)}→${d.current.toFixed(2)})`
                        )
                        .join("; ")
                    : "无"}
                </li>
                <li>
                  tokens: {compare.token_delta.baseline} →{" "}
                  {compare.token_delta.current} (
                  {(compare.token_delta.ratio_increase * 100).toFixed(1)}%)
                </li>
                <li>
                  latency: {compare.latency_delta.baseline.toFixed(3)}s →{" "}
                  {compare.latency_delta.current.toFixed(3)}s (
                  {(compare.latency_delta.ratio_increase * 100).toFixed(1)}%)
                </li>
                <li>新 case 数: {compare.new_case_count}</li>
              </ul>
            </div>
          )}

          <div className="eval-table-wrap">
            <table className="eval-table" data-testid="eval-table">
              <thead>
                <tr>
                  <th>用例</th>
                  <th>结果</th>
                  <th>分数</th>
                  <th>tokens</th>
                  <th>步骤</th>
                  <th>耗时</th>
                  <th>状态</th>
                  <th>原因</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.case_id}
                    className={row.passed ? "pass" : "fail"}
                    data-testid={`eval-row-${row.case_id}`}
                  >
                    <td className="mono">{row.case_id}</td>
                    <td>
                      {row.passed ? (
                        <span className="eval-pill ok">通过</span>
                      ) : (
                        <span className="eval-pill bad">失败</span>
                      )}
                    </td>
                    <td>{row.score.toFixed(2)}</td>
                    <td>{row.total_tokens}</td>
                    <td>{row.steps}</td>
                    <td>{row.latency_s.toFixed(3)}s</td>
                    <td>{row.status}</td>
                    <td className="eval-reasons">
                      {row.reasons.length ? row.reasons.join("; ") : "—"}
                    </td>
                    <td>
                      {row.run_id ? (
                        <button
                          type="button"
                          className="eval-link"
                          onClick={() => onOpenRun(row.run_id)}
                          title={`打开 run ${row.run_id}`}
                        >
                          <ExternalLink size={13} aria-hidden="true" />
                          查看轨迹
                        </button>
                      ) : (
                        <span className="eval-muted" title="无 run_id 或 data-dir 已丢">
                          无轨迹
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length === 0 && (
              <div className="eval-empty-table">没有匹配的 case（试试取消「仅失败」）</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function failedCountOr(report: EvalReport): number {
  return report.results.filter((r) => !r.passed).length;
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "ok" | "bad";
}) {
  return (
    <div className={`eval-metric ${tone || ""}`}>
      <small>{label}</small>
      <strong>{value}</strong>
    </div>
  );
}
