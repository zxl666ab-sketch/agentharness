from __future__ import annotations

from scripts.evaluate_knowledge import build_frozen_corpus, evaluate_frozen, render_report


def test_frozen_corpus_has_31_unique_chunks() -> None:
    corpus = build_frozen_corpus()
    assert len(corpus) == 31
    hashes = {chunk["chunk_sha256"] for chunk in corpus}
    assert len(hashes) == 31
    assert all(chunk["decision"] == "approved" for chunk in corpus)


def test_evaluate_frozen_is_reproducible() -> None:
    first = evaluate_frozen()
    second = evaluate_frozen()
    assert first["corpus_size"] == 31
    assert first["evaluated_cases"] >= 1
    assert first["aggregates"] == second["aggregates"]
    for key in ("recall@1", "recall@3", "recall@5", "precision@1", "mrr", "top1_hit_rate"):
        assert 0.0 <= first["aggregates"][key] <= 1.0
    # Every evaluated case either hit top-1 or is listed as a failure.
    assert first["failure_cases"] == [
        case for case in first["per_case"]
        if not case["top1_hit"] and case["relevant_count"] > 0 and case["top_k"]
    ][:10]


def test_report_renders_metrics_and_sections() -> None:
    result = evaluate_frozen()
    report = render_report(result, None)
    for section in ("冻结集", "指标（RAG 层", "反馈闭环", "失败案例", "调权建议"):
        assert section in report
    assert "| recall@1 |" in report
    assert "| top1_hit_rate |" in report

    feedback = {
        "retrieved_injected": 10,
        "viewed": 4,
        "adopted": 2,
        "view_rate": 0.4,
        "adopt_rate": 0.2,
    }
    with_feedback = render_report(result, feedback)
    assert "参考查看率：0.4" in with_feedback
    assert "参考采纳率：0.2" in with_feedback
