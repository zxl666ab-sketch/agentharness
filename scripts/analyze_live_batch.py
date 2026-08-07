"""Analyze a live-batch JSON report and flag anomalies (real bugs).

Usage:
  python scripts/analyze_live_batch.py output/procurement-evaluation/live-batch-*.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED_NO_COMPARISON = {"zifeng-dai-pe", "kuaididai-quanbu-taotai"}
EXPECTED_NO_APPROVAL = {"zifeng-dai-pe", "kuaididai-quanbu-taotai"}


def _has(name: str, *tokens: str) -> bool:
    return any(token in name for token in tokens)


def analyze(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    summary = data["summary"]
    print("== summary ==")
    for key in (
        "scenario_count",
        "analysis_success_rate",
        "approval_success_rate",
        "avg_model_turns",
        "avg_tool_calls",
        "total_tokens",
        "total_cost_usd",
        "duplicate_calls_total",
        "unauthorized_calls_total",
    ):
        print(f"  {key}: {summary.get(key)}")
    print("== per scenario ==")
    problems = 0
    for row in data["scenarios"]:
        name = row["scenario"]
        status = row.get("status")
        analysis = row.get("analysis_success")
        comparison = row.get("comparison_produced")
        approval = row.get("approval_success")
        error = row.get("error") or ""
        dup = row.get("duplicate_calls") or 0
        unauth = row.get("unauthorized_calls") or 0
        turns = row.get("model_turns")
        tools = row.get("total_tool_calls")
        flags = []
        capture = row.get("capture_succeeded")
        if analysis is not True:
            flags.append("ANALYSIS_FAILED")
        expected_no_cmp = _has(name, *EXPECTED_NO_COMPARISON)
        if comparison is False and not expected_no_cmp:
            if capture is False:
                flags.append("CAPTURE_FAILED")
            else:
                flags.append("NO_COMPARISON")
        if approval is False and not _has(name, *EXPECTED_NO_APPROVAL):
            flags.append("APPROVAL_FAILED")
        if dup:
            flags.append(f"DUP={dup}")
        if unauth:
            flags.append(f"UNAUTH={unauth}")
        if error and "verification requires human review" not in error:
            flags.append("UNEXPECTED_ERROR")
        if status not in ("completed", "require_human", "failed"):
            flags.append(f"STATUS={status}")
        expected = row.get("expected")
        rec = row.get("recommended_supplier")
        if expected and rec and str(expected) != str(rec):
            flags.append(f"WRONG_RECOMMENDATION(expected={expected},got={rec})")
        marker = row.get("final_text_tail") or ""
        if _has(name, "zhushe") and "【采购决策已验证】" in marker:
            flags.append("EARLY_VERIFIED_MARKER")
        if flags:
            problems += 1
        print(
            f"  {name}: status={status} analysis={analysis} comparison={comparison} "
            f"approval={approval} turns={turns} tools={tools} dup={dup} unauth={unauth}"
        )
        if rec is not None or row.get("eligible_count") is not None:
            print(
                f"      recommended={rec} eligible={row.get('eligible_count')} "
                f"excluded={row.get('excluded_count')} expected={expected}"
            )
        if error:
            print(f"      error: {error[:300]}")
        if flags:
            print(f"      FLAGS: {', '.join(flags)}")
    print(f"== problems: {problems} ==")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "output/procurement-evaluation/live-batch-latest.json")
    raise SystemExit(analyze(path))
