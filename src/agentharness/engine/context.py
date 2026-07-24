"""Context assembly and compaction."""

from __future__ import annotations

from typing import Any

from agentharness.contracts import Message, MessageRole, ToolSpec, Usage


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate when provider count is unavailable (~4 chars/token)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def billable_turn_usage(
    *,
    provider_usage: Usage,
    local_input_estimate: int,
    output_text: str = "",
    inflation_ratio: int = 8,
) -> Usage:
    """Token counts charged against run budget for one model turn.

    Some OpenAI-compatible gateways report wildly inflated prompt/completion
    token counts. Budget enforcement must not treat those as truth, or everyday
    browser tasks die with ``max_tokens exceeded`` after a correct answer.

    Rules:
    - Prefer provider numbers when they stay within ``inflation_ratio`` of the
      harness local estimate (or when no local estimate exists).
    - Otherwise charge the local input/output estimates.
    - Mark ``estimated=True`` when any substitution happens.
    """
    local_out = estimate_tokens(output_text) if output_text else 0
    prov_in = max(0, int(provider_usage.input_tokens or 0))
    prov_out = max(0, int(provider_usage.output_tokens or 0))
    local_in = max(0, int(local_input_estimate or 0))
    used_estimate = bool(provider_usage.estimated)

    if local_in > 0 and prov_in > max(local_in * inflation_ratio, local_in + 4096):
        bill_in = local_in
        used_estimate = True
    elif prov_in > 0:
        bill_in = prov_in
    else:
        bill_in = local_in
        if bill_in:
            used_estimate = True

    if local_out > 0 and prov_out > max(local_out * inflation_ratio, local_out + 1024):
        bill_out = local_out
        used_estimate = True
    elif prov_out > 0:
        bill_out = prov_out
    else:
        bill_out = local_out
        if bill_out:
            used_estimate = True

    return Usage(
        input_tokens=bill_in,
        output_tokens=bill_out,
        total_tokens=bill_in + bill_out,
        estimated=used_estimate,
    )


def estimate_messages_tokens(messages: list[Message]) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(m.content)
        if m.tool_calls:
            for tc in m.tool_calls:
                total += estimate_tokens(tc.name) + estimate_tokens(str(tc.arguments))
    return total


def assemble_context(
    *,
    system: str | None,
    skills: list[str],
    memories: list[str],
    summary: str | None,
    messages: list[Message],
    tools: list[ToolSpec],
    max_tokens: int = 100_000,
) -> tuple[str | None, list[Message], dict[str, Any]]:
    """Build system + messages for a model turn. Compacts if over budget.

    Always preserves the latest user request and tool_call/tool_result pairs.
    """
    parts: list[str] = []
    if system:
        parts.append(system)
    if skills:
        parts.append("## Active skills\n" + "\n\n".join(skills))
    if memories:
        parts.append("## Retrieved memories\n" + "\n".join(f"- {m}" for m in memories))
    if summary:
        parts.append("## Session summary\n" + summary)
    system_text = "\n\n".join(parts) if parts else None

    meta: dict[str, Any] = {
        "token_estimate": estimate_tokens(system_text or "") + estimate_messages_tokens(messages),
        "token_method": "estimate",
        "compacted": False,
    }

    # tool defs counted roughly
    tool_tokens = sum(estimate_tokens(t.name + t.description + str(t.parameters)) for t in tools)
    meta["token_estimate"] += tool_tokens

    if meta["token_estimate"] <= max_tokens:
        return system_text, list(messages), meta

    # Compact: keep last user message + trailing tool pairs, summarize middle
    compacted = compact_messages(messages)
    meta["token_estimate"] = (
        estimate_tokens(system_text or "") + estimate_messages_tokens(compacted) + tool_tokens
    )
    meta["compacted"] = True
    return system_text, compacted, meta


def compact_messages(messages: list[Message]) -> list[Message]:
    """Preserve current request and tool call/result pairs; summarize older turns."""
    if len(messages) <= 6:
        return list(messages)

    # Find last user message index
    last_user = -1
    for i in range(len(messages) - 1, -1, -1):
        role = messages[i].role
        if role == MessageRole.user or role == "user":
            last_user = i
            break

    head = messages[: max(0, last_user - 4)]
    tail = messages[max(0, last_user - 4) :]

    # Build traceable summary of head
    lines: list[str] = []
    for m in head:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        preview = (m.content or "")[:120].replace("\n", " ")
        lines.append(f"[{role}] {preview}")
        if m.tool_calls:
            for tc in m.tool_calls:
                lines.append(f"  tool_call {tc.name}({list(tc.arguments.keys())})")

    summary_msg = Message(
        role=MessageRole.system,
        content="[context_summary]\n" + "\n".join(lines[-40:]),
    )
    return [summary_msg, *tail]
