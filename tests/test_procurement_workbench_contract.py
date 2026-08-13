import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agentharness.procurement.contracts import (
    AI_STATUS_TRANSITIONS,
    AiTaskStatus,
    AiTaskStep,
    AiTaskType,
    AiTaskView,
    ProcurementStatus,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "contracts" / "procurement-workbench.schema.json").read_text())


def _enum(name: str) -> set[str]:
    return set(SCHEMA["$defs"][name]["enum"])


def test_python_enums_and_transitions_match_shared_contract():
    assert {item.value for item in ProcurementStatus} == _enum("ProcurementStatus")
    assert {item.value for item in AiTaskStatus} == _enum("AiTaskStatus")
    assert {item.value for item in AiTaskType} == _enum("AiTaskType")
    assert {item.value for item in AiTaskStep} == _enum("AiTaskStep")
    assert {
        status.value: sorted(target.value for target in targets)
        for status, targets in AI_STATUS_TRANSITIONS.items()
    } == {
        status: sorted(targets)
        for status, targets in SCHEMA["x-ai-status-transitions"].items()
    }


def test_failure_and_stale_examples_validate_and_roundtrip():
    validator = Draft202012Validator(
        {
            "$schema": SCHEMA["$schema"],
            "$defs": SCHEMA["$defs"],
            "$ref": "#/$defs/AiTaskView",
        }
    )
    for name in ("ai-task-failed.json", "ai-task-stale.json"):
        value = json.loads((ROOT / "contracts" / "examples" / name).read_text())
        errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
        assert errors == []
        assert AiTaskView.model_validate(value).model_dump(mode="json") == value


def test_examples_cover_failure_and_stale_semantics():
    failed = json.loads((ROOT / "contracts" / "examples" / "ai-task-failed.json").read_text())
    stale = json.loads((ROOT / "contracts" / "examples" / "ai-task-stale.json").read_text())
    assert failed["status"] == "FAILED" and failed["retryable"] is True
    assert failed["error_code"]
    assert stale["status"] == "SUCCEEDED" and stale["stale"] is True
    assert stale["stale_reason"] == "INPUT_GENERATION_CHANGED"
