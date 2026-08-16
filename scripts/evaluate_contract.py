"""P3-2 合同评测：条款提取 precision/recall + 风险分级 + 草拟字段注入一致性硬校验。

数据集：procurement-service/src/main/resources/frozen/frozen-evaluation-contract.json
（synthetic 合成合同，README 已如实标注）。输出到 output/procurement-evaluation/contract/。
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from agentharness.procurement.contract_drafting import build_contract_draft

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "procurement-service" / "src" / "main" / "resources" / "frozen" / "frozen-evaluation-contract.json"
OUT = ROOT / "output" / "procurement-evaluation" / "contract"


def _amount_in_text(text: str) -> str | None:
    match = re.search(r"金额为人民币\s*([0-9.]+)", text)
    return match.group(1) if match else None


def _lead_days_in_text(text: str) -> str | None:
    match = re.search(r"生效后\s*(\d+)\s*天", text)
    return match.group(1) if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description="P3-2 contract evaluation (synthetic dataset)")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = dataset["cases"]

    true_positive = 0
    false_positive = 0
    false_negative = 0
    consistency_violations: list[str] = []
    risk_violations: list[str] = []
    per_case: list[dict] = []

    for case in cases:
        # contract_id 唯一化（不能与真实合同 id 冲突；build_contract_draft 只要求非空）
        inputs = {**case["inputs"], "contract_id": f"eval-{case['id']}"}
        draft = build_contract_draft(inputs)
        produced = {clause["title"] for clause in draft["clauses"]}
        expected = set(case["expected_clauses"])
        tp = len(produced & expected)
        fp = len(produced - expected)
        fn = len(expected - produced)
        true_positive += tp
        false_positive += fp
        false_negative += fn

        # 风险分级校验
        risk_by_title = {clause["title"]: clause["risk_level"] for clause in draft["clauses"]}
        for title, level in case["expected_risk"].items():
            if risk_by_title.get(title) != level:
                risk_violations.append(f"{case['id']}:{title}")

        # 字段注入一致性：草拟文本金额/交期 == 注入值（硬校验）
        amount = _amount_in_text(draft["draft_text"])
        lead = _lead_days_in_text(draft["draft_text"])
        if amount != inputs["amount"]:
            consistency_violations.append(f"{case['id']}:amount({amount} != {inputs['amount']})")
        if lead != str(inputs["lead_days"]):
            consistency_violations.append(f"{case['id']}:lead_days({lead} != {inputs['lead_days']})")

        per_case.append({
            "id": case["id"],
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "risk_ok": not any(title in case["expected_risk"] and risk_by_title.get(title) != level
                               for title, level in case["expected_risk"].items()),
        })

    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0

    evidence = {
        "dataset": dataset["dataset"],
        "dataset_label": dataset["dataset_label"],
        "synthetic": dataset["synthetic"],
        "case_count": len(cases),
        "clause_extraction": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
        },
        "risk_level_violations": risk_violations,
        "field_injection_consistency": {
            "violations": consistency_violations,
            "passed": not consistency_violations,
        },
        "acceptance": {
            "clause_precision_recall_100pct": precision == 1.0 and recall == 1.0,
            "field_injection_consistent": not consistency_violations,
            "risk_levels_correct": not risk_violations,
        },
        "per_case": per_case,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "contract-evaluation.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "clause_precision": round(precision, 4),
        "clause_recall": round(recall, 4),
        "field_injection_consistent": not consistency_violations,
        "risk_levels_correct": not risk_violations,
        "acceptance": evidence["acceptance"],
    }, ensure_ascii=False, indent=2))
    ok = precision == 1.0 and recall == 1.0 and not consistency_violations and not risk_violations
    print("CONTRACT EVAL:", "ALL PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
