"""Governed procurement tool execution, validation, approvals and recovery."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import random
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
    EffectKind,
    EventType,
    Message,
    MessageRole,
    ReplayPolicy,
    RunRequest,
    RunStatus,
    ToolCall,
    ToolContentPart,
    ToolContext,
    ToolInvocationRecord,
    ToolInvocationStatus,
    ToolResult,
    ToolSpec,
    Usage,
    new_id,
)
from agentharness.engine.events import EventEmitter
from agentharness.engine.run_state import RunContext, ensure_ctx
from agentharness.engine.scheduler import EffectScheduler
from agentharness.security.approval import auto_decision
from agentharness.security.redaction import Redactor
from agentharness.storage.sqlite import Storage
from agentharness.tools.summary import summarize_tool_arguments

if TYPE_CHECKING:
    from agentharness.engine.lifecycle import RunLifecycle

ApprovalCallback = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# State-changing effects advance the dedup epoch: reads are deduped per
# analysis/approval round so read_request x4 polling loops are blocked.
_STATE_CHANGING_EFFECTS = frozenset({
    EffectKind.workspace_write,
    EffectKind.destructive,
})


def tool_call_completed(tool_call: ToolCall, completed: set[str]) -> bool:
    """Use invocation ids for v8 checkpoints and provider ids for legacy checkpoints."""
    return tool_call.invocation_id in completed or tool_call.id in completed


def enabled_tool_names(
    request: RunRequest,
    invocations: list[ToolInvocationRecord],
    registered_names: set[str],
) -> set[str]:
    """Resolve static allowlists plus optional successful-tool prerequisites."""
    enabled = set(registered_names)
    if request.tools is not None:
        enabled.intersection_update(request.tools)
    raw_prerequisites = request.metadata.get("tool_prerequisites")
    if not isinstance(raw_prerequisites, dict):
        return enabled
    succeeded = {
        invocation.tool_name
        for invocation in invocations
        if invocation.status == ToolInvocationStatus.succeeded
    }
    for tool_name, raw_required in raw_prerequisites.items():
        if tool_name not in enabled:
            continue
        if not isinstance(raw_required, list) or not all(
            isinstance(item, str) for item in raw_required
        ):
            enabled.discard(tool_name)
            continue
        if not set(raw_required).issubset(succeeded):
            enabled.discard(tool_name)
    return enabled



def current_stage_index(
    request: RunRequest,
    invocations: list[ToolInvocationRecord],
) -> int:
    """Index of the active explicit tool stage, or -1 when no stage matrix is set.

    The matrix comes from ``request.metadata["tool_stage_matrix"]``: a list of
    ``{"name", "tools", "advance_on"}`` dicts. A stage advances once any of its
    ``advance_on`` tools has a succeeded invocation, turning the prompt-level
    capture → analysis → approve flow into an enforced state machine.
    """
    raw = request.metadata.get("tool_stage_matrix")
    if not isinstance(raw, list) or not raw:
        return -1
    stages = [
        item
        for item in raw
        if isinstance(item, dict)
        and isinstance(item.get("tools"), list)
        and isinstance(item.get("advance_on"), list)
    ]
    if not stages:
        return -1
    initial = request.metadata.get("tool_stage_initial", 0)
    try:
        current = int(initial)
    except (TypeError, ValueError):
        current = 0
    current = max(0, min(current, len(stages) - 1))
    succeeded = {
        invocation.tool_name
        for invocation in invocations
        if invocation.status == ToolInvocationStatus.succeeded
    }
    for index in range(current, len(stages)):
        advance = stages[index].get("advance_on") or []
        advanced = any(
            name in succeeded for name in advance if isinstance(name, str)
        )
        if not advanced:
            # Some stages complete inside another tool pipeline (e.g. the
            # capture tool already runs the analysis and returns
            # stage=analysis_completed). Allow result-marked advancement.
            for marker in stages[index].get("advance_on_result") or []:
                if not isinstance(marker, dict):
                    continue
                marker_tool = marker.get("tool")
                marker_stage = marker.get("stage")
                for invocation in invocations:
                    if (
                        invocation.status == ToolInvocationStatus.succeeded
                        and invocation.tool_name == marker_tool
                        and _invocation_stage(invocation) == marker_stage
                    ):
                        advanced = True
                        break
                if advanced:
                    break
        if not advanced:
            break
        current = index + 1
    return min(current, len(stages) - 1)


def _invocation_stage(invocation: ToolInvocationRecord) -> str:
    """Best-effort stage label embedded in a succeeded tool result payload."""
    result = invocation.result
    if result is None or result.is_error:
        return ""
    try:
        payload = json.loads(result.content or "{}")
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("stage") or "")

def canonical_arguments(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def arguments_sha256(arguments: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_arguments(arguments).encode("utf-8")).hexdigest()


def validate_tool_spec(spec: ToolSpec) -> None:
    if not _TOOL_NAME.fullmatch(spec.name):
        raise ValueError(f"invalid tool name: {spec.name!r}")
    if not spec.description.strip():
        raise ValueError(f"tool {spec.name!r} must have a description")
    schema = spec.parameters or {"type": "object"}
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid JSON schema for tool {spec.name!r}: {exc.message}") from exc
    if schema.get("type") not in (None, "object"):
        raise ValueError(f"tool {spec.name!r} parameters must describe an object")


def validate_tool_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> list[str]:
    schema = spec.parameters or {"type": "object"}
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(arguments), key=lambda item: list(item.path)):
        pointer = "/" + "/".join(str(item) for item in error.absolute_path)
        errors.append(f"{pointer or '/'}: {error.message}")
    return errors


def resolved_replay_policy(spec: ToolSpec, effect: EffectKind) -> ReplayPolicy:
    if spec.replay_policy is not None:
        return spec.replay_policy
    if effect in (EffectKind.pure, EffectKind.workspace_read):
        return ReplayPolicy.safe
    return ReplayPolicy.never


def resolved_parallel_safe(spec: ToolSpec, effect: EffectKind) -> bool:
    if spec.parallel_safe is not None:
        return spec.parallel_safe
    return effect in (EffectKind.pure, EffectKind.workspace_read)


def approval_scope(tool_name: str, effect: EffectKind, arguments: dict[str, Any]) -> str:
    for key in ("path", "url", "server", "context_id", "scope"):
        value = arguments.get(key)
        if value not in (None, ""):
            return f"{tool_name}:{effect.value}:{key}={value}"
    return f"{tool_name}:{effect.value}"


def invalid_arguments_result(
    *, tool_call_id: str, invocation_id: str, tool_name: str, errors: list[str]
) -> ToolResult:
    detail = "; ".join(errors[:8])
    return ToolResult(
        tool_call_id=tool_call_id,
        invocation_id=invocation_id,
        name=tool_name,
        content=f"Invalid tool arguments: {detail}",
        is_error=True,
        error_code="invalid_arguments",
        error_category="validation",
        retryable=True,
        recovery_hint="Call the tool again with arguments matching its JSON schema.",
        attempts=0,
    )


def tool_result_model_content(result: ToolResult) -> str:
    if not result.is_error:
        return result.content
    return json.dumps(
        {
            "ok": False,
            "error": {
                "code": result.error_code or "tool_failed",
                "category": result.error_category or "tool",
                "message": result.content,
                "retryable": result.retryable,
                "recovery_hint": result.recovery_hint,
            },
        },
        ensure_ascii=False,
    )


def _tool_result_messages_for_call(
    messages: list[Message], tool_call: ToolCall
) -> list[Message]:
    assistant_index = -1
    for index, message in enumerate(messages):
        if message.role != MessageRole.assistant or not message.tool_calls:
            continue
        if any(call.invocation_id == tool_call.invocation_id for call in message.tool_calls):
            assistant_index = index
    matches: list[Message] = []
    for message in messages[assistant_index + 1 :]:
        if message.role != MessageRole.tool:
            continue
        if message.tool_result is not None and message.tool_result.invocation_id:
            if message.tool_result.invocation_id == tool_call.invocation_id:
                matches.append(message)
            continue
        if message.tool_call_id == tool_call.id:
            matches.append(message)
    return matches


def _truncate_utf8(text: str, max_bytes: int, suffix: str = "") -> str:
    max_bytes = max(0, max_bytes)
    suffix_bytes = suffix.encode("utf-8")
    if len(suffix_bytes) > max_bytes:
        return suffix_bytes[:max_bytes].decode("utf-8", errors="ignore")
    encoded = text.encode("utf-8")
    if len(encoded) + len(suffix_bytes) <= max_bytes:
        return text + suffix
    available = max_bytes - len(suffix_bytes)
    return encoded[:available].decode("utf-8", errors="ignore") + suffix


def _serialized_tool_result(result: ToolResult) -> bytes:
    return json.dumps(
        result.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _json_string_payload_bytes(text: str) -> int:
    encoded = json.dumps(text, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return max(0, len(encoded) - 2)


def _truncate_json_string_payload(text: str, max_bytes: int, suffix: str) -> str:
    if _json_string_payload_bytes(text) <= max_bytes:
        return text
    bounded_suffix = suffix if _json_string_payload_bytes(suffix) <= max_bytes else ""
    low = 0
    high = len(text)
    best = bounded_suffix
    while low <= high:
        midpoint = (low + high) // 2
        candidate = text[:midpoint] + bounded_suffix
        if _json_string_payload_bytes(candidate) <= max_bytes:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _fit_serialized_tool_result(result: ToolResult, max_bytes: int) -> ToolResult:
    if len(_serialized_tool_result(result)) <= max_bytes:
        return result
    final_output = result.final_output
    base = result.model_copy(
        update={
            "content": "",
            "final_output": "" if final_output is not None else None,
            "parts": [],
        }
    )
    available = max(0, max_bytes - len(_serialized_tool_result(base)))
    bounded_final = None
    if final_output is not None:
        bounded_final = _truncate_json_string_payload(
            final_output,
            available,
            "\n...[final output limit reached]",
        )
        available -= _json_string_payload_bytes(bounded_final)
    bounded_content = _truncate_json_string_payload(
        result.content,
        available,
        "\n...[structured tool result limit reached]",
    )
    return result.model_copy(
        update={
            "content": bounded_content,
            "final_output": bounded_final,
            "parts": [],
        }
    )


class ToolInvocationExecutor:
    """Governed execution of one batch of model tool calls.

    Owns invocation persistence, JSON-schema validation, approval gating,
    effect-aware scheduling, retry/reconcile recovery, result bounding and the
    tool-result messages appended to the transcript. Per-run state lives in the
    shared RunContext registry; run-level policy (budgets, allow_write) arrives
    with each batch via RunRequest.
    """

    def __init__(
        self,
        *,
        storage: Storage,
        tools: dict[str, Any],
        runs: dict[str, RunContext],
        redactor: Redactor,
        events: EventEmitter,
        lifecycle: RunLifecycle,
        spawner: Any = None,
        approval_callback: ApprovalCallback | None = None,
    ) -> None:
        self.storage = storage
        self.tools = tools
        self._runs = runs
        self.redactor = redactor
        self.events = events
        self.lifecycle = lifecycle
        self.spawner = spawner
        self.approval_callback = approval_callback
        self.scheduler = EffectScheduler()

    def _ctx(self, run_id: str) -> RunContext:
        return ensure_ctx(self._runs, run_id)

    def _stage_matrix(self, request: RunRequest) -> list[dict[str, Any]]:
        raw = request.metadata.get("tool_stage_matrix")
        if not isinstance(raw, list):
            return []
        return [
            item
            for item in raw
            if isinstance(item, dict)
            and isinstance(item.get("tools"), list)
            and isinstance(item.get("advance_on"), list)
        ]

    def _reject_invocation(
        self,
        *,
        run_id: str,
        session_id: str,
        run_row: dict[str, Any],
        messages: list[Message],
        tc: ToolCall,
        tool: Any,
        step: int,
        collected_results: list[ToolResult],
        completed_tool_ids: set[str],
        error_code: str,
        error_category: str,
        content: str,
        recovery_hint: str,
        event_type: EventType,
        event_payload: dict[str, Any],
    ) -> None:
        """Persist a governed rejection (stage violation / duplicate call) as a
        failed invocation with an audit event, without crashing the run."""
        effect = self._effect_for(tool, tc)
        spec = tool.spec
        invocation = ToolInvocationRecord(
            id=tc.invocation_id,
            run_id=run_id,
            session_id=session_id,
            step=step,
            ordinal=tc.ordinal,
            provider_call_id=tc.id,
            tool_name=tc.name,
            tool_version=spec.version,
            status=ToolInvocationStatus.failed,
            effect=effect,
            replay_policy=resolved_replay_policy(spec, effect),
            arguments=tc.arguments,
            reason=tc.reason,
            arguments_sha256=arguments_sha256(tc.arguments),
            attempt_count=0,
            error_code=error_code,
            error_category=error_category,
            finished_at=datetime.now(UTC),
        )
        result = ToolResult(
            tool_call_id=tc.id,
            invocation_id=tc.invocation_id,
            name=tc.name,
            content=content,
            is_error=True,
            error_code=error_code,
            error_category=error_category,
            retryable=False,
            recovery_hint=recovery_hint,
            attempts=0,
        )
        invocation.result = result
        self.storage.save_tool_invocation(invocation)
        self._append_tool_result(run_id, session_id, messages, result, run_row)
        collected_results.append(result)
        completed_tool_ids.add(tc.invocation_id)
        self.events.emit_and_update(
            run_id,
            events=[
                self.events.event(
                    run_row,
                    event_type,
                    {
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                        "name": tc.name,
                        "step": step,
                        **event_payload,
                    },
                )
            ],
)

    async def execute_batch(
        self,
        *,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None,
        request: RunRequest,
        messages: list[Message],
        tool_calls: list[ToolCall],
        completed_tool_ids: set[str],
        usage: Usage,
        step: int,
        cancel: asyncio.Event,
    ) -> list[ToolResult]:
        run_row = self.storage.get_run(run_id)
        assert run_row is not None
        collected_results: list[ToolResult] = []

        # Filter already completed (resume safety)
        pending = [
            tc for tc in tool_calls if not tool_call_completed(tc, completed_tool_ids)
        ]
        if not pending:
            return collected_results

        batch_ctx = self._ctx(run_id)
        batch_ctx.completed_tool_ids = set(completed_tool_ids)
        batch_ctx.pending_tool_calls = list(pending)

        self.lifecycle.checkpoint(
            run_id,
            phase="tool_batch",
            step=step,
            messages=messages,
            pending=pending,
            completed=completed_tool_ids,
            usage=usage,
        )

        # Approval gating
        allowed: list[tuple[ToolCall, ToolInvocationRecord]] = []
        recovering_invocations: set[str] = set()
        enabled_names = enabled_tool_names(
            request,
            self.storage.list_tool_invocations(run_id),
            set(self.tools),
        )
        for tc in pending:
            tool = self.tools.get(tc.name)
            if tool is None or tc.name not in enabled_names:
                error_code = "unknown_tool" if tool is None else "tool_disabled"
                disabled_effect = tool.spec.effect if tool is not None else EffectKind.destructive
                error_message = (
                    f"Unknown tool: {tc.name}"
                    if tool is None
                    else f"Tool is not enabled in the current run stage: {tc.name}"
                )
                invocation = ToolInvocationRecord(
                    id=tc.invocation_id,
                    run_id=run_id,
                    session_id=session_id,
                    step=step,
                    ordinal=tc.ordinal,
                    provider_call_id=tc.id,
                    tool_name=tc.name,
                    status=ToolInvocationStatus.failed,
                    effect=disabled_effect,
                    replay_policy=(
                        resolved_replay_policy(tool.spec, disabled_effect)
                        if tool is not None
                        else ReplayPolicy.never
                    ),
                    arguments=tc.arguments,
                    reason=tc.reason,
                    arguments_sha256=arguments_sha256(tc.arguments),
                    attempt_count=0,
                    error_code=error_code,
                    error_category="configuration",
                    finished_at=datetime.now(UTC),
                )
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content=error_message,
                    is_error=True,
                    error_code=error_code,
                    error_category="configuration",
                    retryable=tool is not None,
                    recovery_hint=(
                        "Choose one of the enabled tool schemas."
                        if tool is None
                        else "Complete the required earlier tool successfully, then retry."
                    ),
                    attempts=0,
                )
                invocation.result = result
                self.storage.save_tool_invocation(invocation)
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                collected_results.append(result)
                completed_tool_ids.add(tc.invocation_id)
                continue


            # Explicit tool-stage governance: reject calls outside the current
            # stage with a structured hint so the model can self-correct.
            stage_index = current_stage_index(
                request, self.storage.list_tool_invocations(run_id)
            )
            if stage_index >= 0:
                stages = self._stage_matrix(request)
                stage = stages[stage_index]
                stage_allowed = stage.get("tools") or []
                if tc.name not in stage_allowed:
                    stage_name = str(stage.get("name") or f"stage-{stage_index}")
                    self._ctx(run_id).stage_denied_count += 1
                    self._reject_invocation(
                        run_id=run_id,
                        session_id=session_id,
                        run_row=run_row,
                        messages=messages,
                        tc=tc,
                        tool=tool,
                        step=step,
                        collected_results=collected_results,
                        completed_tool_ids=completed_tool_ids,
                        error_code="tool_stage_denied",
                        error_category="governance",
                        content=(
                            f"越权调用：当前阶段为「{stage_name}」，不允许调用 {tc.name}。"
                            f"本阶段仅允许：{', '.join(stage_allowed)}。"
                            "请根据已有工具结果进入下一阶段后重试。"
                        ),
                        recovery_hint=(
                            f"完成阶段「{stage_name}」的合法工具后再调用 {tc.name}。"
                        ),
                        event_type=EventType.tool_stage_denied,
                        event_payload={
                            "stage": stage_name,
                            "stage_index": stage_index,
                            "tool_name": tc.name,
                            "allowed_tools": list(stage_allowed),
                        },
                    )
                    continue

            # Anti-loop dedup: a tool that already succeeded in the current
            # state epoch (same set of state-changing tool successes) must not
            # be called again. Returns "结果未变化" and counts as a governance
            # anomaly instead of crashing the run. This directly targets the
            # historical read_request x4 polling loop.
            current_invocations = self.storage.list_tool_invocations(run_id)
            epoch_at: dict[str, int] = {}
            epoch = 0
            for item in current_invocations:
                if item.status == ToolInvocationStatus.succeeded:
                    epoch_at[item.id] = epoch
                    if item.effect in _STATE_CHANGING_EFFECTS:
                        epoch += 1
            duplicate = any(
                item.tool_name == tc.name
                and item.status == ToolInvocationStatus.succeeded
                and epoch_at.get(item.id) == epoch
                for item in current_invocations
            )
            if duplicate:
                self._ctx(run_id).duplicate_call_count += 1
                self._reject_invocation(
                    run_id=run_id,
                    session_id=session_id,
                    run_row=run_row,
                    messages=messages,
                    tc=tc,
                    tool=tool,
                    step=step,
                    collected_results=collected_results,
                    completed_tool_ids=completed_tool_ids,
                    error_code="duplicate_tool_call",
                    error_category="governance",
                    content=(
                        f"结果未变化：本轮状态下已成功调用过 {tc.name}，重复调用被拦截。"
                        "请基于已有工具结果继续推进，不要重复调用同一工具。"
                    ),
                    recovery_hint="不要重复调用同一工具；根据已有工具结果继续。",
                    event_type=EventType.tool_call_duplicate,
                    event_payload={"tool_name": tc.name, "step": step, "epoch": epoch},
                )
                continue
            effect = self._effect_for(tool, tc)
            spec = tool.spec
            replay_policy = self._replay_policy_for(tool, tc, effect)
            argument_hash = arguments_sha256(tc.arguments)
            invocation = self.storage.get_tool_invocation(tc.invocation_id)
            if invocation is None:
                # Resume safety: a previously interrupted resume may re-issue a
                # tool at the same (run_id, step, ordinal) slot with a fresh
                # invocation id. Reuse the existing record for that slot instead
                # of crashing on the UNIQUE constraint.
                invocation = next(
                    (
                        item
                        for item in self.storage.list_tool_invocations(run_id)
                        if item.step == step
                        and item.ordinal == tc.ordinal
                        and item.tool_name == tc.name
                    ),
                    None,
                )
                if invocation is not None:
                    tc = tc.model_copy(update={"invocation_id": invocation.id})
            if invocation is None:
                invocation = ToolInvocationRecord(
                    id=tc.invocation_id,
                    run_id=run_id,
                    session_id=session_id,
                    step=step,
                    ordinal=tc.ordinal,
                    provider_call_id=tc.id,
                    tool_name=tc.name,
                    tool_version=spec.version,
                    status=ToolInvocationStatus.received,
                    effect=effect,
                    replay_policy=replay_policy,
                    arguments=tc.arguments,
                    reason=tc.reason,
                    arguments_sha256=argument_hash,
                )
                self.storage.save_tool_invocation(invocation)
            elif invocation.result is not None and invocation.status in (
                ToolInvocationStatus.succeeded,
                ToolInvocationStatus.failed,
            ):
                result = invocation.result
                existing_results = _tool_result_messages_for_call(messages, tc)
                if existing_results and not any(
                    message.tool_result == result for message in existing_results
                ):
                    self._drop_tool_result_messages(run_id, messages, tc)
                    existing_results = []
                if not existing_results:
                    self._append_tool_result(run_id, session_id, messages, result, run_row)
                collected_results.append(result)
                completed_tool_ids.add(tc.invocation_id)
                continue
            elif invocation.status == ToolInvocationStatus.indeterminate or (
                invocation.status == ToolInvocationStatus.running
                and invocation.replay_policy == ReplayPolicy.never
            ):
                self.storage.finish_running_tool_attempts(
                    invocation.id,
                    status="indeterminate",
                    error_code="outcome_indeterminate",
                    error_category="recovery",
                )
                invocation = invocation.model_copy(
                    update={
                        "status": ToolInvocationStatus.indeterminate,
                        "error_code": "outcome_indeterminate",
                        "error_category": "recovery",
                        "updated_at": datetime.now(UTC),
                        "finished_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                self._ctx(run_id).indeterminate_reason = (
                    f"Tool {tc.name} may have produced an external side effect before interruption"
                )
                self.events.emit_and_update(
                    run_id,
                    events=[
                        self.events.event(
                            run_row,
                            EventType.tool_execution_indeterminate,
                            {
                                "tool_call_id": tc.id,
                                "invocation_id": tc.invocation_id,
                                "name": tc.name,
                                "error_code": "outcome_indeterminate",
                            },
                        )
                    ],
                )
                self._drop_tool_result_messages(run_id, messages, tc)
                continue
            elif invocation.status in (
                ToolInvocationStatus.running,
                ToolInvocationStatus.cancelled,
            ):
                self._drop_tool_result_messages(run_id, messages, tc)
                recovering_invocations.add(invocation.id)

            validation_errors = validate_tool_arguments(spec, tc.arguments)
            if validation_errors:
                result = invalid_arguments_result(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    tool_name=tc.name,
                    errors=validation_errors,
                )
                invocation = invocation.model_copy(
                    update={
                        "status": ToolInvocationStatus.failed,
                        "result": result,
                        "error_code": result.error_code,
                        "error_category": result.error_category,
                        "updated_at": datetime.now(UTC),
                        "finished_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                collected_results.append(result)
                completed_tool_ids.add(tc.invocation_id)
                continue

            invocation = invocation.model_copy(
                update={
                    "status": ToolInvocationStatus.validated,
                    "effect": effect,
                    "replay_policy": replay_policy,
                    "arguments_sha256": argument_hash,
                    "updated_at": datetime.now(UTC),
                }
            )
            self.storage.save_tool_invocation(invocation)
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        EventType.tool_call_validated,
                        {
                            "tool_call_id": tc.id,
                            "invocation_id": tc.invocation_id,
                            "name": tc.name,
                            "effect": effect.value,
                            "arguments_sha256": argument_hash,
                            "reason": tc.reason,
                        },
                    )
                ],
            )
            requires_confirmation = bool(
                getattr(tool, "requires_confirmation", False)
            ) or effect == EffectKind.destructive
            # Child runs default readonly — block write effects without grant
            if not request.allow_write and effect in (
                EffectKind.workspace_write,
                EffectKind.destructive,
            ):
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content="Write permission not granted for this run",
                    is_error=True,
                    error_code="write_not_allowed",
                    error_category="permission",
                    retryable=False,
                    recovery_hint="Request a writable run or use read-only verification.",
                    attempts=0,
                )
                invocation = invocation.model_copy(
                    update={
                        "status": ToolInvocationStatus.failed,
                        "result": result,
                        "error_code": result.error_code,
                        "error_category": result.error_category,
                        "updated_at": datetime.now(UTC),
                        "finished_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                collected_results.append(result)
                completed_tool_ids.add(tc.invocation_id)
                continue

            decision = (
                ApprovalDecision.deny
                if requires_confirmation and request.approval == ApprovalMode.never
                else None
                if requires_confirmation
                else auto_decision(effect, request.approval)
            )
            # Run-level allow list
            run_ctx = self._runs.get(run_id)
            scope = approval_scope(tc.name, effect, tc.arguments)
            if (
                not requires_confirmation
                and decision is None
                and run_ctx is not None
                and scope in run_ctx.approval_scopes
            ):
                decision = ApprovalDecision.allow_once

            if decision is None:
                # Interactive approval
                apr = ApprovalRequest(
                    run_id=run_id,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    invocation_id=tc.invocation_id,
                    tool_version=spec.version,
                    effect=effect,
                    arguments_summary=self.redactor.redact_text(
                        json.dumps(tc.arguments, ensure_ascii=False)[:500]
                    ),
                    arguments_sha256=argument_hash,
                    approval_scope=scope,
                    requires_confirmation=requires_confirmation,
                )
                invocation = invocation.model_copy(
                    update={
                        "status": ToolInvocationStatus.waiting_approval,
                        "approval_id": apr.id,
                        "updated_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                self.storage.save_approval(apr.model_dump(mode="json"))
                self.storage.update_run(run_id, status=RunStatus.waiting_approval)
                self.events.emit_and_update(
                    run_id,
                    events=[
                        self.events.event(
                            run_row,
                            EventType.approval_requested,
                            {
                                "approval_id": apr.id,
                                "tool_call_id": tc.id,
                                "invocation_id": tc.invocation_id,
                                "tool": tc.name,
                                "effect": effect.value,
                                "arguments_summary": apr.arguments_summary,
                                "requires_confirmation": requires_confirmation,
                                "arguments_sha256": argument_hash,
                                "approval_scope": scope,
                            },
                        )
                    ],
                )
                self.lifecycle.checkpoint(
                    run_id,
                    phase="waiting_approval",
                    step=step,
                    messages=messages,
                    pending=pending,
                    completed=completed_tool_ids,
                    usage=usage,
                    status=RunStatus.waiting_approval,
                    approval_token=apr.id,
                )
                if self.approval_callback is None:
                    decision = ApprovalDecision.deny
                    cancelled_while_waiting = False
                else:
                    # Time parked waiting for the buyer does not consume the
                    # run's wall-clock budget (see RunContext wall_* fields).
                    pause_ctx = self._ctx(run_id)
                    pause_ctx.wall_pause_started = time.monotonic()
                    try:
                        approval_task = asyncio.ensure_future(self.approval_callback(apr))
                        cancel_waiter = asyncio.create_task(cancel.wait())
                        try:
                            done, _ = await asyncio.wait(
                                {approval_task, cancel_waiter},
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            cancelled_while_waiting = cancel_waiter in done
                            if cancelled_while_waiting:
                                approval_task.cancel()
                                with suppress(asyncio.CancelledError):
                                    await approval_task
                                decision = ApprovalDecision.deny
                            else:
                                try:
                                    decision = approval_task.result()
                                except Exception:  # noqa: BLE001
                                    decision = ApprovalDecision.deny
                        finally:
                            cancel_waiter.cancel()
                            with suppress(asyncio.CancelledError):
                                await cancel_waiter
                    finally:
                        if pause_ctx.wall_pause_started is not None:
                            pause_ctx.wall_paused_s += (
                                time.monotonic() - pause_ctx.wall_pause_started
                            )
                            pause_ctx.wall_pause_started = None
                if requires_confirmation and decision == ApprovalDecision.allow_run:
                    decision = ApprovalDecision.allow_once
                apr.decision = decision
                self.storage.save_approval(
                    {
                        **apr.model_dump(mode="json"),
                        "decision": decision.value,
                        "resolved_at": datetime.now(UTC).isoformat(),
                    }
                )
                if not cancelled_while_waiting:
                    self.storage.update_run(run_id, status=RunStatus.running)
                self.events.emit_and_update(
                    run_id,
                    events=[
                        self.events.event(
                            run_row,
                            EventType.approval_resolved,
                            {
                                "approval_id": apr.id,
                                "tool_call_id": tc.id,
                                "decision": decision.value,
                                "tool": tc.name,
                            },
                        )
                    ],
                )
                if decision == ApprovalDecision.allow_run and not requires_confirmation:
                    self._ctx(run_id).approval_scopes.add(scope)
                if cancelled_while_waiting:
                    return collected_results

            if decision == ApprovalDecision.deny:
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content="Approval denied",
                    is_error=True,
                    error_code="approval_denied",
                    error_category="approval",
                    retryable=False,
                    recovery_hint="Ask a human to approve this governed action.",
                    attempts=0,
                )
                invocation = invocation.model_copy(
                    update={
                        "status": ToolInvocationStatus.failed,
                        "result": result,
                        "error_code": result.error_code,
                        "error_category": result.error_category,
                        "updated_at": datetime.now(UTC),
                        "finished_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                collected_results.append(result)
                completed_tool_ids.add(tc.invocation_id)
                continue

            invocation = invocation.model_copy(
                update={
                    "status": ToolInvocationStatus.approved,
                    "updated_at": datetime.now(UTC),
                }
            )
            self.storage.save_tool_invocation(invocation)
            allowed.append((tc, invocation))

        async def make_runner(
            tc: ToolCall, invocation: ToolInvocationRecord
        ) -> ToolResult:
            return await self._run_tool_invocation(
                run_id=run_id,
                session_id=session_id,
                root_run_id=root_run_id,
                parent_run_id=parent_run_id,
                request=request,
                usage=usage,
                cancel=cancel,
                run_row=run_row,
                tc=tc,
                invocation=invocation,
                recovering_invocations=recovering_invocations,
            )

        # Batch with concurrency rules (scheduler wraps make_runner once)
        items: list[tuple[Any, ...]] = []
        for tc, invocation in allowed:
            tool = self.tools[tc.name]
            effect = self._effect_for(tool, tc)
            parallel_safe = self._parallel_safe_for(tool, tc, effect)
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        EventType.tool_execution_queued,
                        {
                            "tool_call_id": tc.id,
                            "invocation_id": tc.invocation_id,
                            "name": tc.name,
                            "effect": effect.value,
                            "parallel_safe": parallel_safe,
                        },
                    )
                ],
            )
            items.append(
                (
                    effect,
                    lambda tc=tc, invocation=invocation: make_runner(tc, invocation),
                    parallel_safe,
                )
            )

        if items:
            results = await self.scheduler.run_batch(
                items, max_concurrency=request.budget.max_concurrent_tools
            )
            collected_results.extend(results)
            for result in results:
                # Cancel/interrupt mid-flight: error results are incomplete — keep pending
                # so resume re-runs them. Successful tools (and non-cancel failures) complete.
                saved = (
                    self.storage.get_tool_invocation(result.invocation_id)
                    if result.invocation_id
                    else None
                )
                incomplete = (
                    saved is not None
                    and saved.status == ToolInvocationStatus.indeterminate
                ) or (cancel.is_set() and result.is_error)
                if incomplete:
                    continue
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                completed_id = result.invocation_id or result.tool_call_id
                completed_tool_ids.add(completed_id)
                self._ctx(run_id).completed_tool_ids.add(completed_id)

        # Drop finished tools from pending; incomplete stay for resume
        batch_ctx = self._ctx(run_id)
        batch_ctx.pending_tool_calls = [
            tc for tc in pending if not tool_call_completed(tc, completed_tool_ids)
        ]
        batch_ctx.completed_tool_ids = set(completed_tool_ids)

        self.lifecycle.checkpoint(
            run_id,
            phase="tool_batch",
            step=step,
            messages=messages,
            pending=batch_ctx.pending_tool_calls,
            completed=completed_tool_ids,
            usage=usage,
        )
        return collected_results

    async def _run_tool_invocation(
        self,
        *,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None,
        request: RunRequest,
        usage: Usage,
        cancel: asyncio.Event,
        run_row: dict[str, Any],
        tc: ToolCall,
        invocation: ToolInvocationRecord,
        recovering_invocations: set[str],
    ) -> ToolResult:
        """Execute one tool. Concurrency is applied by EffectScheduler outside —
        do not nest scheduler.run here (asyncio.Lock is not reentrant)."""
        if cancel.is_set():
            return ToolResult(
                tool_call_id=tc.id,
                invocation_id=tc.invocation_id,
                name=tc.name,
                content="cancelled",
                is_error=True,
                error_code="cancelled",
                error_category="cancellation",
                retryable=True,
                recovery_hint="Resume the run when cancellation is cleared.",
                attempts=0,
            )
        tool = self.tools[tc.name]
        spec = tool.spec
        span_id = new_id()
        t0 = time.monotonic()
        self.events.emit_and_update(
            run_id,
            events=[
                self.events.event(
                    run_row,
                    EventType.tool_execution_started,
                    {
                        "kind": "tool",
                        "name": tc.name,
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                    },
                    span_id=span_id,
                ),
                self.events.event(
                    run_row,
                    EventType.span_start,
                    {
                        "kind": "tool",
                        "name": tc.name,
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                    },
                    span_id=span_id,
                ),
            ],
        )
        ctx = ToolContext(
            run_id=run_id,
            session_id=session_id,
            cwd=request.cwd or ".",
            extra_dirs=request.extra_dirs,
            data_dir=str(self.storage.data_dir),
            allow_write=request.allow_write,
            cancel_event=cancel,
            approval_mode=request.approval,
            shell=request.shell,
            metadata={
                "root_run_id": root_run_id,
                "parent_run_id": parent_run_id,
                "tool_call_id": tc.id,
                "budget": request.budget.model_dump(),
                "pricing": request.pricing.model_dump(mode="json"),
                "provider": (
                    usage.provider_attempts[-1].provider
                    if usage.provider_attempts
                    else request.provider
                ),
                "model": (
                    usage.provider_attempts[-1].model
                    if usage.provider_attempts
                    else request.model
                ),
            },
            harness=self.spawner,
        )
        result = ToolResult(
            tool_call_id=tc.id,
            invocation_id=tc.invocation_id,
            name=tc.name,
            content="Tool did not run",
            is_error=True,
            error_code="tool_not_run",
            error_category="tool",
        )

        recovering = invocation.id in recovering_invocations
        if recovering:
            self.storage.finish_running_tool_attempts(
                invocation.id,
                status="interrupted",
                error_code="process_lost",
                error_category="recovery",
            )

        if (
            invocation.id in recovering_invocations
            and invocation.replay_policy == ReplayPolicy.reconcile
        ):
            reconciler = getattr(tool, "reconcile", None)
            if callable(reconciler):
                try:
                    reconciled = await self._invoke_reconciler(
                        reconciler,
                        ctx,
                        tc.arguments,
                        timeout_s=spec.timeout_s,
                        cancel=cancel,
                    )
                except TimeoutError:
                    reconciled = ToolResult(
                        tool_call_id=tc.id,
                        invocation_id=tc.invocation_id,
                        name=tc.name,
                        content=f"Reconciliation timed out after {spec.timeout_s:g}s",
                        is_error=True,
                        error_code="outcome_indeterminate",
                        error_category="recovery",
                        retryable=False,
                        recovery_hint="Inspect the target state before deciding whether to retry.",
                    )
                except Exception as exc:  # noqa: BLE001 - fail closed on uncertain recovery
                    reconciled = ToolResult(
                        tool_call_id=tc.id,
                        invocation_id=tc.invocation_id,
                        name=tc.name,
                        content=f"Could not reconcile interrupted tool outcome: {exc}",
                        is_error=True,
                        error_code="outcome_indeterminate",
                        error_category="recovery",
                        retryable=False,
                        recovery_hint="Inspect the target state before deciding whether to retry.",
                    )
            else:
                reconciled = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content="Tool declares reconcile recovery but provides no reconciler",
                    is_error=True,
                    error_code="outcome_indeterminate",
                    error_category="recovery",
                    retryable=False,
                    recovery_hint="Inspect the target state before deciding whether to retry.",
                )
            if isinstance(reconciled, ToolResult):
                result = reconciled.model_copy(
                    update={
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                        "name": tc.name,
                        "attempts": invocation.attempt_count,
                    }
                )
                reconciliation_failed = reconciled.is_error
                if reconciliation_failed:
                    self._ctx(run_id).indeterminate_reason = (
                        f"Could not reconcile the previous {tc.name} outcome"
                    )
                invocation = invocation.model_copy(
                    update={
                        "status": (
                            ToolInvocationStatus.indeterminate
                            if reconciliation_failed
                            else ToolInvocationStatus.succeeded
                        ),
                        "result": result,
                        "error_code": result.error_code,
                        "error_category": result.error_category,
                        "updated_at": datetime.now(UTC),
                        "finished_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                return result

        max_attempts = spec.max_attempts
        first_attempt = invocation.attempt_count + 1
        for attempt_offset in range(max_attempts):
            attempt = first_attempt + attempt_offset
            attempt_started = time.monotonic()
            invocation = invocation.model_copy(
                update={
                    "status": ToolInvocationStatus.running,
                    "attempt_count": attempt,
                    "updated_at": datetime.now(UTC),
                    "started_at": invocation.started_at or datetime.now(UTC),
                }
            )
            self.storage.save_tool_invocation(invocation)
            attempt_id = self.storage.start_tool_attempt(invocation.id, attempt)
            try:
                async with asyncio.timeout(spec.timeout_s):
                    result = await tool.run(ctx, tc.arguments)
            except TimeoutError:
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content=f"Tool timed out after {spec.timeout_s:g}s",
                    is_error=True,
                    error_code="tool_timeout",
                    error_category="timeout",
                    retryable=True,
                    recovery_hint="Retry only if the tool is safe or its outcome can be reconciled.",
                )
            except asyncio.CancelledError:
                if not cancel.is_set():
                    raise
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content="cancelled",
                    is_error=True,
                    error_code="cancelled",
                    error_category="cancellation",
                    retryable=True,
                )
            except Exception as exc:  # noqa: BLE001
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content=f"Tool error: {exc}",
                    is_error=True,
                    error_code="tool_exception",
                    error_category="tool",
                    retryable=False,
                    recovery_hint="Inspect the tool error before deciding whether to retry.",
                )

            result = result.model_copy(
                update={
                    "tool_call_id": tc.id,
                    "invocation_id": tc.invocation_id,
                    "name": tc.name,
                    "attempts": attempt,
                }
            )
            if (
                result.is_error
                and result.error_code in {"tool_timeout", "cancelled"}
                and invocation.replay_policy == ReplayPolicy.reconcile
            ):
                reconciler = getattr(tool, "reconcile", None)
                if callable(reconciler):
                    try:
                        reconciled = await self._invoke_reconciler(
                            reconciler,
                            ctx,
                            tc.arguments,
                            timeout_s=spec.timeout_s,
                            cancel=cancel,
                        )
                    except TimeoutError:
                        reconciled = ToolResult(
                            tool_call_id=tc.id,
                            invocation_id=tc.invocation_id,
                            name=tc.name,
                            content=f"Reconciliation timed out after {spec.timeout_s:g}s",
                            is_error=True,
                            error_code="outcome_indeterminate",
                            error_category="recovery",
                            retryable=False,
                            recovery_hint=(
                                "Inspect the target state before deciding whether to retry."
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001 - uncertain side effect
                        reconciled = ToolResult(
                            tool_call_id=tc.id,
                            invocation_id=tc.invocation_id,
                            name=tc.name,
                            content=f"Could not reconcile timed out tool outcome: {exc}",
                            is_error=True,
                            error_code="outcome_indeterminate",
                            error_category="recovery",
                            retryable=False,
                            recovery_hint=(
                                "Inspect the target state before deciding whether to retry."
                            ),
                        )
                else:
                    reconciled = ToolResult(
                        tool_call_id=tc.id,
                        invocation_id=tc.invocation_id,
                        name=tc.name,
                        content="Tool declares reconcile recovery but provides no reconciler",
                        is_error=True,
                        error_code="outcome_indeterminate",
                        error_category="recovery",
                        retryable=False,
                        recovery_hint="Inspect the target state before deciding whether to retry.",
                    )
                if isinstance(reconciled, ToolResult):
                    result = reconciled.model_copy(
                        update={
                            "tool_call_id": tc.id,
                            "invocation_id": tc.invocation_id,
                            "name": tc.name,
                            "attempts": attempt,
                            **(
                                {
                                    "error_code": "outcome_indeterminate",
                                    "error_category": "recovery",
                                    "retryable": False,
                                }
                                if reconciled.is_error
                                else {}
                            ),
                        }
                    )
                else:
                    result = result.model_copy(
                        update={
                            "retryable": False,
                            "recovery_hint": (
                                "Reconciliation found no completed side effect; retry explicitly."
                            ),
                        }
                    )
            if result.is_error and cancel.is_set() and not result.error_code:
                result = result.model_copy(
                    update={
                        "error_code": "cancelled",
                        "error_category": "cancellation",
                        "retryable": True,
                        "recovery_hint": result.recovery_hint
                        or "Resume the run to retry this incomplete tool call.",
                    }
                )
            if result.is_error and not result.error_code:
                result = result.model_copy(
                    update={
                        "error_code": "tool_failed",
                        "error_category": result.error_category or "tool",
                        "recovery_hint": result.recovery_hint
                        or "Inspect the tool result and retry with corrected arguments.",
                    }
                )
            attempt_duration = (time.monotonic() - attempt_started) * 1000
            self.storage.finish_tool_attempt(
                attempt_id,
                status="failed" if result.is_error else "succeeded",
                duration_ms=attempt_duration,
                error_code=result.error_code,
                error_category=result.error_category,
            )
            can_retry = (
                result.is_error
                and result.retryable
                and invocation.replay_policy == ReplayPolicy.safe
                and attempt_offset + 1 < max_attempts
                and not cancel.is_set()
            )
            if not can_retry:
                break
            delay = min(2.0, 0.25 * (2 ** (attempt - 1))) * random.uniform(0.5, 1.5)
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        EventType.tool_retry,
                        {
                            "tool_call_id": tc.id,
                            "invocation_id": tc.invocation_id,
                            "name": tc.name,
                            "attempt": attempt,
                            "delay_s": delay,
                            "error_code": result.error_code,
                        },
                        span_id=span_id,
                    )
                ],
            )
            await asyncio.sleep(delay)

        duration = (time.monotonic() - t0) * 1000
        result = result.model_copy(update={"duration_ms": duration})
        result = self._bound_tool_result(result, spec=spec, request=request)

        uncertain = result.error_code == "outcome_indeterminate" or (
            invocation.replay_policy == ReplayPolicy.never
            and result.is_error
            and result.error_code in {"tool_timeout", "cancelled"}
        )
        terminal_status = (
            ToolInvocationStatus.indeterminate
            if uncertain
            else ToolInvocationStatus.cancelled
            if result.error_code == "cancelled"
            else ToolInvocationStatus.failed
            if result.is_error
            else ToolInvocationStatus.succeeded
        )
        if uncertain:
            result = result.model_copy(
                update={
                    "error_code": "outcome_indeterminate",
                    "error_category": "recovery",
                    "retryable": False,
                    "recovery_hint": "Inspect the external system before deciding whether to retry.",
                }
            )
            self._ctx(run_id).indeterminate_reason = (
                f"Outcome of {tc.name} is indeterminate after timeout or cancellation"
            )
        invocation = invocation.model_copy(
            update={
                "status": terminal_status,
                "result": result,
                "error_code": result.error_code,
                "error_category": result.error_category,
                "updated_at": datetime.now(UTC),
                "finished_at": datetime.now(UTC),
            }
        )
        self.storage.save_tool_invocation(invocation)
        if terminal_status in (
            ToolInvocationStatus.cancelled,
            ToolInvocationStatus.indeterminate,
        ):
            lifecycle_type = (
                EventType.tool_execution_indeterminate
                if terminal_status == ToolInvocationStatus.indeterminate
                else EventType.tool_execution_cancelled
            )
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        lifecycle_type,
                        {
                            "tool_call_id": tc.id,
                            "invocation_id": tc.invocation_id,
                            "name": tc.name,
                            "error_code": result.error_code,
                        },
                        span_id=span_id,
                    )
                ],
            )
        self.events.emit_and_update(
            run_id,
            events=[
                self.events.event(
                    run_row,
                    EventType.tool_result,
                    {
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                        "name": tc.name,
                        "is_error": result.is_error,
                        "content_preview": self.redactor.redact_text(result.content[:300]),
                        "duration_ms": duration,
                        "attempts": result.attempts,
                        "artifact_id": result.artifact_id,
                        "error_code": result.error_code,
                        "error_category": result.error_category,
                        "retryable": result.retryable,
                        "recovery_hint": self.redactor.redact_text(
                            result.recovery_hint or ""
                        ),
                    },
                    span_id=span_id,
                ),
                self.events.event(
                    run_row,
                    EventType.span_end,
                    {"kind": "tool", "name": tc.name, "status": terminal_status.value},
                    span_id=span_id,
                ),
                self.events.event(
                    run_row,
                    EventType.tool_call_end,
                    {
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                        "name": tc.name,
                        "is_error": result.is_error,
                        "arguments_summary": self.redactor.redact_text(
                            summarize_tool_arguments(tc.arguments)
                        ),
                    },
                    span_id=span_id,
                ),
            ],
        )
        return result

    async def _invoke_reconciler(
        self,
        reconciler: Callable[[ToolContext, dict[str, Any]], Any],
        ctx: ToolContext,
        arguments: dict[str, Any],
        *,
        timeout_s: float,
        cancel: asyncio.Event,
    ) -> Any:
        async def invoke() -> Any:
            if inspect.iscoroutinefunction(reconciler):
                return await reconciler(ctx, arguments)
            value = await asyncio.to_thread(reconciler, ctx, arguments)
            return await value if inspect.isawaitable(value) else value

        reconcile_task = asyncio.create_task(invoke())
        cancel_waiter = (
            asyncio.create_task(cancel.wait()) if not cancel.is_set() else None
        )
        try:
            async with asyncio.timeout(timeout_s):
                if cancel_waiter is None:
                    return await reconcile_task
                done, _ = await asyncio.wait(
                    {reconcile_task, cancel_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_waiter in done:
                    reconcile_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await reconcile_task
                    raise RuntimeError("reconciliation cancelled")
                return reconcile_task.result()
        finally:
            if not reconcile_task.done():
                reconcile_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reconcile_task
            if cancel_waiter is not None:
                cancel_waiter.cancel()
                with suppress(asyncio.CancelledError):
                    await cancel_waiter

    def _bound_tool_result(
        self,
        result: ToolResult,
        *,
        spec: ToolSpec,
        request: RunRequest,
    ) -> ToolResult:
        content_limit = min(spec.max_result_bytes, request.budget.max_tool_result_bytes)
        content_marker = "\n...[tool result limit reached]"
        content = result.content
        if len(content.encode("utf-8")) > content_limit:
            content = _truncate_utf8(content, content_limit, content_marker)
        final_output = result.final_output
        if final_output is not None:
            final_limit = min(content_limit, request.budget.max_output_length)
            if len(final_output.encode("utf-8")) > final_limit:
                final_output = _truncate_utf8(
                    final_output,
                    final_limit,
                    "\n...[final output limit reached]",
                )

        parts = result.parts or [ToolContentPart(type="text", text=content)]
        result = result.model_copy(
            update={
                "content": content,
                "final_output": final_output,
                "parts": parts,
                "error_code": (
                    _truncate_utf8(result.error_code, 128) if result.error_code else None
                ),
                "error_category": (
                    _truncate_utf8(result.error_category, 128)
                    if result.error_category
                    else None
                ),
                "recovery_hint": (
                    _truncate_utf8(result.recovery_hint, 512)
                    if result.recovery_hint
                    else None
                ),
            }
        )

        total_limit = request.budget.max_tool_result_bytes
        result = _fit_serialized_tool_result(result, total_limit)
        serialized = _serialized_tool_result(result)

        inline_limit = request.budget.max_inline_tool_result_bytes
        if len(serialized) > inline_limit:
            meta = self.storage.artifacts.put(
                serialized,
                content_type="application/json",
                summary=result.content[:200],
            )
            meta["id"] = self.storage.register_artifact(meta)
            suffix = f"\n...[artifact:{meta['id']} sha={meta['sha256'][:12]}]"
            result = result.model_copy(
                update={
                    "artifact_id": meta["id"],
                    "content": _truncate_utf8(result.content, inline_limit, suffix),
                    "parts": [
                        ToolContentPart(
                            type="resource",
                            text="Full tool result stored as artifact",
                            mime_type="application/json",
                            artifact_id=meta["id"],
                        )
                    ],
                }
            )
            result = _fit_serialized_tool_result(result, total_limit)
        return result

    def _effect_for(self, tool: Any, tc: ToolCall) -> EffectKind:
        """Resolve a domain tool's dynamic effect, if it provides one."""
        effect_for = getattr(tool, "effect_for", None)
        if callable(effect_for):
            args = tc.arguments if isinstance(tc.arguments, dict) else {}
            try:
                dynamic = effect_for(args)
            except Exception:  # noqa: BLE001
                dynamic = None
            if isinstance(dynamic, EffectKind):
                return dynamic
        return tool.spec.effect

    def _replay_policy_for(
        self, tool: Any, tc: ToolCall, effect: EffectKind
    ) -> ReplayPolicy:
        policy_for = getattr(tool, "replay_policy_for", None)
        if callable(policy_for):
            try:
                dynamic = policy_for(tc.arguments)
            except Exception:  # noqa: BLE001 - fail closed
                dynamic = None
            if isinstance(dynamic, ReplayPolicy):
                return dynamic
        return resolved_replay_policy(tool.spec, effect)

    def _parallel_safe_for(
        self, tool: Any, tc: ToolCall, effect: EffectKind
    ) -> bool:
        policy_for = getattr(tool, "parallel_safe_for", None)
        if callable(policy_for):
            try:
                dynamic = policy_for(tc.arguments)
            except Exception:  # noqa: BLE001 - fail closed
                return False
            if isinstance(dynamic, bool):
                return dynamic
        return resolved_parallel_safe(tool.spec, effect)

    def _append_tool_result(
        self,
        run_id: str,
        session_id: str,
        messages: list[Message],
        result: ToolResult,
        run_row: dict[str, Any],
    ) -> None:
        content = self.redactor.redact_text(tool_result_model_content(result))
        msg = Message(
            role=MessageRole.tool,
            content=content,
            tool_call_id=result.tool_call_id,
            name=result.name,
            tool_result=result.model_copy(update={"content": self.redactor.redact_text(result.content)}),
        )
        messages.append(msg)
        self.storage.save_message(run_id, session_id, msg, seq=len(messages))

    def _drop_tool_result_messages(
        self,
        run_id: str,
        messages: list[Message],
        tool_call: ToolCall,
    ) -> None:
        stale = _tool_result_messages_for_call(messages, tool_call)
        if not stale:
            return
        stale_ids = {message.id for message in stale}
        messages[:] = [message for message in messages if message.id not in stale_ids]
        self.storage.delete_messages(run_id, list(stale_ids))


__all__ = [
    "ApprovalCallback",
    "ToolInvocationExecutor",
    "approval_scope",
    "arguments_sha256",
    "canonical_arguments",
    "invalid_arguments_result",
    "resolved_parallel_safe",
    "resolved_replay_policy",
    "tool_call_completed",
    "tool_result_model_content",
    "validate_tool_arguments",
    "validate_tool_spec",
]

