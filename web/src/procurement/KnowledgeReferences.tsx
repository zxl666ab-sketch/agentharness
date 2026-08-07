import { BookOpen, ChevronDown, Eye, ThumbsUp, X } from "lucide-react";
import { useState } from "react";

import type { KnowledgeFeedbackAction, KnowledgeReference } from "./types";

const DEFAULT_VISIBLE = 3;
const EXPANDED_VISIBLE = 5;

type Props = {
  references: KnowledgeReference[];
  onFeedback?: (chunkId: string, action: KnowledgeFeedbackAction) => void;
};

function money(value: string, currency: string) {
  if (!value) return "—";
  try {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: currency || "CNY",
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    }).format(Number(value));
  } catch {
    return `${value} ${currency || ""}`.trim();
  }
}

export function KnowledgeReferences({ references, onFeedback }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [openSource, setOpenSource] = useState<string | null>(null);

  if (!references.length) {
    return (
      <section className="proc-knowledge-block empty" aria-label="历史成交参考">
        <header className="proc-knowledge-head">
          <BookOpen size={15} />
          <h3>历史成交参考</h3>
        </header>
        <p className="proc-knowledge-empty">暂无相似历史成交</p>
      </section>
    );
  }

  const visible = expanded ? references.slice(0, EXPANDED_VISIBLE) : references.slice(0, DEFAULT_VISIBLE);
  const expandable = references.length > DEFAULT_VISIBLE;
  const open = openSource ? references.find((item) => item.chunk_id === openSource) : null;

  return (
    <section className="proc-knowledge-block" aria-label="历史成交参考">
      <header className="proc-knowledge-head">
        <BookOpen size={15} />
        <h3>历史成交参考</h3>
        <span className="proc-knowledge-note">来自本地已成交记录，仅作参考，不影响本次确定性结论</span>
      </header>
      <div className="proc-knowledge-table-wrap">
        <table className="proc-knowledge-table">
          <thead>
            <tr>
              <th>供应商</th>
              <th>成交价</th>
              <th>到货成本</th>
              <th>成交日期</th>
              <th>是否成交</th>
              <th>来源</th>
              <th aria-label="操作" />
            </tr>
          </thead>
          <tbody>
            {visible.map((reference) => (
              <tr key={reference.chunk_id}>
                <td>
                  <strong>{reference.supplier_name || "—"}</strong>
                  <small>{reference.specification_summary || reference.item_name || "规格未填写"}</small>
                </td>
                <td>
                  <strong>{money(reference.unit_price, reference.currency)}</strong>
                  <small>{reference.currency || "—"}</small>
                </td>
                <td>
                  <strong>{money(reference.landed_unit_cost, reference.currency)}</strong>
                  <small>到货单价</small>
                </td>
                <td>{reference.decision_at ? reference.decision_at.slice(0, 10) : "—"}</td>
                <td>
                  {reference.decision === "approved" ? (
                    <span className="proc-knowledge-status pass">已成交</span>
                  ) : (
                    <span className="proc-knowledge-status">未成交</span>
                  )}
                </td>
                <td>
                  <button
                    className="proc-knowledge-source"
                    type="button"
                    title={`查看来源 ${reference.request_reference}`}
                    onClick={() => {
                      setOpenSource(reference.chunk_id);
                      onFeedback?.(reference.chunk_sha256, "viewed");
                    }}
                  >
                    {reference.request_reference || "来源未知"}
                  </button>
                </td>
                <td>
                  <span className="proc-knowledge-actions">
                    <button
                      className="proc-knowledge-action"
                      type="button"
                      title="查看详情"
                      aria-label={`查看详情 ${reference.supplier_name}`}
                      onClick={() => {
                        setOpenSource(reference.chunk_id);
                        onFeedback?.(reference.chunk_sha256, "viewed");
                      }}
                    >
                      <Eye size={13} />查看详情
                    </button>
                    <button
                      className="proc-knowledge-action"
                      type="button"
                      title="标记为有帮助"
                      aria-label={`有帮助 ${reference.supplier_name}`}
                      onClick={() => onFeedback?.(reference.chunk_sha256, "adopted")}
                    >
                      <ThumbsUp size={13} />有帮助
                    </button>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {expandable ? (
        <button
          className="proc-knowledge-more"
          type="button"
          onClick={() => setExpanded((value) => !value)}
        >
          <ChevronDown size={14} className={expanded ? "proc-knowledge-chevron open" : "proc-knowledge-chevron"} />
          {expanded ? "收起" : `展开全部 ${references.length} 条`}
        </button>
      ) : null}

      {open ? (
        <div className="proc-modal-backdrop" role="presentation">
          <section className="proc-knowledge-dialog" role="dialog" aria-modal="true" aria-labelledby="knowledge-source-title">
            <header>
              <div><BookOpen size={18} /><h2 id="knowledge-source-title">历史成交参考详情</h2></div>
              <button className="proc-icon-button" type="button" title="关闭" aria-label="关闭" onClick={() => setOpenSource(null)}><X size={18} /></button>
            </header>
            <dl className="proc-knowledge-detail">
              <div><dt>来源编号</dt><dd>{open.request_reference || "—"}</dd></div>
              <div><dt>供应商</dt><dd>{open.supplier_name || "—"}</dd></div>
              <div><dt>规格摘要</dt><dd>{open.specification_summary || "—"}</dd></div>
              <div><dt>成交价</dt><dd>{money(open.unit_price, open.currency)}</dd></div>
              <div><dt>到货单价</dt><dd>{money(open.landed_unit_cost, open.currency)}</dd></div>
              <div><dt>成交日期</dt><dd>{open.decision_at ? open.decision_at.slice(0, 10) : "—"}</dd></div>
              <div><dt>成交结论</dt><dd>{open.decision === "approved" ? "已成交" : "未成交"}</dd></div>
              <div><dt>来源哈希</dt><dd><code>{open.source_sha256 || "—"}</code></dd></div>
              {open.note ? <div><dt>备注</dt><dd>{open.note}</dd></div> : null}
            </dl>
          </section>
        </div>
      ) : null}
    </section>
  );
}
