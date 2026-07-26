"""Validation and policy helpers for governed tool execution."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from agentharness.contracts import EffectKind, ReplayPolicy, ToolCall, ToolResult, ToolSpec

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def tool_call_completed(tool_call: ToolCall, completed: set[str]) -> bool:
    """Use invocation ids for v8 checkpoints and provider ids for legacy checkpoints."""
    return tool_call.invocation_id in completed or tool_call.id in completed


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


__all__ = [
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
