from pathlib import Path

def patch(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"{label}: anchor not found")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"ok {label}")

# 1) context.py
patch(
    "src/agentharness/engine/context.py",
    '''from agentharness.contracts import Message, MessageRole, ToolSpec


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate when provider count is unavailable (~4 chars/token)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def estimate_messages_tokens(messages: list[Message]) -> int:
''',
    '''from agentharness.contracts import Message, MessageRole, ToolSpec, Usage


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
''',
    "context.billable_turn_usage",
)

# 2) runtime.py import + usage accumulation + final-answer budget
patch(
    "src/agentharness/engine/runtime.py",
    "from agentharness.engine.context import assemble_context, estimate_tokens\n",
    "from agentharness.engine.context import assemble_context, billable_turn_usage, estimate_tokens\n",
    "runtime.import",
)

patch(
    "src/agentharness/engine/runtime.py",
    '''            text = "".join(text_parts)
            if not turn_usage.total_tokens:
                # Deterministic estimate
                turn_usage = Usage(
                    input_tokens=estimate_tokens(system or "") + sum(
                        estimate_tokens(m.content) for m in ctx_messages
                    ),
                    output_tokens=estimate_tokens(text),
                    total_tokens=0,
                    estimated=True,
                )
                turn_usage.total_tokens = turn_usage.input_tokens + turn_usage.output_tokens

            usage.input_tokens += turn_usage.input_tokens
            usage.output_tokens += turn_usage.output_tokens
            usage.total_tokens += turn_usage.total_tokens
            usage.estimated = usage.estimated or turn_usage.estimated
            # Per-turn breakdown for CLI / inspector (cumulative fields above stay as Σ).
            usage.last_input_tokens = turn_usage.input_tokens
            usage.last_output_tokens = turn_usage.output_tokens
            usage.last_local_estimate = int(ctx_meta.get("token_estimate") or 0)
            usage.model_turns = step + 1

            if usage.total_tokens > budget.max_tokens and error_msg is None:
                error_msg = "max_tokens exceeded"
                error_kind = "budget"

            tool_calls = [tool_acc[i] for i in order if i in tool_acc]
''',
    '''            text = "".join(text_parts)
            if not turn_usage.total_tokens:
                # Deterministic estimate
                turn_usage = Usage(
                    input_tokens=estimate_tokens(system or "") + sum(
                        estimate_tokens(m.content) for m in ctx_messages
                    ),
                    output_tokens=estimate_tokens(text),
                    total_tokens=0,
                    estimated=True,
                )
                turn_usage.total_tokens = turn_usage.input_tokens + turn_usage.output_tokens

            local_est = int(ctx_meta.get("token_estimate") or 0)
            # Preserve raw provider numbers for inspector last_*; charge budget with
            # de-inflated billable counts so gateway token lies cannot fail real work.
            raw_in = turn_usage.input_tokens
            raw_out = turn_usage.output_tokens
            billable = billable_turn_usage(
                provider_usage=turn_usage,
                local_input_estimate=local_est,
                output_text=text,
            )
            usage.input_tokens += billable.input_tokens
            usage.output_tokens += billable.output_tokens
            usage.total_tokens += billable.total_tokens
            usage.estimated = usage.estimated or billable.estimated
            usage.last_input_tokens = raw_in
            usage.last_output_tokens = raw_out
            usage.last_local_estimate = local_est
            usage.model_turns = step + 1

            tool_calls = [tool_acc[i] for i in order if i in tool_acc]

            if usage.total_tokens > budget.max_tokens and error_msg is None:
                # Final answer already produced: complete rather than false-fail.
                if not tool_calls and (text or output_parts):
                    pass
                else:
                    error_msg = "max_tokens exceeded"
                    error_kind = "budget"
''',
    "runtime.billable_accumulate",
)

print("all core patches applied")
