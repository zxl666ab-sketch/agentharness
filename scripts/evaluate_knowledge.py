"""Offline retrieval-quality evaluation for the historical-price RAG index.

Builds a frozen corpus from the existing procurement scenario library
(``eval_truth.json``, 31 quotes), hides the target deal per case, retrieves
with the deterministic hybrid retriever (0 model calls), and reports
recall@k, precision@k, MRR, top-1 hit rate. When a ``--data-dir`` is given it
also folds knowledge feedback events (viewed/adopted) into the report.

Usage:
    uv run python scripts/evaluate_knowledge.py
    uv run python scripts/evaluate_knowledge.py --output docs/evidence/rag-retrieval-2026-08-07.md
    uv run python scripts/evaluate_knowledge.py --data-dir output/dev-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from agentharness.procurement.evaluation import load_frozen_truth
from agentharness.rag.chunking import specification_summary
from agentharness.rag.retriever import Retriever
from agentharness.storage.sqlite import Storage

EVAL_AS_OF = date(2026, 8, 7)

_MATERIAL_ALIASES = {
    "PE": ("pe", "聚乙烯", "polyethylene"),
    "PVC": ("pvc", "聚氯乙烯", "polyvinyl chloride"),
    "PP": ("pp", "聚丙烯", "polypropylene"),
    "PET": ("pet", "聚对苯二甲酸乙二醇酯"),
    "PLA": ("pla", "聚乳酸"),
}
_COLOR_ALIASES = {
    "white": ("白色", "白", "white"),
    "black": ("黑色", "黑", "black"),
    "transparent": ("透明", "transparent", "clear"),
    "red": ("红色", "红", "red"),
    "blue": ("蓝色", "蓝", "blue"),
}


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _canonical_material(value: Any) -> str | None:
    text = _norm(value)
    for canonical, aliases in _MATERIAL_ALIASES.items():
        if any(alias in text for alias in aliases):
            return canonical
    return None


def _canonical_color(value: Any) -> str | None:
    text = _norm(value)
    for canonical, aliases in _COLOR_ALIASES.items():
        if any(alias in text for alias in aliases):
            return canonical
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except Exception:  # noqa: BLE001 - eval-only coercion
        return None
    return result if result.is_finite() else None


def _spec_compatible(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    size_tolerance_mm: Decimal,
    thickness_tolerance_um: Decimal,
) -> bool:
    if _canonical_material(left.get("material")) != _canonical_material(
        right.get("material")
    ):
        return False
    if _canonical_color(left.get("color")) != _canonical_color(right.get("color")):
        return False
    if _decimal(left.get("print_colors")) != _decimal(right.get("print_colors")):
        return False
    for dimension, tolerance in (
        ("width_mm", size_tolerance_mm),
        ("length_mm", size_tolerance_mm),
        ("thickness_um", thickness_tolerance_um),
    ):
        left_value = _decimal(left.get(dimension))
        right_value = _decimal(right.get(dimension))
        if left_value is None or right_value is None:
            continue
        if abs(left_value - right_value) > tolerance:
            return False
    return True


def build_frozen_corpus() -> list[dict[str, Any]]:
    """Build one chunk per frozen quote; specs come from parsed truth fields."""
    truth = load_frozen_truth()
    corpus: list[dict[str, Any]] = []
    for index, case in enumerate(truth["quotes"]):
        fields = case.get("fields", {})
        specs = {
            "width_mm": fields.get("width_mm"),
            "length_mm": fields.get("length_mm"),
            "thickness_um": fields.get("thickness_um"),
            "material": fields.get("material"),
            "color": fields.get("color"),
            "print_colors": fields.get("print_colors"),
        }
        supplier = str(fields.get("supplier_name") or case.get("filename") or f"供应商{index}")
        chunk_sha = f"{index:064x}"
        corpus.append(
            {
                "chunk_sha256": chunk_sha,
                "request_id": f"frozen-{case['id']}",
                "quote_id": f"quote-{case['id']}",
                "artifact_id": f"artifact-{case['id']}",
                "artifact_sha256": f"source-{case['id']}".ljust(64, "0"),
                "request_reference": f"RFQ-20260727-{str(case['id'])[-6:].upper()}",
                "supplier_name": supplier,
                "item_name": "快递袋",
                "category": "ecommerce_packaging",
                "specifications": specs,
                "unit_price": str(fields.get("unit_price") or ""),
                "currency": str(fields.get("currency") or "CNY"),
                "landed_unit_cost": str(case.get("expected_landed_total_base") or ""),
                "lead_days": fields.get("lead_time_days"),
                "moq": fields.get("moq"),
                "decision": "approved",
                "decision_at": f"2026-07-{index % 28 + 1:02d}T00:00:00+00:00",
                "content": (
                    f"快递袋 {specification_summary(specs)} 供应商 {supplier} "
                    f"成交价 {fields.get('unit_price')} {fields.get('currency')}"
                ),
                "quality_flags": [],
            }
        )
    return corpus


def evaluate_frozen(
    *,
    as_of: date = EVAL_AS_OF,
    candidate_limit: int = 20,
    top_k_values: tuple[int, ...] = (1, 3, 5),
) -> dict[str, Any]:
    corpus = build_frozen_corpus()
    size_tolerance = Decimal("2")
    thickness_tolerance = Decimal("3")
    per_case: list[dict[str, Any]] = []
    for target in corpus:
        query_specs = target["specifications"]
        relevant = [
            chunk
            for chunk in corpus
            if chunk["chunk_sha256"] != target["chunk_sha256"]
            and _spec_compatible(
                query_specs,
                chunk["specifications"],
                size_tolerance_mm=size_tolerance,
                thickness_tolerance_um=thickness_tolerance,
            )
        ]
        relevant_ids = {chunk["chunk_sha256"] for chunk in relevant}
        retriever = Retriever(_MemoryStorage(corpus))
        results = retriever.retrieve(
            request={
                "id": f"query-{target['quote_id']}",
                "item_name": "快递袋",
                "specifications": query_specs,
                "constraints": {
                    "size_tolerance_mm": "2",
                    "thickness_tolerance_um": "3",
                },
            },
            limit=5,
            candidate_limit=candidate_limit,
            now=as_of,
        )
        ranked_ids = [str(item["chunk_sha256"]) for item in results]
        hits = [chunk_id for chunk_id in ranked_ids if chunk_id in relevant_ids]
        first_rank = ranked_ids.index(hits[0]) + 1 if hits else None
        per_case.append(
            {
                "case_id": target["quote_id"],
                "relevant_count": len(relevant_ids),
                "top_k": ranked_ids,
                "hits": hits,
                "first_relevant_rank": first_rank,
                "top1_hit": bool(hits and ranked_ids[0] in relevant_ids),
                "top1_id": ranked_ids[0] if ranked_ids else None,
                "top1_supplier": next(
                    (
                        item["supplier_name"]
                        for item in results
                        if str(item["chunk_sha256"]) == (ranked_ids[0] if ranked_ids else None)
                    ),
                    None,
                ),
            }
        )

    evaluated = [case for case in per_case if case["relevant_count"] > 0]
    aggregates: dict[str, float] = {}
    for k in top_k_values:
        recall_sum = 0.0
        precision_sum = 0.0
        for case in evaluated:
            top = case["top_k"][:k]
            hit_count = sum(1 for item in top if item in {h for h in case["hits"]})
            recall_sum += hit_count / case["relevant_count"]
            precision_sum += hit_count / max(1, len(top))
        aggregates[f"recall@{k}"] = round(recall_sum / len(evaluated), 4) if evaluated else 0.0
        aggregates[f"precision@{k}"] = round(precision_sum / len(evaluated), 4) if evaluated else 0.0
    mrrs = [
        1.0 / case["first_relevant_rank"]
        for case in evaluated
        if case["first_relevant_rank"] is not None
    ]
    aggregates["mrr"] = round(sum(mrrs) / len(evaluated), 4) if evaluated else 0.0
    aggregates["top1_hit_rate"] = round(
        sum(1 for case in evaluated if case["top1_hit"]) / len(evaluated), 4
    ) if evaluated else 0.0
    failures = [
        case
        for case in evaluated
        if not case["top1_hit"] and case["relevant_count"] > 0 and case["top_k"]
    ]
    return {
        "as_of": as_of.isoformat(),
        "corpus_size": len(corpus),
        "evaluated_cases": len(evaluated),
        "cases_without_relevant": len(per_case) - len(evaluated),
        "aggregates": aggregates,
        "per_case": per_case,
        "failure_cases": failures[:10],
    }


class _MemoryStorage:
    """Minimal storage stand-in exposing ``.rag.list_chunks/fts_search``."""

    def __init__(self, corpus: list[dict[str, Any]]) -> None:
        self.rag = _MemoryRag(corpus)


class _MemoryRag:
    def __init__(self, corpus: list[dict[str, Any]]) -> None:
        self._corpus = corpus

    def list_chunks(self, *, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        return self._corpus[offset : offset + limit]

    def fts_search(self, query: str, *, limit: int = 100) -> list[dict[str, Any]]:
        terms = [term for term in query.replace("，", " ").split() if term]
        hits = []
        for chunk in self._corpus:
            haystack = " ".join(
                str(chunk.get(key) or "")
                for key in ("item_name", "supplier_name", "content")
            ).casefold()
            if all(term.casefold() in haystack for term in terms):
                hits.append(chunk)
            if len(hits) >= limit:
                break
        return hits


def feedback_stats(data_dir: Path) -> dict[str, Any]:
    storage = Storage(data_dir)
    try:
        retrieved_events = 0
        viewed = 0
        adopted = 0
        for event in storage.procurement.list_knowledge_feedback_events():
            if event["type"] == "knowledge_reference_viewed":
                viewed += 1
            elif event["type"] == "knowledge_reference_adopted":
                adopted += 1
        for request in storage.procurement.list_requests(limit=100_000):
            for event in storage.procurement.list_audit_events(str(request["id"])):
                if event["type"] == "knowledge_retrieved":
                    retrieved_events += int(event.get("payload", {}).get("injected_count") or 0)
        return {
            "retrieved_injected": retrieved_events,
            "viewed": viewed,
            "adopted": adopted,
            "view_rate": round(viewed / retrieved_events, 4) if retrieved_events else None,
            "adopt_rate": round(adopted / retrieved_events, 4) if retrieved_events else None,
        }
    finally:
        storage.close()


def render_report(result: dict[str, Any], feedback: dict[str, Any] | None) -> str:
    aggregates = result["aggregates"]
    lines = [
        "# 历史行情 RAG 检索质量冻结评测（2026-08-07）",
        "",
        "> 0 模型调用，完全确定性可复现；与确定性冻结评测（617/620、31/31、0 漏检）分层呈现。",
        "",
        "## 冻结集",
        "",
        f"- 语料：{result['corpus_size']} 条历史成交 chunk（来自 `eval_truth.json` 31 份报价的规格真值）",
        f"- 评测案例：{result['evaluated_cases']}（有相关历史可判定的案例）",
        f"- 无相关历史的案例：{result['cases_without_relevant']}（不计入分子分母）",
        f"- 检索基准日：{result['as_of']}；隐藏目标后检索（目标 chunk 不计入相关集）",
        "",
        "## 指标（RAG 层，不与确定性指标混用）",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
    ]
    for key, value in aggregates.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")
    lines += [
        "> 解读：相关集按「同规格族」（材质/颜色/印刷色数规范化相等 + 尺寸容差内）定义，",
        "因此同一规格族内多条历史时 recall@k 天然偏低；top-1 命中率与 MRR 是主要质量信号。",
        "",
    ]
    if feedback is not None:
        lines += [
            "## 反馈闭环（1.6 反馈事件，调权依据）",
            "",
            f"- 展示注入参考数：{feedback['retrieved_injected']}",
            f"- knowledge_reference_viewed：{feedback['viewed']}",
            f"- knowledge_reference_adopted：{feedback['adopted']}",
            f"- 参考查看率：{feedback['view_rate'] if feedback['view_rate'] is not None else '无数据'}",
            f"- 参考采纳率：{feedback['adopt_rate'] if feedback['adopt_rate'] is not None else '无数据'}",
            "",
        ]
    else:
        lines += [
            "## 反馈闭环",
            "",
            "未提供 --data-dir，暂无反馈事件数据（参考查看率/采纳率待 1.8/1.9 实测后补充）。",
            "",
        ]
    lines += [
        "## 失败案例（top-1 未命中相关历史）",
        "",
    ]
    failures = result["failure_cases"]
    if failures:
        for case in failures:
            lines.append(
                f"- `{case['case_id']}`：相关 {case['relevant_count']} 条，"
                f"top-1 为 `{case['top1_id']}`（{case['top1_supplier'] or '未知'}），"
                f"首个相关排名 {case['first_relevant_rank']}"
            )
    else:
        lines.append("- 无（top-1 全部命中相关历史）")
    lines += [
        "",
        "## 调权建议",
        "",
        "- 指标用于校验 rerank 权重（规格 0.50 / 时间 0.15 / 口碑 0.20 / 质量 0.15）的冻结基线；",
        "- 若 recall@5 低于 0.9，优先放宽 FTS 关键词（加入规格摘要词）与结构化容差；",
        "- 反馈采纳率上升的供应商权重由 `_reputation_score` 自动上调；",
        "- 本报告不改变确定性冻结评测数字，两者分层归档。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/evidence/rag-retrieval-2026-08-07.md"),
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args()
    result = evaluate_frozen()
    feedback = feedback_stats(args.data_dir) if args.data_dir else None
    report = render_report(result, feedback)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(json.dumps({"aggregates": result["aggregates"], "corpus": result["corpus_size"]}, ensure_ascii=False, indent=2))
    print(f"报告已写入：{args.output}")


if __name__ == "__main__":
    sys.exit(main())
