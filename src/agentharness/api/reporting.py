"""Read-only, reproducible run reports assembled from persisted runtime facts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from agentharness.contracts import EventEnvelope, EventType, ToolInvocationRecord
from agentharness.harness import Harness

_ACTIVE_STATUSES = {"pending", "running", "waiting_approval"}

# The report carries the most recent event window for the timeline; verification
# attempts, artifact discovery and the evidence hash still derive from the full log.
_RECENT_EVENTS_LIMIT = 200


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


def _iter_all_events(runtime: Any, run_id: str, page_size: int = 2_000) -> list[Any]:
    """Page through the full persisted event log for a run.

    The report claims to derive evidence from the complete log, so a hard
    10_000-event prefix could silently change verification/evidence hashes on
    long runs. Pagination keeps the claim true at any event count.
    """
    events: list[Any] = []
    after = 0
    while True:
        page = runtime.get_events(run_id=run_id, after_global_seq=after, limit=page_size)
        if not page:
            break
        events.extend(page)
        after = int(page[-1].global_seq)
        if len(page) < page_size:
            break
    return events


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
    if run_status == "budget_stopped":
        return {
            "status": "budget_stopped",
            "label": "已停在安全边界",
            "verified": False,
            "reason": failure_reasons[-1] if failure_reasons else "预算用尽，已停在安全边界；可调整预算后恢复继续。",
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


def _convergence(
    *,
    run: dict[str, Any],
    events: list[EventEnvelope],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convergence and governance metrics for the run report (phase 3)."""
    usage = _json_object(run.get("usage_json"))
    model_turns = _integer(usage.get("model_turns")) or sum(
        1 for event in events if _event_type(event) == "model_turn_end"
    )
    tool_call_counts: dict[str, int] = {}
    tool_reasons: list[dict[str, Any]] = []
    for invocation in tools:
        name = str(invocation.get("tool_name") or "")
        tool_call_counts[name] = tool_call_counts.get(name, 0) + 1
        tool_reasons.append(
            {
                "tool_name": name,
                "step": _integer(invocation.get("step")),
                "status": invocation.get("status"),
                "reason": invocation.get("reason"),
            }
        )
    duplicate_calls = sum(
        1 for event in events if _event_type(event) == "tool_call_duplicate"
    )
    unauthorized_calls = sum(
        1 for event in events if _event_type(event) == "tool_stage_denied"
    )
    return {
        "model_turns": model_turns,
        "tool_call_counts": tool_call_counts,
        "total_tool_calls": sum(tool_call_counts.values()),
        "duplicate_calls": duplicate_calls,
        "unauthorized_calls": unauthorized_calls,
        "tool_reasons": tool_reasons,
    }

def _tool_payload(runtime: Harness, invocation: ToolInvocationRecord) -> dict[str, Any]:
    payload = invocation.model_dump(mode="json")
    payload["attempts_audit"] = runtime.list_tool_attempts(invocation.id)
    return payload


def _event_summary(event: EventEnvelope) -> dict[str, Any]:
    """Compact, human-oriented summary for a timeline event row."""
    payload = event.payload or {}
    event_type = _event_type(event)
    if event_type == "run_status":
        return {
            "status": payload.get("status"),
            "reason": str(payload.get("reason") or "")[:200] or None,
        }
    if event_type in {
        "tool_call_start",
        "tool_call_end",
        "tool_result",
        "tool_stage_denied",
        "tool_call_duplicate",
        "approval_requested",
        "approval_resolved",
    }:
        return {
            "tool_name": payload.get("tool_name"),
            "status": payload.get("status"),
        }
    if event_type == "budget_warning":
        return {
            "tokens": payload.get("tokens"),
            "cost_usd": payload.get("cost"),
        }
    if event_type in {"verification_started", "verification_result"}:
        return {
            "attempt": payload.get("attempt"),
            "step": payload.get("step"),
            "action": payload.get("action"),
        }
    if event_type in {"model_turn_start", "model_turn_end"}:
        return {"step": payload.get("step")}
    return {}


def build_run_timeline(
    runtime: Harness, run_id: str, *, limit: int = 1_000
) -> dict[str, Any] | None:
    """Read-only merged timeline of run events and tool invocations.

    Events and tool calls are merged into one ordered list so a failure can be
    traced to "which step / which tool / which attempt / what error". The list
    is bounded (newest retained) and everything derives from persisted facts.
    """
    run = runtime.get_run(run_id)
    if run is None:
        return None
    events = runtime.get_events(run_id=run_id, limit=10_000)
    tools = runtime.list_tool_invocations(run_id)
    items: list[dict[str, Any]] = []
    for event in events:
        items.append(
            {
                "kind": "event",
                "id": event.event_id,
                "seq": event.global_seq,
                "run_seq": event.run_seq,
                "at": event.timestamp.isoformat(),
                "type": _event_type(event),
                "summary": _event_summary(event),
            }
        )
    for invocation in tools:
        result = invocation.result
        at = (
            (invocation.finished_at or invocation.created_at).isoformat()
            if (invocation.finished_at or invocation.created_at)
            else None
        )
        items.append(
            {
                "kind": "tool",
                "id": invocation.id,
                "seq": invocation.ordinal,
                "run_seq": invocation.step,
                "at": at,
                "tool_name": invocation.tool_name,
                "tool_version": invocation.tool_version,
                "status": invocation.status.value,
                "step": invocation.step,
                "ordinal": invocation.ordinal,
                "duration_ms": result.duration_ms if result else None,
                "attempt_count": invocation.attempt_count,
                "error_code": invocation.error_code,
                "error_category": invocation.error_category,
                "reason": invocation.reason,
                "attempts_audit": runtime.list_tool_attempts(invocation.id),
            }
        )
    items.sort(key=lambda item: (item.get("at") or "", item.get("kind") or ""))
    total = len(items)
    retained = items[-limit:]
    return {
        "run_id": run_id,
        "items": retained,
        "total": total,
        "truncated": total > len(retained),
        "event_count": len(events),
        "tool_count": len(tools),
        "max_global_seq": max((event.global_seq for event in events), default=0),
    }


def build_usage_summary(runtime: Harness, *, limit: int = 10_000) -> dict[str, Any]:
    """Aggregate token / cost / duration / cache metrics across runs."""
    rows = runtime.storage.runs.list_runs_for_metrics(limit=limit)
    by_status: dict[str, int] = {}
    by_model: dict[str, int] = {}
    input_tokens = output_tokens = cached_input_tokens = total_tokens = 0
    model_turns = 0
    estimated_cost_usd = 0.0
    cost_unknown_runs = 0
    cache_rates: list[float] = []
    durations_ms: list[int] = []
    for row in rows:
        status = str(row.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        model = str(row.get("model") or "unknown")
        by_model[model] = by_model.get(model, 0) + 1
        usage = _json_object(row.get("usage_json"))
        input_tokens += _integer(usage.get("input_tokens"))
        output_tokens += _integer(usage.get("output_tokens"))
        cached_input_tokens += _integer(usage.get("cached_input_tokens"))
        total_tokens += _integer(usage.get("total_tokens"))
        model_turns += _integer(usage.get("model_turns"))
        cost = usage.get("estimated_cost_usd")
        if isinstance(cost, (int, float)) and cost > 0:
            estimated_cost_usd += float(cost)
        else:
            cost_unknown_runs += 1
        rate = usage.get("cache_hit_rate")
        if isinstance(rate, (int, float)):
            cache_rates.append(float(rate))
        created = row.get("created_at")
        finished = row.get("finished_at") or row.get("updated_at")
        if created and finished:
            try:
                start = datetime.fromisoformat(str(created))
                end = datetime.fromisoformat(str(finished))
                durations_ms.append(int((end - start).total_seconds() * 1000))
            except (TypeError, ValueError):
                pass
    budget_warnings = runtime.storage.events.count_events_by_type("budget_warning")
    return {
        "runs": len(rows),
        "by_status": by_status,
        "by_model": by_model,
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "cached_input": cached_input_tokens,
            "total": total_tokens,
        },
        "model_turns": model_turns,
        "estimated_cost_usd": round(estimated_cost_usd, 6),
        "cost_unknown_runs": cost_unknown_runs,
        "cache_hit_rate": (
            round(sum(cache_rates) / len(cache_rates), 4) if cache_rates else None
        ),
        "avg_duration_ms": (
            round(sum(durations_ms) / len(durations_ms)) if durations_ms else None
        ),
        "duration_runs": len(durations_ms),
        "budget_warnings": budget_warnings,
    }


def build_run_report(runtime: Harness, run_id: str) -> dict[str, Any] | None:
    """Project a durable report without introducing a second state owner."""
    run = runtime.get_run(run_id)
    if run is None:
        return None

    metadata = _json_object(run.get("metadata_json"))
    policy = metadata.get("_agentharness_verification_policy")
    if not isinstance(policy, dict):
        policy = None
    all_events = _iter_all_events(runtime, run_id)
    attempts = _verification_attempts(all_events)
    configured = bool(policy) or bool(attempts)
    failures = _failure_reasons(attempts, run.get("error"))
    tools = [_tool_payload(runtime, item) for item in runtime.list_tool_invocations(run_id)]
    approvals = runtime.list_approvals(run_id)
    event_payloads = [event.model_dump(mode="json") for event in all_events]
    events_total = runtime.count_events(run_id)
    # The timeline only carries a bounded window; evidence derivation above still
    # uses the full event list, so truncation never weakens verification results,
    # artifact discovery or the canonical report hash.
    recent_events = event_payloads[-_RECENT_EVENTS_LIMIT:]
    events_truncated = events_total > len(recent_events)

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
        "tools": tools,
        "approvals": approvals,
        "artifacts": artifacts,
        "versions": {
            "prompt_version": metadata.get("procurement_prompt_version"),
            "prompt_sha256": metadata.get("procurement_prompt_sha256"),
            "tool_schema_version": metadata.get("procurement_tool_schema_version"),
            "tool_schema_sha256": metadata.get("procurement_tool_schema_sha256"),
            "parser_version": metadata.get("procurement_parser_version"),
            "ruleset_version": metadata.get("procurement_ruleset_version"),
            "provider": run.get("provider"),
            "model": run.get("model"),
        },
        "usage": _json_object(run.get("usage_json")),
        "convergence": _convergence(
            run=run,
            events=all_events,
            tools=tools,
        ),
        "events": recent_events,
        "events_total": events_total,
        "events_truncated": events_truncated,
        "source": {
            "run_updated_at": run.get("updated_at"),
            "max_global_seq": max((event.global_seq for event in all_events), default=0),
            "event_count": events_total,
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
