"""Read-only, reproducible run reports assembled from persisted runtime facts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from agentharness.contracts import EventEnvelope, EventType, ToolInvocationRecord
from agentharness.harness import Harness

_ACTIVE_STATUSES = {"pending", "running", "waiting_approval"}
_VERSION_PATTERN = re.compile(r"file_version sha256=([0-9a-f]{64})")


def _event_type(event: EventEnvelope) -> str:
    return event.type.value if isinstance(event.type, EventType) else str(event.type)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _verification_attempts(events: list[EventEnvelope]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for event in events:
        event_type = _event_type(event)
        if event_type == "verification_started":
            validators = event.payload.get("validators")
            attempts.append(
                {
                    "attempt": _integer(event.payload.get("attempt")),
                    "step": _integer(event.payload.get("step")),
                    "validators": list(validators) if isinstance(validators, list) else [],
                    "max_retries": _integer(event.payload.get("max_retries")),
                    "started_at": event.timestamp.isoformat(),
                    "started_event_id": event.event_id,
                    "started_global_seq": event.global_seq,
                    "action": "pending",
                    "passed": False,
                    "failures": [],
                    "evidence": {},
                }
            )
            continue
        if event_type != "verification_result":
            continue
        attempt_number = _integer(event.payload.get("attempt"))
        target = next(
            (
                item
                for item in reversed(attempts)
                if item["attempt"] == attempt_number and item["action"] == "pending"
            ),
            None,
        )
        if target is None:
            target = {
                "attempt": attempt_number,
                "step": _integer(event.payload.get("step")),
                "validators": [],
                "max_retries": 0,
                "started_at": None,
                "started_event_id": None,
                "started_global_seq": None,
            }
            attempts.append(target)
        action = str(event.payload.get("action") or "stop")
        raw_failures = event.payload.get("failures")
        raw_evidence = event.payload.get("evidence")
        target.update(
            {
                "action": action,
                "passed": action == "pass" or event.payload.get("passed") is True,
                "failures": list(raw_failures) if isinstance(raw_failures, list) else [],
                "evidence": dict(raw_evidence) if isinstance(raw_evidence, dict) else {},
                "finished_at": event.timestamp.isoformat(),
                "result_event_id": event.event_id,
                "result_global_seq": event.global_seq,
            }
        )
    return attempts


def _failure_reasons(attempts: list[dict[str, Any]], run_error: Any) -> list[str]:
    reasons: list[str] = []
    for attempt in attempts:
        for failure in attempt.get("failures") or []:
            if not isinstance(failure, dict):
                continue
            message = str(failure.get("message") or "").strip()
            if message and message not in reasons:
                reasons.append(message)
    error = str(run_error or "").strip()
    if error and not any(error == reason or error.endswith(reason) for reason in reasons):
        reasons.append(error)
    return reasons


def _conclusion(
    *,
    run_status: str,
    configured: bool,
    attempts: list[dict[str, Any]],
    failure_reasons: list[str],
) -> dict[str, Any]:
    latest = attempts[-1] if attempts else None
    if configured and run_status == "completed" and latest and latest.get("passed"):
        return {
            "status": "passed",
            "label": "已完成",
            "verified": True,
            "reason": "所有验收规则均已通过。",
        }
    if run_status == "require_human":
        return {
            "status": "needs_review",
            "label": "需要人工处理",
            "verified": False,
            "reason": failure_reasons[-1] if failure_reasons else "验收需要人工确认。",
        }
    if run_status == "failed":
        return {
            "status": "failed",
            "label": "失败",
            "verified": False,
            "reason": failure_reasons[-1] if failure_reasons else "运行或验收失败。",
        }
    if run_status in _ACTIVE_STATUSES:
        return {
            "status": "pending",
            "label": "验收中" if configured else "运行中",
            "verified": False,
            "reason": "任务尚未形成最终结论。",
        }
    if run_status == "cancelled":
        return {
            "status": "cancelled",
            "label": "已停止",
            "verified": False,
            "reason": failure_reasons[-1] if failure_reasons else "任务已停止。",
        }
    if run_status == "interrupted":
        return {
            "status": "interrupted",
            "label": "已中断",
            "verified": False,
            "reason": failure_reasons[-1] if failure_reasons else "任务已中断，可恢复后继续。",
        }
    return {
        "status": "unverified",
        "label": "运行结束",
        "verified": False,
        "reason": (
            "该历史运行没有配置验收规则，因此不标记为已完成。"
            if not configured
            else "运行已结束，但缺少可确认的验收通过证据。"
        ),
    }


def _artifact_ids(value: Any, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "artifact_id" and isinstance(item, str) and item and item not in found:
                found.append(item)
            else:
                _artifact_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            _artifact_ids(item, found)


def _tool_payload(runtime: Harness, invocation: ToolInvocationRecord) -> dict[str, Any]:
    payload = invocation.model_dump(mode="json")
    payload["attempts_audit"] = runtime.list_tool_attempts(invocation.id)
    return payload


def _workspace_changes(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("effect") != "workspace_write":
            continue
        arguments = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
        content = str(result.get("content") or "")
        version = _VERSION_PATTERN.search(content)
        target = arguments.get("path") or arguments.get("target") or arguments.get("file")
        changes.append(
            {
                "invocation_id": tool.get("id"),
                "tool": tool.get("tool_name"),
                "path": str(target) if target is not None else None,
                "status": tool.get("status"),
                "changed": tool.get("status") == "succeeded",
                "expected_version": arguments.get("expected_version"),
                "resulting_version": version.group(1) if version else None,
                "arguments_sha256": tool.get("arguments_sha256"),
                "artifact_id": result.get("artifact_id"),
                "finished_at": tool.get("finished_at"),
            }
        )
    return changes


def build_run_report(runtime: Harness, run_id: str) -> dict[str, Any] | None:
    """Project a durable report without introducing a second state owner."""
    run = runtime.get_run(run_id)
    if run is None:
        return None

    metadata = _json_object(run.get("metadata_json"))
    policy = metadata.get("_agentharness_verification_policy")
    if not isinstance(policy, dict):
        policy = None
    events = runtime.get_events(run_id=run_id, limit=10_000)
    attempts = _verification_attempts(events)
    configured = bool(policy) or bool(attempts)
    failures = _failure_reasons(attempts, run.get("error"))
    tools = [_tool_payload(runtime, item) for item in runtime.list_tool_invocations(run_id)]
    approvals = runtime.list_approvals(run_id)
    event_payloads = [event.model_dump(mode="json") for event in events]

    artifact_ids: list[str] = []
    _artifact_ids(event_payloads, artifact_ids)
    _artifact_ids(tools, artifact_ids)
    artifacts: list[dict[str, Any]] = []
    for artifact_id in artifact_ids:
        artifact = runtime.get_artifact(artifact_id)
        if artifact is None:
            continue
        artifacts.append(
            {
                "id": artifact["id"],
                "sha256": artifact["sha256"],
                "content_type": artifact.get("content_type"),
                "size_bytes": artifact.get("size_bytes"),
                "summary": artifact.get("summary"),
                "created_at": artifact.get("created_at"),
            }
        )

    status = str(run.get("status") or "")
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "session_id": run.get("session_id"),
        "as_of": run.get("updated_at"),
        "run": run,
        "conclusion": _conclusion(
            run_status=status,
            configured=configured,
            attempts=attempts,
            failure_reasons=failures,
        ),
        "verification": {
            "configured": configured,
            "policy": policy,
            "attempts": attempts,
            "failure_reasons": failures,
        },
        "workspace_changes": _workspace_changes(tools),
        "tools": tools,
        "approvals": approvals,
        "artifacts": artifacts,
        "usage": _json_object(run.get("usage_json")),
        "events": event_payloads,
        "source": {
            "run_updated_at": run.get("updated_at"),
            "max_global_seq": max((event.global_seq for event in events), default=0),
            "event_count": len(events),
            "tool_count": len(tools),
            "approval_count": len(approvals),
            "artifact_count": len(artifacts),
        },
    }
    safe_payload = runtime.redactor.redact_public_obj(payload)
    canonical = json.dumps(
        safe_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    safe_payload["evidence_sha256"] = hashlib.sha256(canonical).hexdigest()
    return safe_payload


__all__ = ["build_run_report"]
