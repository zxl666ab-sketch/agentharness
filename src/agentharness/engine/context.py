"""Context assembly and compaction."""

from __future__ import annotations

from typing import Any

from agentharness.contracts import Message, MessageRole, ToolSpec


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate when provider count is unavailable (~4 chars/token)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


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
