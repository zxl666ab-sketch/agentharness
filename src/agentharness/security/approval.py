"""Approval policy for tool effects."""

from __future__ import annotations

from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    EffectKind,
    ToolSpec,
)


def effect_needs_approval(effect: EffectKind, mode: ApprovalMode) -> bool:
    """Return whether this effect requires an interactive approval decision."""
    if effect in (EffectKind.pure, EffectKind.workspace_read):
        return False
    if effect == EffectKind.destructive:
        # Always requires confirmation — even under auto.
        return True
    if mode == ApprovalMode.auto:
        return False
    if mode == ApprovalMode.never:
        return True  # will be auto-denied
    return True  # ask


def auto_decision(effect: EffectKind, mode: ApprovalMode) -> ApprovalDecision | None:
    """Return a decision without prompting, or None if interactive prompt needed."""
    if effect in (EffectKind.pure, EffectKind.workspace_read):
        return ApprovalDecision.allow_once
    if effect == EffectKind.destructive:
        if mode == ApprovalMode.never:
            return ApprovalDecision.deny
        return None  # always interactive
    if mode == ApprovalMode.auto:
        return ApprovalDecision.allow_run
    if mode == ApprovalMode.never:
        return ApprovalDecision.deny
    return None  # ask


def should_gate(spec: ToolSpec, mode: ApprovalMode) -> bool:
    return effect_needs_approval(spec.effect, mode)
