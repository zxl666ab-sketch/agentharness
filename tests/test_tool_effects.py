from agentharness.contracts import ApprovalMode, EffectKind
from agentharness.security.approval import auto_decision, effect_needs_approval
from agentharness.tools.memory import MemoryStoreTool
from agentharness.tools.shell import ShellTool


def test_memory_store_declares_persistent_write_effect():
    assert MemoryStoreTool().spec.effect == EffectKind.workspace_write


def test_shell_uses_enforceable_destructive_approval_default():
    effect = ShellTool().spec.effect

    assert effect == EffectKind.destructive
    assert effect_needs_approval(effect, ApprovalMode.auto)
    assert auto_decision(effect, ApprovalMode.auto) is None
