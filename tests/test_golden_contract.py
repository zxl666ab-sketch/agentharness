"""Cross-language golden contract regression.

Both Java (FrozenComparisonContractTest) and Python read the SAME
contracts/golden/frozen-comparison-v3.json. This test mirrors the Java
assertions (31/31 match, 31/31 landed totals, zero missed hard constraints,
zero incorrect eligible selections) so the shared golden artifact is enforced
from both sides.
"""

from __future__ import annotations

import json

from agentharness.procurement.evaluation import (
    FROZEN_DATASET_NAME,
    FROZEN_TRUTH_SHA256,
    JAVA_GOLDEN_PATH,
    load_frozen_truth,
)


def _load_contract() -> dict:
    return json.loads(JAVA_GOLDEN_PATH.read_text(encoding="utf-8"))


def test_java_golden_contract_is_locked_to_frozen_truth():
    contract = _load_contract()
    truth = load_frozen_truth()
    assert contract["dataset"] == FROZEN_DATASET_NAME == truth["name"]
    assert contract["truth_sha256"] == FROZEN_TRUTH_SHA256


def test_java_golden_covers_all_frozen_quotes():
    contract = _load_contract()
    truth = load_frozen_truth()
    expected = {str(case["id"]) for case in truth["quotes"]}
    actual = {str(item["quote_id"]) for item in contract["full_comparison"]["quotes"]}
    assert actual == expected


def test_java_golden_rows_match_quote_inputs_expectations():
    contract = _load_contract()
    by_supplier = {item["supplier_name"]: item for item in contract["full_comparison"]["quotes"]}
    matching = 0
    amounts = 0
    missed_hard = 0
    incorrect_eligible = 0
    for input_case in contract["quote_inputs"]:
        supplier = input_case["supplier_name"]
        row = by_supplier[supplier]
        actual_match = bool(row["match"]["passed"])
        if actual_match == bool(input_case["expected_match"]):
            matching += 1
        if str(row["cost"]["landed_total_base"]) == str(input_case["expected_landed_total_base"]):
            amounts += 1
        expected_exclusions = {str(x) for x in input_case.get("expected_exclusions", [])}
        actual_exclusions = {str(x["code"]) for x in row["exclusion_reasons"]}
        missing = expected_exclusions - actual_exclusions
        missed_hard += len(missing)
        if expected_exclusions and bool(row["eligible"]):
            incorrect_eligible += 1

    assert len(contract["quote_inputs"]) == 31
    assert matching == 31
    assert amounts == 31
    assert missed_hard == 0
    assert incorrect_eligible == 0
