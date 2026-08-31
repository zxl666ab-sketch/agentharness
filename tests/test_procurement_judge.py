from __future__ import annotations

import json

import pytest

from agentharness.procurement.judge import (
    JUDGE_DIMENSIONS,
    PERTURBATION_KINDS,
    JudgeResponseError,
    build_judge_prompt,
    case_to_facts,
    cohen_kappa,
    ground_truth_verdict,
    human_verdicts,
    judge_case,
    judge_cases,
    judge_verdicts,
    parse_judge_response,
    perturb_facts,
    result_key,
    summarize_judge,
)


def _case(case_id: str = "q-alpha", **overrides):
    base = {
        "case_id": case_id,
        "layout": "xlsx_vertical",
        "anomalies": ["per_thousand_unit_price"],
        "fields": [
            {"name": "unit_price", "expected": "520", "final_value": "520", "final_correct": True},
            {"name": "moq", "expected": "5000", "final_value": "5000", "final_correct": True},
        ],
        "expected_match": True,
        "actual_match": True,
        "expected_landed_total_base": "5200.00",
        "actual_landed_total_base": "5200.00",
        "expected_exclusions": [],
        "detected_exclusions": [],
        "eligible": True,
    }
    base.update(overrides)
    return base


_REQUIREMENT = {
    "item_name": "快递袋",
    "quantity": 10000,
    "unit": "piece",
    "specifications": {"width_mm": "250"},
    "constraints": {"max_lead_days": 15, "invoice_required": True, "max_landed_unit_cost": "0.70"},
}


def _scores(**overrides):
    scores = dict.fromkeys(JUDGE_DIMENSIONS, 1.0)
    scores.update(overrides)
    return scores


def test_facts_hide_expected_answers() -> None:
    facts = case_to_facts(_case(), _REQUIREMENT)
    prompt = build_judge_prompt(facts)
    # Judge 可见事实与结论
    assert "快递袋" in prompt
    assert "5200.00" in prompt
    assert "max_lead_days" in prompt
    # 期望答案绝不泄漏给 Judge
    assert "expected" not in prompt.lower().replace("output_schema", "")
    for dim in JUDGE_DIMENSIONS:
        assert dim in prompt
    # 确定性：同输入逐字节一致
    assert prompt == build_judge_prompt(facts)


def test_ground_truth_verdict_equality() -> None:
    assert ground_truth_verdict(_case()) is True
    assert ground_truth_verdict(_case(actual_landed_total_base="5300.00")) is False
    assert ground_truth_verdict(_case(actual_match=False)) is False
    assert ground_truth_verdict(
        _case(expected_exclusions=["lead_time"], detected_exclusions=[])
    ) is False


@pytest.mark.parametrize("kind", PERTURBATION_KINDS)
def test_perturbations_always_contradict_truth(kind: str) -> None:
    case = _case()
    facts = case_to_facts(case, _REQUIREMENT)
    corrupted = perturb_facts(facts, kind)
    assert corrupted["variant"] == f"perturbed:{kind}"
    assert corrupted["conclusion"] != facts["conclusion"]
    # 原对象不被污染
    assert facts["conclusion"]["landed_total_base"] == "5200.00"


def test_perturbation_details() -> None:
    facts = case_to_facts(_case(), _REQUIREMENT)
    drift = perturb_facts(facts, "cost_drift")
    assert drift["conclusion"]["landed_total_base"] == "5720.00"  # 5200 × 1.1
    spurious = perturb_facts(facts, "exclusion_removed")
    assert spurious["conclusion"]["exclusions"] == ["budget"]
    assert spurious["conclusion"]["eligible"] is False
    dropped = perturb_facts(
        case_to_facts(
            _case(expected_exclusions=["moq"], detected_exclusions=["moq"], eligible=False),
            _REQUIREMENT,
        ),
        "exclusion_removed",
    )
    assert dropped["conclusion"]["exclusions"] == []
    assert dropped["conclusion"]["eligible"] is True
    flipped = perturb_facts(facts, "match_flipped")
    assert flipped["conclusion"]["item_match"] is False
    with pytest.raises(ValueError, match="unknown perturbation"):
        perturb_facts(facts, "teleport_supplier")


def test_parse_accepts_json_wrapped_in_noise() -> None:
    text = "评审如下：\n" + json.dumps(
        {"scores": _scores(), "verdict": "correct", "reasons": []}
    ) + "\n以上。"
    parsed = parse_judge_response(text)
    assert parsed["verdict"] == "correct"
    assert parsed["scores"]["evidence_consistency"] == 1.0


@pytest.mark.parametrize(
    "payload",
    [
        "no json here",
        json.dumps({"scores": _scores(), "verdict": "maybe"}),
        json.dumps({"scores": {**_scores(), "constraint_adherence": 1.7}, "verdict": "correct"}),
        json.dumps({"scores": _scores(conclusion_correctness="high"), "verdict": "correct"}),
        json.dumps({"verdict": "correct"}),
        json.dumps({"scores": _scores(), "verdict": "correct", "reasons": "not-a-list"}),
    ],
)
def test_parse_rejects_invalid_responses(payload: str) -> None:
    with pytest.raises(JudgeResponseError):
        parse_judge_response(payload)


def test_judge_case_adds_mean_and_variant() -> None:
    facts = case_to_facts(_case(), _REQUIREMENT)
    invoke = lambda prompt: json.dumps(  # noqa: E731
        {"scores": _scores(hallucination_free=0.8), "verdict": "incorrect", "reasons": ["x"]}
    )
    result = judge_case(facts, invoke)
    assert result["case_id"] == "q-alpha"
    assert result["variant"] == "clean"
    assert result["mean_score"] == pytest.approx(0.95)
    assert result_key(result) == "q-alpha#clean"


def test_judge_cases_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def flaky(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "garbage"
        return json.dumps({"scores": _scores(), "verdict": "correct", "reasons": []})

    results = judge_cases([case_to_facts(_case(), _REQUIREMENT)], flaky, retries=1)
    assert calls["n"] == 2
    assert results[0]["verdict"] == "correct"


def test_judge_cases_gives_up_after_retries() -> None:
    facts = case_to_facts(_case(), _REQUIREMENT)
    with pytest.raises(JudgeResponseError, match="after 2 attempts"):
        judge_cases([facts], lambda prompt: "garbage", retries=1)


def test_verdict_requires_correct_and_all_dims_above_threshold() -> None:
    results = [
        {"case_id": "a", "variant": "clean", "scores": _scores(), "verdict": "correct", "mean_score": 1.0},
        {
            "case_id": "b",
            "variant": "clean",
            "scores": _scores(constraint_adherence=0.5),
            "verdict": "correct",  # 口头 correct 但维度分不过线 → 不认可
            "mean_score": 0.875,
        },
        {
            "case_id": "b",
            "variant": "perturbed:cost_drift",
            "scores": _scores(),
            "verdict": "incorrect",
            "mean_score": 0.5,
        },
    ]
    verdicts = judge_verdicts(results)
    assert verdicts == {"a#clean": True, "b#clean": False, "b#perturbed:cost_drift": False}


def test_human_verdicts_three_way_equality() -> None:
    system = {
        "q1": {"landed_total_base": "5200.00", "item_match": True, "exclusion_codes": []},
        "q2": {"landed_total_base": "4500.00", "item_match": False, "exclusion_codes": ["MOQ"]},
    }
    observations = [
        {"case_id": "q1", "landed_total_base": "5200.00", "item_match": True, "exclusion_codes": []},
        {"case_id": "q2", "landed_total_base": "4500.00", "item_match": True, "exclusion_codes": ["MOQ"]},
        {"case_id": "unknown", "landed_total_base": "1", "item_match": True, "exclusion_codes": []},
    ]
    assert human_verdicts(observations, system) == {"q1": True, "q2": False}


def test_cohen_kappa_perfect_and_degenerate() -> None:
    left = {"a": True, "b": False, "c": True, "d": False}
    assert cohen_kappa(left, dict(left)) == 1.0
    assert cohen_kappa({}, {}) is None
    all_true = {"a": True, "b": True}
    assert cohen_kappa(all_true, dict(all_true)) is None  # 退化分布不可定义


def test_summarize_with_reference_calibration() -> None:
    results = [
        {"case_id": "a", "variant": "clean", "scores": _scores(), "verdict": "correct", "mean_score": 1.0},
        {
            "case_id": "b",
            "variant": "clean",
            "scores": _scores(evidence_consistency=0.2),
            "verdict": "incorrect",
            "mean_score": 0.8,
        },
        {
            "case_id": "c",
            "variant": "perturbed:cost_drift",
            "scores": _scores(),
            "verdict": "incorrect",
            "mean_score": 0.5,
        },
    ]
    reference = {"a#clean": True, "b#clean": True, "c#perturbed:cost_drift": False}
    summary = summarize_judge(results, reference=reference)
    calibration = summary["calibration"]
    assert calibration["reference_cases"] == 3
    assert calibration["agreement"] == "2/3"
    assert calibration["clean_agreement"] == "1/2"
    assert calibration["perturbation_detection"] == "1/1"
    assert calibration["disagreements"] == ["b#clean"]
    assert calibration["cohen_kappa"] is not None


def test_summarize_rejects_empty() -> None:
    with pytest.raises(ValueError):
        summarize_judge([])
