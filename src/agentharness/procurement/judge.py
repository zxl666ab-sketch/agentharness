"""LLM-as-a-Judge offline evaluation for frozen procurement comparison conclusions.

字段级精确匹配（617/620）只能证明"表格填对了"；本模块用独立 LLM 评审按四维
rubric 判断每条比价结论（资格判定 + 排除原因 + 到货总价 + 物料匹配）是否被
需求与事实支撑，并用"干净结论 + 注入错误对照组"校准 Judge-真值一致率与
Cohen's kappa。

设计要点：
- Judge 只看需求、抽取事实与系统结论，看不到期望答案——检测靠领域推理而非
  机械比对，否则评测退化为精确匹配的复读。
- 被评审对象是确定性解析器 + Java 比价引擎（全程无 LLM 参与），任何 LLM 评审
  天然满足"评估模型与被评模型异源"的独立评审纪律。
- 注入错误对照（成本漂移/漏检排除/匹配翻转）提供"incorrect"侧样本，避免全对
  数据下 kappa 退化、一致率虚高。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

JUDGE_DIMENSIONS = (
    "conclusion_correctness",
    "evidence_consistency",
    "constraint_adherence",
    "hallucination_free",
)

PERTURBATION_KINDS = ("cost_drift", "exclusion_removed", "match_flipped")

JUDGE_SYSTEM_PROMPT = (
    "你是采购比价结论的独立质检评审。给定采购需求、从报价单抽取的事实字段，"
    "以及系统得出的比价结论，判断该结论是否被事实与需求支撑：总价与单价/数量/"
    "含税/运费是否自洽，排除判定是否违反交期、起订量、发票、预算等硬约束，"
    "是否存在事实中不存在的编造值。不引入外部知识，不揣测供应商意图。"
    "输出必须是单个 JSON 对象，不得包含任何 JSON 之外的文字。"
)

_PASS_THRESHOLD = 0.9


class JudgeResponseError(ValueError):
    """评审响应不可解析或不满足 schema 时抛出。"""


def case_to_facts(case: Mapping[str, Any], requirement: Mapping[str, Any]) -> dict[str, Any]:
    """把冻结评测的 raw case 转成 Judge 可见事实（不含期望答案）。

    requirement 必须携带 analysis_as_of（评估基准日）：否则 Judge 无法核验
    "expired" 类排除的证据，会把正确结论误判为幻觉。
    """
    return {
        "case_id": str(case["case_id"]),
        "variant": "clean",
        "requirement": {
            "item_name": requirement.get("item_name"),
            "quantity": requirement.get("quantity"),
            "unit": requirement.get("unit"),
            "specifications": requirement.get("specifications", {}),
            "constraints": requirement.get("constraints", {}),
            "analysis_as_of": requirement.get("analysis_as_of"),
        },
        "extracted_fields": {
            str(field["name"]): field.get("final_value") for field in case.get("fields", [])
        },
        "conclusion": {
            "item_match": case.get("actual_match"),
            "landed_total_base": case.get("actual_landed_total_base"),
            "exclusions": list(case.get("detected_exclusions") or []),
            "eligible": case.get("eligible"),
        },
    }


def ground_truth_verdict(case: Mapping[str, Any]) -> bool:
    """真值裁定：系统结论与人工冻结期望逐项一致才算认可。"""
    return (
        bool(case.get("expected_match")) == bool(case.get("actual_match"))
        and str(case.get("expected_landed_total_base")) == str(case.get("actual_landed_total_base"))
        and sorted(case.get("expected_exclusions") or [])
        == sorted(case.get("detected_exclusions") or [])
    )


def perturb_facts(facts: Mapping[str, Any], kind: str) -> dict[str, Any]:
    """构造性错误：返回被污染的结论副本（真值必为不认可）。"""
    corrupted = json.loads(json.dumps(dict(facts), default=str))
    conclusion = corrupted["conclusion"]
    if kind == "cost_drift":
        try:
            landed = Decimal(str(conclusion.get("landed_total_base") or "0"))
        except InvalidOperation as exc:
            raise ValueError(f"cannot perturb non-numeric landed total: {exc}") from exc
        conclusion["landed_total_base"] = str((landed * Decimal("1.1")).quantize(Decimal("0.01")))
    elif kind == "exclusion_removed":
        exclusions = list(conclusion.get("exclusions") or [])
        if exclusions:
            # 全量漏检 → 资格判定必然翻转（留一条正确排除会留下
            # "最终判定未变" 的歧义样本，Judge 判 correct 也说得通）
            conclusion["exclusions"] = []
            conclusion["eligible"] = True
        else:
            conclusion["exclusions"] = ["budget"]
            conclusion["eligible"] = False
    elif kind == "match_flipped":
        conclusion["item_match"] = not bool(conclusion.get("item_match"))
    else:
        raise ValueError(f"unknown perturbation kind: {kind}")
    corrupted["variant"] = f"perturbed:{kind}"
    return corrupted


def build_judge_prompt(case_facts: Mapping[str, Any]) -> str:
    """把单案事实与评分口径组装成评审 prompt（确定性、可复算）。"""
    rubric = {
        "conclusion_correctness": "资格判定与推荐结论是否与需求及事实一致（排除项、预算、硬约束）",
        "evidence_consistency": "结论数值（到货总价、物料匹配）是否与抽取事实及需求参数自洽",
        "constraint_adherence": "硬约束（交期/发票/起订量/预算上限/规格公差）是否被逐条遵守",
        "hallucination_free": "是否存在抽取事实与需求中不存在的字段值、供应商或数字",
    }
    payload = {
        "case_id": case_facts["case_id"],
        "variant": case_facts.get("variant", "clean"),
        "requirement": case_facts["requirement"],
        "extracted_fields": case_facts["extracted_fields"],
        "conclusion": case_facts["conclusion"],
        "rubric_dimensions": rubric,
        "output_schema": {
            "scores": {dim: "0.0-1.0 之间的数值" for dim in JUDGE_DIMENSIONS},
            "verdict": '"correct" 或 "incorrect"（任一维度低于 0.9 或存在漏检/误排/幻觉即 incorrect）',
            "reasons": "字符串数组，逐条列出扣分原因（无问题则为空数组）",
        },
    }
    return (
        JUDGE_SYSTEM_PROMPT
        + "\n\n案件事实：\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    )


def parse_judge_response(text: str) -> dict[str, Any]:
    """宽容提取首个 JSON 对象，但严格校验 schema 与取值域。"""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise JudgeResponseError("judge response contains no JSON object")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise JudgeResponseError(f"judge response JSON invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise JudgeResponseError("judge response must be a JSON object")
    scores = data.get("scores")
    if not isinstance(scores, dict):
        raise JudgeResponseError("judge response missing scores object")
    normalized: dict[str, float] = {}
    for dim in JUDGE_DIMENSIONS:
        raw = scores.get(dim)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise JudgeResponseError(f"score for {dim} is not numeric") from exc
        if not 0.0 <= value <= 1.0:
            raise JudgeResponseError(f"score for {dim} out of range [0,1]")
        normalized[dim] = value
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in {"correct", "incorrect"}:
        raise JudgeResponseError("judge verdict must be correct or incorrect")
    reasons = data.get("reasons")
    if reasons is None:
        reasons = []
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise JudgeResponseError("judge reasons must be a list of strings")
    return {"scores": normalized, "verdict": verdict, "reasons": [str(r) for r in reasons]}


def judge_case(case_facts: Mapping[str, Any], invoke: Callable[[str], str]) -> dict[str, Any]:
    """单案评审：组 prompt → 调模型 → 解析校验。invoke 抛错原样上抛。"""
    parsed = parse_judge_response(invoke(build_judge_prompt(case_facts)))
    return {
        "case_id": case_facts["case_id"],
        "variant": str(case_facts.get("variant", "clean")),
        **parsed,
        "mean_score": round(sum(parsed["scores"].values()) / len(JUDGE_DIMENSIONS), 4),
    }


def judge_cases(
    cases_facts: Sequence[Mapping[str, Any]],
    invoke: Callable[[str], str],
    *,
    retries: int = 1,
) -> list[dict[str, Any]]:
    """批量评审；响应非法时按 retries 重试。"""
    results: list[dict[str, Any]] = []
    for facts in cases_facts:
        attempts = retries + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                results.append(judge_case(facts, invoke))
                break
            except JudgeResponseError as exc:
                last_error = exc
        else:
            raise JudgeResponseError(
                f"judge failed for case {facts['case_id']} after {attempts} attempts: {last_error}"
            )
    return results


async def judge_cases_async(
    cases_facts: Sequence[Mapping[str, Any]],
    ainvoke: Callable[[str], Awaitable[str]],
    *,
    retries: int = 1,
    concurrency: int = 4,
    fail_fast: bool = False,
) -> list[dict[str, Any]]:
    """并发批量评审（信号量限流）；结果顺序与输入一致。

    默认单案失败不拖垮整批：重试耗尽后记为 ``judge_error`` 结果（不计入指标，
    计入覆盖率），由 summarize_judge 如实上报。fail_fast=True 恢复旧行为。
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def one(facts: Mapping[str, Any]) -> dict[str, Any]:
        async with semaphore:
            attempts = retries + 1
            last_error: Exception | None = None
            for _ in range(attempts):
                try:
                    parsed = parse_judge_response(await ainvoke(build_judge_prompt(facts)))
                    return {
                        "case_id": facts["case_id"],
                        "variant": str(facts.get("variant", "clean")),
                        **parsed,
                        "mean_score": round(
                            sum(parsed["scores"].values()) / len(JUDGE_DIMENSIONS), 4
                        ),
                    }
                except JudgeResponseError as exc:
                    last_error = exc
            if fail_fast:
                raise JudgeResponseError(
                    f"judge failed for case {facts['case_id']} after {attempts} attempts: {last_error}"
                )
            return {
                "case_id": facts["case_id"],
                "variant": str(facts.get("variant", "clean")),
                "judge_error": str(last_error),
            }

    return list(await asyncio.gather(*(one(facts) for facts in cases_facts)))


def human_verdicts(
    observations: Iterable[Mapping[str, Any]],
    system_by_case: Mapping[str, Mapping[str, Any]],
) -> dict[str, bool]:
    """把人工盲测观察折算成与 Judge 同域的二值裁定（同数据集版本时方可用于校准）。

    人工 observation 记录其独立核对的 landed_total_base / item_match /
    exclusion_codes；三者与系统结论逐项一致 → 人工认可该结论（True）。
    """
    verdicts: dict[str, bool] = {}
    for obs in observations:
        case_id = str(obs.get("case_id", ""))
        actual = system_by_case.get(case_id)
        if actual is None:
            continue
        landed_ok = str(obs.get("landed_total_base")) == str(actual.get("landed_total_base"))
        match_ok = bool(obs.get("item_match")) == bool(actual.get("item_match"))
        exclusions_ok = sorted(obs.get("exclusion_codes") or []) == sorted(
            actual.get("exclusion_codes") or []
        )
        verdicts[case_id] = landed_ok and match_ok and exclusions_ok
    return verdicts


def result_key(result: Mapping[str, Any]) -> str:
    """评审结果唯一键：case + variant（对照组的同案不同结论不互相覆盖）。"""
    return f"{result['case_id']}#{result.get('variant', 'clean')}"


def judge_verdicts(results: Sequence[Mapping[str, Any]]) -> dict[str, bool]:
    """Judge 二值裁定：verdict=correct 且四维均达通过线才算认可。"""
    verdicts: dict[str, bool] = {}
    for item in results:
        scores = item.get("scores", {})
        passed = str(item.get("verdict", "")).lower() == "correct" and all(
            float(scores.get(dim, 0.0)) >= _PASS_THRESHOLD for dim in JUDGE_DIMENSIONS
        )
        verdicts[result_key(item)] = passed
    return verdicts


def cohen_kappa(left: Mapping[str, bool], right: Mapping[str, bool]) -> float | None:
    """二值 Cohen's kappa；样本为空或退化（全同类）返回 None。"""
    common = sorted(set(left) & set(right))
    if not common:
        return None
    n = len(common)
    agree = sum(1 for key in common if bool(left[key]) == bool(right[key]))
    po = agree / n
    left_pos = sum(1 for key in common if left[key]) / n
    right_pos = sum(1 for key in common if right[key]) / n
    pe = left_pos * right_pos + (1 - left_pos) * (1 - right_pos)
    if pe == 1.0:
        return None
    return round((po - pe) / (1 - pe), 4)


def summarize_judge(
    results: Sequence[Mapping[str, Any]],
    *,
    reference: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """聚合：四维均分、通过率、（可选）与参考裁定（真值/人工）的一致率与 kappa。

    judge_error 结果不计入指标，单独如实上报（绝不静默丢样本）。
    """
    scored = [r for r in results if "judge_error" not in r]
    errors = [r for r in results if "judge_error" in r]
    if not scored:
        raise ValueError("no judge results to summarize")
    per_dim = {
        dim: round(sum(float(r["scores"][dim]) for r in scored) / len(scored), 4)
        for dim in JUDGE_DIMENSIONS
    }
    passed = judge_verdicts(scored)
    summary: dict[str, Any] = {
        "cases": len(scored),
        "judge_errors": len(errors),
        "error_cases": [result_key(r) for r in errors],
        "dimension_means": per_dim,
        "pass_rate": round(sum(passed.values()) / len(passed), 4),
        "mean_score_overall": round(
            sum(float(r["mean_score"]) for r in scored) / len(scored), 4
        ),
    }
    if reference:
        common = sorted(set(passed) & set(reference))
        agreement = sum(1 for key in common if passed[key] == reference[key])
        clean = [key for key in common if key.endswith("#clean")]
        perturbed = [key for key in common if not key.endswith("#clean")]
        clean_agree = sum(1 for key in clean if passed[key] == reference[key])
        perturb_detect = sum(
            1 for key in perturbed if not passed[key] and not reference[key]
        )
        summary["calibration"] = {
            "reference_cases": len(common),
            "agreement_rate": round(agreement / len(common), 4) if common else None,
            "agreement": f"{agreement}/{len(common)}",
            "clean_agreement": f"{clean_agree}/{len(clean)}",
            "perturbation_detection": f"{perturb_detect}/{len(perturbed)}",
            "cohen_kappa": cohen_kappa(passed, reference),
            "disagreements": sorted(
                key for key in common if passed[key] != reference[key]
            ),
        }
    return summary
