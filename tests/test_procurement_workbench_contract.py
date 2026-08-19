import json
from pathlib import Path

import yaml
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


def test_requirement_view_preserves_unknown_drafts_but_rejects_confirmed_unknowns():
    validator = Draft202012Validator(
        {
            "$schema": SCHEMA["$schema"],
            "$defs": SCHEMA["$defs"],
            "$ref": "#/$defs/ProcurementRequirementView",
        }
    )
    draft = {"quantity": None, "unit": None, "requirement_confirmed": False}
    confirmed = {"quantity": "5000", "unit": "个", "requirement_confirmed": True}
    invalid = {"quantity": None, "unit": None, "requirement_confirmed": True}

    assert list(validator.iter_errors(draft)) == []
    assert list(validator.iter_errors(confirmed)) == []
    assert list(validator.iter_errors(invalid))


def _openapi(name: str) -> dict:
    return yaml.safe_load((ROOT / "contracts" / name).read_text(encoding="utf-8"))


def test_public_openapi_exposes_durable_human_interactions_and_idempotent_fulfillment():
    document = _openapi("procurement-workbench-openapi.yaml")
    paths = document["paths"]
    expected = {
        "/api/procurement/requests/{businessId}/interactions",
        "/api/procurement/interactions/{interactionId}",
        "/api/procurement/interactions/{interactionId}/answer",
        "/api/procurement/interactions/{interactionId}/artifacts",
        "/api/procurement/interactions/{interactionId}/retry",
        "/api/procurement/interactions/{interactionId}/cancel",
        "/api/procurement/operations/{operationId}",
    }
    assert expected <= paths.keys()
    answer_schema = paths["/api/procurement/interactions/{interactionId}/answer"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]["$ref"]
    assert answer_schema.endswith("#/$defs/HumanInteractionAnswerRequest")
    for path in (
        "/api/procurement/orders/{orderId}/transition",
        "/api/procurement/settlements/{settlementId}/transition",
    ):
        parameters = paths[path]["post"]["parameters"]
        assert {item.get("$ref") for item in parameters} >= {
            "#/components/parameters/IdempotencyKey"
        }


def test_internal_openapis_bind_human_answer_and_authorized_context():
    agent = _openapi("agent-internal-openapi.yaml")
    command_enum = agent["components"]["schemas"]["AgentCommand"]["properties"][
        "operation_type"
    ]["enum"]
    assert {
        "human_interaction_answer",
        "parse_invoice",
        "explain_invoice_diff",
        "draft_contract",
    } <= set(command_enum)

    java = _openapi("procurement-internal-openapi.yaml")
    task_context = java["components"]["schemas"]["TaskAgentContext"]
    assert {
        "quantity",
        "unit",
        "requirement_confirmed",
        "authorized_artifacts",
        "interactions",
    } <= set(task_context["required"])
    assert "null" in task_context["properties"]["quantity"]["type"]
    assert "null" in task_context["properties"]["unit"]["type"]
    interaction = java["components"]["schemas"]["HumanInteractionContext"]
    assert "checkpoint_id" in interaction["required"]
    response = java["paths"]["/internal/v1/tasks/{taskId}/context"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert response == "#/components/schemas/TaskAgentContext"
