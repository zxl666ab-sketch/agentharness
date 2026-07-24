"""Human-label dataset IO and judge calibration metrics."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from agentharness.eval.contracts import (
    CONTRACT_SCHEMA_VERSION,
    CalibrationExample,
    CalibrationReport,
)


class CalibrationDataset:
    """Import/export the explicit human-label contract as JSON or JSONL."""

    @staticmethod
    def export(
        examples: list[CalibrationExample], path: str | Path
    ) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.casefold() == ".jsonl":
            text = "\n".join(
                example.model_dump_json() for example in examples
            ) + ("\n" if examples else "")
        else:
            text = json.dumps(
                {
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                    "examples": [
                        example.model_dump(mode="json") for example in examples
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n"
        target.write_text(text, encoding="utf-8")
        return target

    @staticmethod
    def load(path: str | Path) -> list[CalibrationExample]:
        target = Path(path)
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read calibration dataset {target}: {exc}") from exc
        try:
            if target.suffix.casefold() == ".jsonl":
                values = [json.loads(line) for line in text.splitlines() if line.strip()]
            else:
                payload = json.loads(text)
                if not isinstance(payload, dict) or payload.get("schema_version") != 2:
                    raise ValueError("calibration JSON requires schema_version=2")
                values = payload.get("examples")
            return TypeAdapter(list[CalibrationExample]).validate_python(values)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"invalid calibration dataset {target}: {exc}") from exc


class JudgeCalibrator:
    """Compare mean judge samples to human labels with classification and rank metrics."""

    def calibrate(self, examples: list[CalibrationExample]) -> CalibrationReport:
        usable = [item for item in examples if item.judge_scores]
        if not usable:
            return CalibrationReport(
                sample_count=0,
                synthetic_only=bool(examples) and all(item.synthetic for item in examples),
                trust_status="unverified",
            )
        human_scores = [item.human_score for item in usable]
        judge_scores = [statistics.fmean(item.judge_scores) for item in usable]
        truth = [item.human_passed for item in usable]
        predicted = [score >= 0.5 for score in judge_scores]
        tp = sum(expected and actual for expected, actual in zip(truth, predicted, strict=True))
        tn = sum(
            (not expected) and (not actual)
            for expected, actual in zip(truth, predicted, strict=True)
        )
        fp = sum(
            (not expected) and actual
            for expected, actual in zip(truth, predicted, strict=True)
        )
        fn = sum(
            expected and (not actual)
            for expected, actual in zip(truth, predicted, strict=True)
        )
        count = len(usable)
        accuracy = (tp + tn) / count
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        expected_agreement = (
            ((tp + fn) / count) * ((tp + fp) / count)
            + ((tn + fp) / count) * ((tn + fn) / count)
        )
        kappa = (
            (accuracy - expected_agreement) / (1.0 - expected_agreement)
            if expected_agreement < 1.0
            else 1.0
        )
        mae = statistics.fmean(
            abs(human - judge)
            for human, judge in zip(human_scores, judge_scores, strict=True)
        )
        consistency = statistics.fmean(
            max(
                sum(score >= 0.5 for score in item.judge_scores),
                sum(score < 0.5 for score in item.judge_scores),
            )
            / len(item.judge_scores)
            for item in usable
        )
        by_task: dict[str, list[float]] = defaultdict(list)
        for item, judge in zip(usable, judge_scores, strict=True):
            by_task[item.task_type].append(judge - item.human_score)
        bias = {
            task: round(statistics.fmean(deltas), 6)
            for task, deltas in sorted(by_task.items())
        }
        synthetic_only = all(item.synthetic for item in usable)
        trusted = (
            not synthetic_only
            and count >= 20
            and accuracy >= 0.8
            and kappa >= 0.6
            and mae <= 0.15
            and consistency >= 0.8
        )
        return CalibrationReport(
            sample_count=count,
            synthetic_only=synthetic_only,
            trust_status="trusted" if trusted else "unverified",
            accuracy=round(accuracy, 6),
            precision=round(precision, 6),
            recall=round(recall, 6),
            f1=round(f1, 6),
            cohens_kappa=round(kappa, 6),
            spearman=self._spearman(human_scores, judge_scores),
            mean_absolute_error=round(mae, 6),
            internal_consistency=round(consistency, 6),
            task_type_bias=bias,
        )

    @staticmethod
    def _spearman(left: list[float], right: list[float]) -> float | None:
        if len(left) < 2 or len(right) != len(left):
            return None
        left_ranks = JudgeCalibrator._ranks(left)
        right_ranks = JudgeCalibrator._ranks(right)
        left_mean = statistics.fmean(left_ranks)
        right_mean = statistics.fmean(right_ranks)
        numerator = sum(
            (a - left_mean) * (b - right_mean)
            for a, b in zip(left_ranks, right_ranks, strict=True)
        )
        left_norm = math.sqrt(sum((value - left_mean) ** 2 for value in left_ranks))
        right_norm = math.sqrt(sum((value - right_mean) ** 2 for value in right_ranks))
        if left_norm == 0 or right_norm == 0:
            return None
        return round(numerator / (left_norm * right_norm), 6)

    @staticmethod
    def _ranks(values: list[float]) -> list[float]:
        ordered = sorted(enumerate(values), key=lambda item: item[1])
        ranks = [0.0] * len(values)
        cursor = 0
        while cursor < len(ordered):
            end = cursor + 1
            while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
                end += 1
            rank = (cursor + 1 + end) / 2.0
            for index, _value in ordered[cursor:end]:
                ranks[index] = rank
            cursor = end
        return ranks
