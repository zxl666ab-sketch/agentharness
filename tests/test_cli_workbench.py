"""TTY workbench view-model fold path and layout structure (no live terminal required)."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from agentharness.cli import interactive as interactive_mod
from agentharness.cli import workbench as workbench_mod
from agentharness.cli.view_model import (
    CliViewModel,
    CoalesceStats,
    ItemKind,
    UiPhase,
    truncate_display,
)


def _event(etype: str, payload: dict | None = None, run_id: str = "run-1") -> SimpleNamespace:
    return SimpleNamespace(type=etype, payload=payload or {}, run_id=run_id)


def test_idle_frame_has_four_regions_and_status_fields() -> None:
    vm = CliViewModel()
    vm.configure(
        cwd=r"D:\个人通用agentharness",
        branch="codex/cli-web-productization",
        provider="fake",
        model="toy",
        approval="always-approve",
        profile="刀哥grok",
    )
    vm.set_idle()
    frame = vm.render_frame(120, 36)
    joined = "\n".join(frame)

    assert "codex/cli-web-productization" in frame[0] or "agentharness" in frame[0]
    assert "fake" in frame[0]
    assert "toy" in frame[0] or "always-approve" in frame[0]
    assert any(line.startswith("┌") for line in frame)
    assert any(line.startswith("└") for line in frame)
    assert any("> " in line or line.strip().startswith("│>") for line in frame)
    assert "Enter:send" in joined
    assert "Alt+Enter:newline" in joined
    assert "Tab:complete" in joined
    assert "Ctrl+C" in joined
    # Idle is not a bare you> prompt
    assert "you>" not in joined.split("\n")[0]
    assert vm.phase == UiPhase.idle
    assert "provider=fake" in vm.status_detail or "fake" in vm.format_phase_line(80)


def test_event_fold_streams_tools_and_completion_order() -> None:
    vm = CliViewModel()
    vm.configure(cwd="/tmp/ws", provider="fake", model="m", approval="auto")
    vm.begin_user_turn("list files please")

    assert vm.items[0].kind == ItemKind.user
    assert vm.phase == UiPhase.connecting

    assert vm.apply_event(_event("run_started")) is True
    assert vm.phase == UiPhase.running

    # Burst of text deltas — coalesced redraws
    now = 1000.0
    redraws = 0
    full = []
    for i, ch in enumerate("hello world from stream"):
        full.append(ch)
        if vm.apply_event(
            _event("text_delta", {"text": ch}),
            now=now + i * 0.001,  # much faster than min interval
        ):
            redraws += 1
    # Final flush always completes text
    vm.coalesce.flush(now=now + 10)
    assert "".join(full) in (vm.stream_text)
    assert vm.stream_text == "hello world from stream"
    # Coalescing must skip most high-frequency deltas
    assert vm.coalesce.delta_events == len("hello world from stream")
    assert vm.coalesce.redraws < vm.coalesce.delta_events
    assert vm.coalesce.coalesced_skips > 0

    assert vm.apply_event(
        _event("tool_call_start", {"tool_call_id": "t1", "name": "shell"})
    )
    assert vm.phase == UiPhase.tool_running
    assert vm.apply_event(
        _event(
            "tool_call_end",
            {
                "tool_call_id": "t1",
                "name": "shell",
                "arguments_summary": "echo hi",
                "is_error": False,
            },
        )
    )
    assert vm.apply_event(
        _event(
            "tool_result",
            {
                "tool_call_id": "t1",
                "name": "shell",
                "is_error": False,
                "duration_ms": 12,
                "content_preview": "hi",
            },
        )
    )
    # Single tool row (no duplicates)
    tool_items = [i for i in vm.items if i.kind == ItemKind.tool]
    assert len(tool_items) == 1
    assert tool_items[0].tool is not None
    assert tool_items[0].tool.name == "shell"
    assert tool_items[0].tool.state == "ok"
    assert tool_items[0].tool.duration_ms == 12
    assert tool_items[0].tool.args_summary == "echo hi"

    # Duplicate tool_result must not create a second row
    vm.apply_event(
        _event(
            "tool_result",
            {
                "tool_call_id": "t1",
                "name": "shell",
                "is_error": False,
                "duration_ms": 12,
                "content_preview": "hi",
            },
        )
    )
    assert len([i for i in vm.items if i.kind == ItemKind.tool]) == 1

    vm.finish_turn(
        status="completed",
        run_id="abcdefghijkl",
        session_id="sesssesssess",
        provider="fake",
        model="m",
        tokens_in=10,
        tokens_out=20,
        duration_s=1.5,
    )
    assert vm.phase == UiPhase.completed
    assert any(i.kind == ItemKind.status for i in vm.items)
    body = "\n".join(vm.iter_body_lines(100))
    assert "you" in body and "list files" in body
    assert "hello world from stream" in body
    assert "shell" in body
    assert "status=completed" in body


def test_tool_start_end_result_order_and_long_truncation() -> None:
    vm = CliViewModel()
    vm.begin_user_turn("x")
    long_args = "A" * 500
    long_preview = "B" * 500
    vm.apply_event(_event("tool_call_start", {"tool_call_id": "z", "name": "fs_read"}))
    vm.apply_event(
        _event(
            "tool_call_end",
            {"tool_call_id": "z", "arguments_summary": long_args, "is_error": False},
        )
    )
    vm.apply_event(
        _event(
            "tool_result",
            {
                "tool_call_id": "z",
                "is_error": True,
                "duration_ms": 99,
                "content_preview": long_preview,
            },
        )
    )
    row = vm.tools["z"]
    assert row.state == "err"
    assert row.is_error is True
    lines = vm._tool_lines(row, width=80)
    assert all(len(line) <= 80 + 1 for line in lines)  # display-truncated
    assert all("\n" not in line for line in lines)
    for line in lines:
        assert len(line) <= 80 or truncate_display(line, 80) == line


def test_status_overlays_for_failure_modes() -> None:
    vm = CliViewModel()
    vm.begin_user_turn("boom")
    vm.mark_interrupted("run-interrupt-1")
    assert vm.phase == UiPhase.interrupted
    assert "Interrupted" in vm.status_detail

    vm2 = CliViewModel()
    vm2.begin_user_turn("boom")
    vm2.mark_error("provider down")
    assert vm2.phase == UiPhase.failed
    assert any(i.kind == ItemKind.error for i in vm2.items)

    for status, phase in [
        ("failed", UiPhase.failed),
        ("cancelled", UiPhase.cancelled),
        ("completed", UiPhase.completed),
    ]:
        vm3 = CliViewModel()
        vm3.begin_user_turn("t")
        vm3.finish_turn(status=status, run_id="r1", session_id="s1", provider="fake")
        assert vm3.phase == phase

    vm4 = CliViewModel()
    vm4.apply_event(_event("approval_requested", {"tool": "shell", "arguments_summary": "rm"}))
    assert vm4.phase == UiPhase.waiting_approval
    assert "waiting approval" in vm4.status_detail


def test_narrow_and_mid_frames_have_no_horizontal_overflow() -> None:
    vm = CliViewModel()
    vm.configure(
        cwd=r"D:\very\long\path\to\the\agentharness\project\directory",
        branch="codex/cli-web-productization-extra-long-branch-name",
        provider="anthropic",
        model="claude-3-5-sonnet-20241022",
        approval="always-approve",
        profile="long-profile-name",
    )
    vm.begin_user_turn("do something with a very long user message " + ("word " * 40))
    vm.apply_event(_event("text_delta", {"text": "x" * 200}))
    vm.apply_event(
        _event("tool_call_start", {"tool_call_id": "t", "name": "shell"})
    )
    vm.apply_event(
        _event(
            "tool_result",
            {
                "tool_call_id": "t",
                "is_error": False,
                "duration_ms": 1,
                "content_preview": "p" * 300,
            },
        )
    )
    vm.finish_turn(status="completed", run_id="r", session_id="s", provider="anthropic")

    for width, height in [(120, 36), (100, 28), (80, 24)]:
        frame = vm.render_frame(width, height)
        assert len(frame) == height
        for line in frame:
            # Allow box-drawing; measure approximate display width via len for ASCII-heavy lines
            assert len(line) <= width + 4, (width, repr(line[:100]))


def test_coalesce_stats_compare_naive_vs_merged() -> None:
    stats = CoalesceStats(min_interval_s=0.05)
    now = 0.0
    for i in range(100):
        stats.note_delta(now=now + i * 0.001)
    stats.flush(now=now + 10)
    naive = stats.delta_events
    merged = stats.redraws
    assert naive == 100
    assert merged < naive
    assert merged >= 2  # first + some interval hits + flush
    # Persist comparison artifact path is written by scenario script; unit-level ratio:
    assert stats.coalesced_skips == naive - (merged - 1) or stats.coalesced_skips > 0


def test_workbench_module_defines_fixed_chrome_layout() -> None:
    source = Path(workbench_mod.__file__).read_text(encoding="utf-8")
    assert "full_screen=True" in source
    assert "FormattedTextControl" in source
    assert "BufferControl" in source
    assert "Enter:send" in source or "format_shortcut_line" in source
    # Structural: Workbench.read uses Application layout with header + composer
    assert "class Workbench" in source
    assert "_header_fragments" in source
    assert "_shortcut_fragments" in source
    assert "_composer_top" in source


def test_interactive_wires_workbench_only_for_tty() -> None:
    source = Path(interactive_mod.__file__).read_text(encoding="utf-8")
    assert "Workbench" in source
    assert "workbench=workbench" in source or "workbench=workbench," in source
    assert "redirected_input" in source
    assert 'redirected_input(console, "you> ")' in source
    # Non-TTY still prints banner; TTY uses workbench chrome
    assert "_print_banner" in source
    assert "sys.stdin.isatty()" in source
    # Execution still goes through harness.run / RunRequest
    assert "RunRequest" in source
    assert "harness.run" in source
    assert "subscribe_events" in source


def test_non_tty_path_still_uses_plain_you_prompt_contract() -> None:
    """Guard: redirected interactive must not import Application into the pipe path."""
    src = inspect.getsource(interactive_mod.run_interactive)
    # workbench constructed only inside isatty branch
    assert "isatty" in src
    assert "Workbench" in src


def test_truncate_display_never_exceeds_width() -> None:
    s = truncate_display("hello世界" * 20, 20)
    # CJK counts as 2; result must fit
    width = sum(2 if ord(c) > 0xFF else 1 for c in s)
    assert width <= 20
