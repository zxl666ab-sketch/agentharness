"""Canonical trace projection over persisted runtime facts."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from agentharness.contracts import EventEnvelope, EventType, Message, MessageRole, ToolResult, Usage
from agentharness.eval.contracts import (
    AgentTrace,
    TraceArtifactRef,
    TraceSpan,
    TraceVersions,
)
from agentharness.security.redaction import Redactor, default_redactor

CURRENT_EVENT_SCHEMA_VERSION = 1


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _duration_ms(started: datetime | None, ended: datetime | None) -> float | None:
    if started is None or ended is None:
        return None
    return round(max(0.0, (ended - started).total_seconds() * 1000.0), 3)


def _event_type(event: EventEnvelope) -> str:
    return event.type.value if isinstance(event.type, EventType) else str(event.type)


def _model_dump_or_value(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    return dump(mode="json") if callable(dump) else value


def _event_sort_key(event: EventEnvelope) -> tuple[int, int, datetime, str]:
    sequence = event.run_seq or event.global_seq or 0
    return (sequence, event.global_seq or 0, event.timestamp, event.event_id)


def _span_kind(types: set[str], payloads: list[dict[str, Any]]) -> str:
    declared = next((str(p.get("kind")) for p in payloads if p.get("kind")), "")
    if declared in {"model", "tool"}:
        return declared
    if types & {"model_turn_start", "model_turn_end", "context_manifest", "text_delta"}:
        return "model"
    if types == {"tool_call_start"}:
        return "tool_call"
    if types & {"tool_result", "tool_call_end"}:
        return "tool"
    if any(item.startswith("verification_") for item in types):
        return "verification"
    if any(item.startswith("approval_") for item in types):
        return "approval"
    if any(item.startswith("child_run_") for item in types):
        return "delegate"
    if "checkpoint" in types:
        return "checkpoint"
    return "unknown"


def _control_kind(event_type: str) -> str:
    if event_type.startswith("approval_"):
        return "approval"
    if event_type.startswith("verification_"):
        return "verification"
    if event_type.startswith("child_run_"):
        return "delegate"
    if event_type == "checkpoint":
        return "checkpoint"
    return "control"


class TraceProjector:
    """Build a canonical trace without changing runtime write behavior."""

    def __init__(self, storage: Any, *, redactor: Redactor | None = None) -> None:
        self.storage = storage
        self.redactor = redactor or getattr(storage, "redactor", None) or default_redactor

    def project(self, run_id: str) -> AgentTrace:
        run = self.storage.get_run(run_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        return self.from_records(
            run=run,
            messages=self.storage.get_messages(run_id),
            events=self.storage.get_events(run_id=run_id, limit=100_000),
            checkpoint=self.storage.load_checkpoint(run_id),
            redactor=self.redactor,
        )

    def persist(self, trace: AgentTrace) -> TraceArtifactRef:
        meta = self.storage.artifacts.put_json(
            trace.model_dump(mode="json"),
            summary=f"Canonical AgentTrace for run {trace.run_id}",
        )
        meta["id"] = self.storage.register_artifact(meta)
        return TraceArtifactRef(
            artifact_id=meta["id"],
            sha256=meta["sha256"],
            content_type=meta.get("content_type") or "application/json",
            size_bytes=int(meta.get("size_bytes") or 0),
        )

    @classmethod
    def from_records(
        cls,
        *,
        run: dict[str, Any],
        messages: Iterable[Message | dict[str, Any]],
        events: Iterable[EventEnvelope | dict[str, Any]],
        checkpoint: Any | None = None,
        redactor: Redactor | None = None,
    ) -> AgentTrace:
        redact = redactor or default_redactor
        safe_run = redact.redact_obj(dict(run))
        safe_messages = [
            Message.model_validate(redact.redact_obj(
                item.model_dump(mode="json") if isinstance(item, Message) else item
            ))
            for item in messages
        ]
        safe_events = [
            EventEnvelope.model_validate(
                redact.redact_obj(
                    item.model_dump(mode="json") if isinstance(item, EventEnvelope) else item
                )
            )
            for item in events
        ]
        safe_events.sort(key=_event_sort_key)

        run_id = str(safe_run.get("id") or safe_run.get("run_id") or "")
        trace_id = _fingerprint(
            {
                "run_id": run_id,
                "root_run_id": safe_run.get("root_run_id") or run_id,
            }
        )
        partial: list[str] = []
        event_types = [_event_type(event) for event in safe_events]
        event_schemas = sorted({int(event.schema_version) for event in safe_events})
        if any(version < CURRENT_EVENT_SCHEMA_VERSION for version in event_schemas):
            partial.append("legacy_event_schema")
        if "run_started" not in event_types:
            partial.append("missing_run_started")
        status = str(safe_run.get("status") or "unknown")
        terminal_types = {
            "completed": "run_completed",
            "failed": "run_failed",
            "cancelled": "run_cancelled",
            "interrupted": "run_interrupted",
        }
        expected_terminal = terminal_types.get(status)
        if expected_terminal and expected_terminal not in event_types:
            partial.append("missing_terminal_event")
        sequences = [event.run_seq for event in safe_events if event.run_seq]
        if len(sequences) != len(set(sequences)):
            partial.append("duplicate_event_sequence")

        assistant_messages = [m for m in safe_messages if m.role == MessageRole.assistant]
        call_by_id: dict[str, Any] = {}
        result_by_id: dict[str, ToolResult] = {}
        for message in safe_messages:
            for call in message.tool_calls or []:
                call_by_id[call.id] = call
            if message.role == MessageRole.tool and message.tool_call_id:
                result_by_id[message.tool_call_id] = ToolResult(
                    tool_call_id=message.tool_call_id,
                    name=message.name or "",
                    content=message.content,
                )
        call_parent_by_id = {
            str(event.payload.get("tool_call_id")): event.parent_span_id
            for event in safe_events
            if _event_type(event) == "tool_call_start"
            and event.payload.get("tool_call_id")
            and event.parent_span_id
        }

        groups: dict[str, list[EventEnvelope]] = defaultdict(list)
        controls: list[EventEnvelope] = []
        for event in safe_events:
            if event.span_id:
                groups[event.span_id].append(event)
            elif _event_type(event) not in {
                "run_started",
                "run_status",
                "run_completed",
                "run_failed",
                "run_cancelled",
                "run_interrupted",
                "heartbeat",
            }:
                controls.append(event)

        spans: list[TraceSpan] = []
        model_index = 0
        for span_id, grouped in sorted(
            groups.items(), key=lambda pair: _event_sort_key(pair[1][0])
        ):
            grouped.sort(key=_event_sort_key)
            types = {_event_type(event) for event in grouped}
            payloads = [event.payload for event in grouped]
            kind = _span_kind(types, payloads)
            first = grouped[0]
            last = grouped[-1]
            declared_start = next(
                (event for event in grouped if _event_type(event) == "span_start"), None
            )
            declared_end = next(
                (event for event in reversed(grouped) if _event_type(event) == "span_end"), None
            )
            started_at = (declared_start or first).timestamp
            ended_at = (declared_end or last).timestamp
            call_id = next(
                (str(p.get("tool_call_id")) for p in payloads if p.get("tool_call_id")), None
            )
            call = call_by_id.get(call_id or "")
            result_event = next(
                (event for event in grouped if _event_type(event) == "tool_result"), None
            )
            tool_result = result_by_id.get(call_id or "")
            if result_event is not None:
                payload = result_event.payload
                if tool_result is None:
                    tool_result = ToolResult(
                        tool_call_id=call_id or str(payload.get("tool_call_id") or ""),
                        name=str(payload.get("name") or ""),
                        content=str(payload.get("content_preview") or ""),
                    )
                tool_result = tool_result.model_copy(
                    update={
                        "is_error": bool(payload.get("is_error")),
                        "artifact_id": payload.get("artifact_id"),
                        "duration_ms": payload.get("duration_ms"),
                        "error_code": payload.get("error_code"),
                        "error_category": payload.get("error_category"),
                        "retryable": bool(payload.get("retryable")),
                        "recovery_hint": payload.get("recovery_hint") or None,
                    }
                )
            end_payload = next(
                (event.payload for event in grouped if _event_type(event) == "model_turn_end"),
                {},
            )
            usage = Usage.model_validate(end_payload.get("usage") or {})
            context_event = next(
                (event for event in grouped if _event_type(event) == "context_manifest"), None
            )
            context_payload = context_event.payload if context_event else {}
            manifest = context_payload.get("manifest") or {}
            step_raw = next((p.get("step") for p in payloads if p.get("step") is not None), None)
            output: Any = tool_result.content if tool_result else None
            if kind == "model":
                if model_index < len(assistant_messages):
                    output = assistant_messages[model_index].content
                model_index += 1
            failed = bool(tool_result and tool_result.is_error) or bool(types & {"error"})
            interrupted = (
                declared_start is not None and declared_end is None
            ) or (
                status in {"interrupted", "cancelled"}
                and declared_end is None
                and kind in {"model", "tool"}
            )
            span_status = (
                "failed"
                if failed
                else "interrupted"
                if interrupted
                else "completed"
                if declared_end is not None or kind in {"model", "tool_call"}
                else "unset"
            )
            if interrupted:
                partial.append("open_span")
            span = TraceSpan(
                trace_id=trace_id,
                span_id=span_id,
                run_id=run_id,
                parent_span_id=(
                    next((event.parent_span_id for event in grouped if event.parent_span_id), None)
                    or call_parent_by_id.get(call_id or "")
                ),
                kind=kind,  # type: ignore[arg-type]
                name=(
                    str(getattr(call, "name", "") or (tool_result.name if tool_result else ""))
                    if kind in {"tool", "tool_call"}
                    else f"model_turn:{step_raw if step_raw is not None else model_index - 1}"
                    if kind == "model"
                    else next(iter(types), "span")
                ),
                status=span_status,  # type: ignore[arg-type]
                sequence_start=first.run_seq or first.global_seq,
                sequence_end=last.run_seq or last.global_seq,
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=(
                    float(tool_result.duration_ms)
                    if tool_result and tool_result.duration_ms is not None
                    else _duration_ms(started_at, ended_at)
                ),
                provider=safe_run.get("provider") if kind == "model" else None,
                model=safe_run.get("model") if kind == "model" else None,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                context_tokens=int(manifest.get("total_tokens") or 0),
                step=int(step_raw) if step_raw is not None else None,
                tool_call_id=call_id,
                tool_name=(getattr(call, "name", None) or (tool_result.name if tool_result else None)),
                tool_arguments=dict(getattr(call, "arguments", {}) or {}),
                tool_result=tool_result,
                context_manifest_artifact_id=context_payload.get("artifact_id"),
                input=(
                    dict(getattr(call, "arguments", {}) or {})
                    if kind in {"tool", "tool_call"}
                    else {"context_manifest_artifact_id": context_payload.get("artifact_id")}
                    if kind == "model"
                    else None
                ),
                output=output,
                attributes={
                    "event_types": [_event_type(event) for event in grouped],
                    "context_manifest": manifest,
                },
                event_ids=[event.event_id for event in grouped],
            )
            spans.append(span)

        for event in controls:
            event_type = _event_type(event)
            spans.append(
                TraceSpan(
                    trace_id=trace_id,
                    span_id=f"event:{event.event_id}",
                    run_id=run_id,
                    parent_span_id=event.parent_span_id,
                    kind=_control_kind(event_type),  # type: ignore[arg-type]
                    name=event_type,
                    status="failed" if event_type == "error" else "completed",
                    sequence_start=event.run_seq or event.global_seq,
                    sequence_end=event.run_seq or event.global_seq,
                    started_at=event.timestamp,
                    ended_at=event.timestamp,
                    duration_ms=0.0,
                    step=(int(event.payload["step"]) if event.payload.get("step") is not None else None),
                    attributes=dict(event.payload),
                    event_ids=[event.event_id],
                )
            )
        lifecycle_events = [
            event
            for event in safe_events
            if _event_type(event)
            in {
                "run_started",
                "run_status",
                "run_completed",
                "run_failed",
                "run_cancelled",
                "run_interrupted",
            }
        ]
        if lifecycle_events:
            first_run_event = lifecycle_events[0]
            last_run_event = lifecycle_events[-1]
            spans.append(
                TraceSpan(
                    trace_id=trace_id,
                    span_id=f"run:{run_id}",
                    run_id=run_id,
                    parent_span_id=None,
                    kind="run",
                    name="run",
                    status=(
                        status
                        if status in {"completed", "failed", "interrupted"}
                        else "interrupted"
                        if status == "cancelled"
                        else "running"
                        if status == "running"
                        else "unset"
                    ),  # type: ignore[arg-type]
                    sequence_start=first_run_event.run_seq or first_run_event.global_seq,
                    sequence_end=last_run_event.run_seq or last_run_event.global_seq,
                    started_at=first_run_event.timestamp,
                    ended_at=last_run_event.timestamp,
                    duration_ms=_duration_ms(first_run_event.timestamp, last_run_event.timestamp),
                    provider=safe_run.get("provider"),
                    model=safe_run.get("model"),
                    attributes={
                        "event_types": [_event_type(event) for event in lifecycle_events],
                        "terminal": lifecycle_events[-1].payload,
                    },
                    event_ids=[event.event_id for event in lifecycle_events],
                )
            )
        spans.sort(key=lambda span: (span.sequence_start, span.span_id))
        span_ids = {span.span_id for span in spans}
        if any(span.parent_span_id and span.parent_span_id not in span_ids for span in spans):
            partial.append("orphan_parent_span")

        metadata = _json_object(safe_run.get("metadata_json"))
        manifests: list[dict[str, Any]] = []
        for event in safe_events:
            manifest = event.payload.get("manifest")
            if _event_type(event) == "context_manifest" and isinstance(manifest, dict):
                manifests.append(manifest)
        versions = cls._versions(safe_run, metadata, manifests, event_schemas)
        usage = Usage.model_validate(_json_object(safe_run.get("usage_json")))
        started = _as_datetime(safe_run.get("created_at"))
        ended = _as_datetime(safe_run.get("finished_at"))
        final_output = "".join(
            message.content for message in safe_messages if message.role == MessageRole.assistant
        ) or str(safe_run.get("output_summary") or "")
        artifact_ids = sorted(
            {
                str(value)
                for span in spans
                for value in (
                    span.context_manifest_artifact_id,
                    span.tool_result.artifact_id if span.tool_result else None,
                )
                if value
            }
        )
        partial = list(dict.fromkeys(partial))
        return AgentTrace(
            trace_id=trace_id,
            run_id=run_id,
            session_id=str(safe_run.get("session_id") or ""),
            root_run_id=str(safe_run.get("root_run_id") or run_id),
            parent_run_id=safe_run.get("parent_run_id"),
            status=status,
            completeness="partial" if partial else "complete",
            partial_reasons=partial,
            provider=safe_run.get("provider"),
            model=safe_run.get("model"),
            started_at=started,
            ended_at=ended,
            duration_ms=_duration_ms(started, ended),
            usage=usage,
            steps=int(safe_run.get("steps") or 0),
            final_output=redact.redact_text(final_output),
            error=redact.redact_text(str(safe_run.get("error") or "")) or None,
            messages=safe_messages,
            spans=spans,
            versions=versions,
            artifact_ids=artifact_ids,
            metadata=redact.redact_obj(
                {
                    "approval": safe_run.get("approval"),
                    "cwd": safe_run.get("cwd"),
                    "delegate_depth": safe_run.get("delegate_depth"),
                    "allow_write": bool(safe_run.get("allow_write", True)),
                    "checkpoint": _model_dump_or_value(checkpoint),
                }
            ),
            event_count=len(safe_events),
        )

    @staticmethod
    def _versions(
        run: dict[str, Any],
        metadata: dict[str, Any],
        manifests: list[dict[str, Any]],
        event_schemas: list[int],
    ) -> TraceVersions:
        tool_hashes: dict[str, str] = {}
        skill_hashes: dict[str, str] = {}
        rule_hashes: dict[str, str] = {}
        prefix_fingerprints: list[str] = []
        for manifest in manifests:
            if manifest.get("prefix_fingerprint"):
                prefix_fingerprints.append(str(manifest["prefix_fingerprint"]))
            for item in manifest.get("items") or []:
                if not isinstance(item, dict) or not item.get("included", True):
                    continue
                section = str(item.get("section") or "")
                source = str(item.get("source") or "")
                value = str(item.get("content_hash") or "")
                if section == "tool_schemas" and source and value:
                    tool_hashes[source] = value
                elif section == "skills" and source and value:
                    skill_hashes[source] = value
                elif section == "workspace_rules" and source and value:
                    rule_hashes[source] = value
        context_request = metadata.get("_agentharness_context_request") or {}
        budget = metadata.get("_agentharness_budget") or {}
        runtime_config = {
            "provider": run.get("provider"),
            "model": run.get("model"),
            "approval": run.get("approval"),
            "cwd": run.get("cwd"),
            "allow_write": bool(run.get("allow_write", True)),
            "budget": budget,
            "tools": context_request.get("tools") if isinstance(context_request, dict) else None,
        }
        prompt = prefix_fingerprints[0] if prefix_fingerprints else _fingerprint(
            {
                "system": context_request.get("system") if isinstance(context_request, dict) else None,
                "goal": context_request.get("original_goal") if isinstance(context_request, dict) else None,
            }
        )
        return TraceVersions(
            event_schema_versions=event_schemas,
            prompt_fingerprint=prompt,
            context_fingerprint=_fingerprint(prefix_fingerprints or manifests),
            tool_schema_fingerprints=tool_hashes,
            skill_fingerprints=skill_hashes,
            workspace_rule_fingerprints=rule_hashes,
            runtime_config_fingerprint=_fingerprint(runtime_config),
        )
