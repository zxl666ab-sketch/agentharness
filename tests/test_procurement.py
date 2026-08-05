from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from scripts import evaluate_procurement as evaluation_script

from agentharness.procurement.evaluation import (
    FROZEN_DATASET_NAME,
    FROZEN_TRUTH_SHA256,
    HUMAN_TRIAL_CASE_IDS,
    MIN_FROZEN_CASES,
    MIN_FROZEN_LAYOUTS,
    build_case_document,
    evaluate_frozen_cases,
    load_frozen_truth,
    recompute_approach_metrics,
    recompute_human_trial_metrics,
)
from agentharness.procurement.parsing import (
    MAX_FILE_BYTES,
    QuoteParseError,
    fields_requiring_review,
    parse_quote,
)


def _xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Quote"
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _standard_rows() -> list[list[object]]:
    return [
        ["供应商", "解析测试供应商"],
        ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
        ["币种", "CNY"],
        ["单价", "500"],
        ["计价数量", "1000"],
        ["税率", "13%"],
        ["是否含税", "是"],
        ["是否包邮", "是"],
        ["MOQ", "1000"],
        ["交期", "7"],
        ["是否可开票", "是"],
    ]


def test_quote_parser_preserves_negative_shipping_and_invoice_semantics() -> None:
    rows = _standard_rows()
    next(row for row in rows if row[0] == "是否包邮")[1] = "否"
    next(row for row in rows if row[0] == "是否可开票")[1] = "否"
    rows.extend([["运费", "600"], ["备注", "不包邮；不可开票"]])

    fields = parse_quote("negative-semantics.xlsx", _xlsx_bytes(rows))["fields"]

    assert fields["shipping_included"]["value"] is False
    assert fields["shipping_fee"]["value"] == "600"
    assert fields["supports_invoice"]["value"] is False
    assert fields["supports_invoice"]["status"] == "accepted"


@pytest.mark.parametrize(
    ("field", "label", "invalid_value"),
    [
        ("unit_price", "单价", "0"),
        ("moq", "MOQ", "-50"),
        ("lead_time_days", "交期", "-3"),
        ("lead_time_days", "交期", "1.9"),
    ],
)
def test_quote_parser_requires_review_for_invalid_business_numbers(
    field: str, label: str, invalid_value: str
) -> None:
    rows = _standard_rows()
    next(row for row in rows if row[0] == label)[1] = invalid_value

    extracted = parse_quote("invalid-number.xlsx", _xlsx_bytes(rows))

    assert extracted["fields"][field]["value"] is None
    assert extracted["fields"][field]["status"] == "needs_review"
    assert field in fields_requiring_review(extracted)


def test_quote_parser_distinguishes_conflicts_from_duplicate_evidence() -> None:
    conflicting = _standard_rows()
    conflicting.insert(4, ["单价", "900"])
    conflict = parse_quote("conflict.xlsx", _xlsx_bytes(conflicting))["fields"]["unit_price"]
    assert conflict["status"] == "needs_review"
    assert {item["value"] for item in conflict["conflicts"]} == {"500", "900"}

    duplicate = _standard_rows()
    duplicate.insert(4, ["单价", "500"])
    accepted = parse_quote("duplicate.xlsx", _xlsx_bytes(duplicate))["fields"]["unit_price"]
    assert accepted["status"] == "accepted"
    assert "conflicts" not in accepted


def test_quote_parser_preserves_per_ten_thousand_price_basis() -> None:
    rows = _standard_rows()
    next(row for row in rows if row[0] == "计价数量")[1] = "每10000个"
    assert parse_quote("basis.xlsx", _xlsx_bytes(rows))["fields"]["price_basis"]["value"] == 10_000


@pytest.mark.parametrize(
    ("filename", "document", "message"),
    [
        ("too-many-rows.xlsx", _xlsx_bytes([[f"row-{index}"] for index in range(501)]), "500"),
        ("too-many-columns.xlsx", _xlsx_bytes([[f"column-{index}" for index in range(41)]]), "40"),
    ],
)
def test_quote_parser_rejects_xlsx_outside_declared_dimensions(
    filename: str, document: bytes, message: str
) -> None:
    with pytest.raises(QuoteParseError, match=message):
        parse_quote(filename, document)


def test_xlsx_source_locator_uses_actual_data_cell_after_blank_row() -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "品名", "币种", "单价", "计价数量"],
            [None, None, None, None, None],
            ["坐标供应商", "PE 白色快递袋", "CNY", "500", "1000"],
        ]
    )
    extracted = parse_quote("source-coordinate.xlsx", document)
    assert extracted["fields"]["unit_price"]["source"]["locator"] == "Quote!D3"


def test_unparseable_valid_until_requires_human_review() -> None:
    rows = _standard_rows()
    rows.append(["报价有效期", "另行通知"])
    extracted = parse_quote("invalid-validity.xlsx", _xlsx_bytes(rows))
    assert extracted["fields"]["valid_until"]["status"] == "needs_review"
    assert "valid_until" in fields_requiring_review(extracted)


def test_quote_parser_rejects_unsupported_and_oversized_inputs() -> None:
    with pytest.raises(QuoteParseError, match="仅支持"):
        parse_quote("quote.csv", b"supplier,price")
    with pytest.raises(QuoteParseError, match="不得超过"):
        parse_quote("quote.pdf", b"x" * (MAX_FILE_BYTES + 1))


def test_each_frozen_layout_builds_deterministically_and_parses() -> None:
    truth = load_frozen_truth()
    by_layout = {case["layout"]: case for case in truth["quotes"]}
    assert len(by_layout) >= MIN_FROZEN_LAYOUTS
    for case in by_layout.values():
        for locale in ("en", "zh-CN"):
            first = build_case_document(case, locale=locale)
            assert first == build_case_document(case, locale=locale)
            parsed = parse_quote(case["filename"], first)
            assert parsed["document_kind"] == case["kind"]
            assert parsed["fields"]["unit_price"]["value"] == case["fields"]["unit_price"]


def test_frozen_evaluation_combines_python_extraction_with_java_golden_rules() -> None:
    result = evaluate_frozen_cases()
    assisted = result["approaches"]["agent_assisted"]

    assert result["dataset"] == FROZEN_DATASET_NAME
    assert result["truth_sha256"] == FROZEN_TRUTH_SHA256
    assert result["case_count"] == MIN_FROZEN_CASES == 31
    assert result["metrics"]["field_extraction"] == {
        "correct": 617,
        "total": 620,
        "accuracy": 0.9952,
    }
    assert result["metrics"]["item_matching"]["correct"] == 31
    assert result["metrics"]["cost_calculation"]["correct"] == 31
    assert result["metrics"]["hard_constraint_miss"]["missed"] == 0
    assert result["metrics"]["hard_constraint_miss"]["expected_violations"] == 17
    assert result["metrics"]["incorrect_eligible_selection"]["count"] == 0
    assert all(result["acceptance"].values())
    assert recompute_approach_metrics(assisted["raw"]) == assisted["metrics"]


def test_evaluation_verifier_accepts_current_frozen_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw_path = tmp_path / "raw-results.json"
    raw_path.write_text(json.dumps(evaluate_frozen_cases(), ensure_ascii=False), encoding="utf-8")
    evaluation_script.verify_evaluation(SimpleNamespace(input=raw_path))
    assert "原始评测结果复算通过" in capsys.readouterr().out


def test_human_trial_metrics_are_recomputed_from_raw_observations() -> None:
    truth = load_frozen_truth()
    cases_by_id = {case["id"]: case for case in truth["quotes"]}
    cases = [cases_by_id[case_id] for case_id in HUMAN_TRIAL_CASE_IDS]
    metrics = recompute_human_trial_metrics(
        {
            "truth_sha256": FROZEN_TRUTH_SHA256,
            "case_ids": list(HUMAN_TRIAL_CASE_IDS),
            "active_time_seconds": 600,
            "rework_count": 2,
            "recommended_quote_id": truth["expected_recommended_quote_id"],
            "observations": [
                {
                    "case_id": case["id"],
                    "landed_total_base": case["expected_landed_total_base"],
                    "item_match": case["expected_match"],
                    "exclusion_codes": case["expected_exclusions"],
                }
                for case in cases
            ],
        }
    )
    assert metrics["cost_calculation"]["accuracy"] == 1
    assert metrics["hard_constraint_miss"]["miss_rate"] == 0
    assert metrics["human_experiment"]["error_count"] == 0
    assert metrics["human_experiment"]["rework_count"] == 2
