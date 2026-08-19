from __future__ import annotations

import pytest

from agentharness.contracts import ApprovalDecision, ApprovalMode, EffectKind, ToolSpec
from agentharness.security.approval import auto_decision, effect_needs_approval, should_gate


@pytest.mark.parametrize(
    ("effect", "mode", "expected"),
    [
        (EffectKind.pure, ApprovalMode.ask, False),
        (EffectKind.workspace_read, ApprovalMode.never, False),
        (EffectKind.destructive, ApprovalMode.auto, True),
        (EffectKind.workspace_write, ApprovalMode.auto, False),
        (EffectKind.network, ApprovalMode.never, True),
        (EffectKind.process, ApprovalMode.ask, True),
    ],
)
def test_effect_needs_approval_respects_effect_and_mode(
    effect: EffectKind, mode: ApprovalMode, expected: bool
) -> None:
    assert effect_needs_approval(effect, mode) is expected


@pytest.mark.parametrize(
    ("effect", "mode", "expected"),
    [
        (EffectKind.pure, ApprovalMode.ask, ApprovalDecision.allow_once),
        (EffectKind.workspace_read, ApprovalMode.never, ApprovalDecision.allow_once),
        (EffectKind.destructive, ApprovalMode.auto, None),
        (EffectKind.destructive, ApprovalMode.never, ApprovalDecision.deny),
        (EffectKind.workspace_write, ApprovalMode.auto, ApprovalDecision.allow_run),
        (EffectKind.network, ApprovalMode.never, ApprovalDecision.deny),
        (EffectKind.process, ApprovalMode.ask, None),
    ],
)
def test_auto_decision_only_skips_safe_or_explicitly_automatic_effects(
    effect: EffectKind, mode: ApprovalMode, expected: ApprovalDecision | None
) -> None:
    assert auto_decision(effect, mode) == expected


def test_should_gate_uses_the_tool_effect() -> None:
    safe = ToolSpec(name="read", description="Read", effect=EffectKind.workspace_read)
    write = ToolSpec(name="write", description="Write", effect=EffectKind.workspace_write)

    assert should_gate(safe, ApprovalMode.ask) is False
    assert should_gate(write, ApprovalMode.ask) is True
