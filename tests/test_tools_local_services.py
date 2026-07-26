"""Offline tool paths against real local services and public Harness seams."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import tracemalloc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    BudgetConfig,
    ModelStreamItem,
    RunRequest,
    RunStatus,
    StreamItemType,
    Usage,
)
from agentharness.security.egress import EgressPolicy
from agentharness.security.redaction import Redactor
from agentharness.tools.mcp_tool import MCPBridge
from tests.fake_provider import FakeModelAdapter, create_test_harness


def _loopback_policy() -> EgressPolicy:
    """Egress policy that trusts loopback for tests hitting a local HTTP server.

    This is an explicit, test-only trusted allowlist injected via DI — the
    production default policy still blocks loopback/private ranges.
    """
    return EgressPolicy.from_config(
        allow_hosts=["127.0.0.1", "localhost"],
        allow_cidrs=["127.0.0.0/8", "::1/128"],
    )


class _LocalHandler(BaseHTTPRequestHandler):
    large_bytes_sent = 0

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/slow":
            time.sleep(0.35)
        if self.path == "/large":
            chunk = b"x" * 4096
            total = 2 * 1024 * 1024
            type(self).large_bytes_sent = 0
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(total))
            self.end_headers()
            try:
                for _ in range(total // len(chunk)):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    type(self).large_bytes_sent += len(chunk)
                    time.sleep(0.001)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        body = b"<html><title>Local Harness</title><body>LOCAL_BROWSER_MARKER</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *args: object) -> None:
        return


@pytest.fixture
def local_http_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_http_tool_uses_local_service_and_enforces_timeout(
    data_dir: Path, workspace: Path, local_http_url: str
):
    harness = create_test_harness(data_dir=data_dir, egress_policy=_loopback_policy())
    try:
        success = await harness.run(
            RunRequest(
                message="[fake:tools]http_request\n"
                + json.dumps({"url": f"{local_http_url}/ok", "timeout_s": 2}),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        timed_out = await harness.run(
            RunRequest(
                message="[fake:tools]http_request\n"
                + json.dumps({"url": f"{local_http_url}/slow", "timeout_s": 0.05}),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        success_messages = harness.get_run_messages(success.run_id)
        timeout_messages = harness.get_run_messages(timed_out.run_id)
    finally:
        await harness.aclose()

    assert success.status == RunStatus.completed
    assert any("status=200" in message.content for message in success_messages)
    assert any("LOCAL_BROWSER_MARKER" in message.content for message in success_messages)
    assert timed_out.status == RunStatus.completed
    assert any("HTTP error" in message.content for message in timeout_messages)


@pytest.mark.asyncio
async def test_http_tool_stops_reading_after_response_limit(
    data_dir: Path, workspace: Path, local_http_url: str
):
    harness = create_test_harness(data_dir=data_dir, egress_policy=_loopback_policy())
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]http_request\n"
                + json.dumps({"url": f"{local_http_url}/large", "timeout_s": 2}),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        messages = harness.get_run_messages(result.run_id)
        await asyncio.sleep(0.1)
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert any("...[artifact:" in message.content for message in messages)
    assert _LocalHandler.large_bytes_sent < 2 * 1024 * 1024


@pytest.mark.asyncio
async def test_browser_tool_real_local_flow_and_harness_cleanup(
    data_dir: Path, workspace: Path, local_http_url: str
):
    provider = FakeModelAdapter(
        script=[
            {
                "kind": "tools",
                "tools": [
                    {
                        "name": "browser",
                        "arguments": {
                            "action": "launch",
                            "context_id": "local-flow",
                            "headless": True,
                        },
                    }
                ],
            },
            {
                "kind": "tools",
                "tools": [
                    {
                        "name": "browser",
                        "arguments": {
                            "action": "goto",
                            "context_id": "local-flow",
                            "url": f"{local_http_url}/ok",
                            "timeout_s": 2,
                        },
                    }
                ],
            },
            {
                "kind": "tools",
                "tools": [
                    {
                        "name": "browser",
                        "arguments": {"action": "content", "context_id": "local-flow"},
                    }
                ],
            },
            {"kind": "text", "text": "browser flow complete"},
        ]
    )
    harness = create_test_harness(
        data_dir=data_dir, providers={"fake": provider}, egress_policy=_loopback_policy()
    )
    browser = harness.tools["browser"]
    try:
        result = await asyncio.wait_for(
            harness.run(
                RunRequest(
                    message="browse local service",
                    provider="fake",
                    approval=ApprovalMode.auto,
                    cwd=str(workspace),
                )
            ),
            timeout=20,
        )
        messages = harness.get_run_messages(result.run_id)
        assert browser._browsers
        assert all(
            result.run_id not in entry.get("run_ids", set())
            for entry in browser._browsers.values()
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert any("LOCAL_BROWSER_MARKER" in message.content for message in messages)
    assert browser._browsers == {}
    assert browser._playwright is None


@pytest.mark.asyncio
async def test_browser_goto_honors_timeout_and_cleanup(
    data_dir: Path, workspace: Path, local_http_url: str
):
    provider = FakeModelAdapter(
        script=[
            {
                "kind": "tools",
                "tools": [
                    {
                        "name": "browser",
                        "arguments": {
                            "action": "launch",
                            "context_id": "timeout-flow",
                            "headless": True,
                        },
                    }
                ],
            },
            {
                "kind": "tools",
                "tools": [
                    {
                        "name": "browser",
                        "arguments": {
                            "action": "goto",
                            "context_id": "timeout-flow",
                            "url": f"{local_http_url}/slow",
                            "timeout_s": 0.05,
                        },
                    }
                ],
            },
            {"kind": "text", "text": "timeout observed"},
        ]
    )
    harness = create_test_harness(
        data_dir=data_dir, providers={"fake": provider}, egress_policy=_loopback_policy()
    )
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            harness.run(
                RunRequest(
                    message="time out local navigation",
                    provider="fake",
                    approval=ApprovalMode.auto,
                    cwd=str(workspace),
                )
            ),
            timeout=20,
        )
        messages = harness.get_run_messages(result.run_id)
    finally:
        await harness.aclose()

    assert time.monotonic() - started < 5
    assert any("Timeout" in message.content for message in messages)


@pytest.mark.asyncio
async def test_mcp_unavailable_is_isolated_as_tool_error(data_dir: Path, workspace: Path):
    harness = create_test_harness(data_dir=data_dir)

    async def approve(_request):
        # call_tool runs arbitrary remote code → destructive → requires approval.
        return ApprovalDecision.allow_once

    harness.set_approval_callback(approve)
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]mcp\n"
                + json.dumps(
                    {"action": "call_tool", "server": "missing", "tool": "anything"}
                ),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        messages = harness.get_run_messages(result.run_id)
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert any("not connected" in message.content for message in messages)


@pytest.mark.asyncio
async def test_max_steps_limits_tool_batches(data_dir: Path, workspace: Path):
    provider = FakeModelAdapter(
        script=[
            {
                "kind": "tools",
                "tools": [{"name": "read_file", "arguments": {"path": "a.txt"}}],
            }
        ]
    )
    harness = create_test_harness(data_dir=data_dir, providers={"fake": provider})
    try:
        result = await harness.run(
            RunRequest(
                message="one batch only",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                budget=BudgetConfig(max_steps=1),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.failed
    assert result.error == "max_steps exceeded"
    assert result.steps == 1


@pytest.mark.asyncio
async def test_delegate_depth_zero_blocks_child_run(data_dir: Path, workspace: Path):
    harness = create_test_harness(data_dir=data_dir)
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]delegate\n"
                + json.dumps({"task": "[fake:text]must not start"}),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                budget=BudgetConfig(max_delegate_depth=0),
            )
        )
        messages = harness.get_run_messages(result.run_id)
        tree = harness.get_run_tree(result.run_id)
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert len(tree) == 1
    assert any("Max delegate depth 0 reached" in message.content for message in messages)


class _ConcurrentDelegateProvider:
    name = "delegate-test"

    def __init__(self) -> None:
        self.active_children = 0
        self.max_active_children = 0

    async def stream(self, request):
        last_user_index = max(
            index
            for index, message in enumerate(request.messages)
            if str(message.role) == "user"
        )
        user_text = request.messages[last_user_index].content
        current_tools = [
            message
            for message in request.messages[last_user_index + 1 :]
            if str(message.role) == "tool"
        ]
        if user_text == "parent" and not current_tools:
            for call_id, task in (("child-1", "child one"), ("child-2", "child two")):
                yield ModelStreamItem(
                    type=StreamItemType.tool_call_start,
                    tool_call_id=call_id,
                    tool_name="delegate",
                )
                yield ModelStreamItem(
                    type=StreamItemType.tool_call_end,
                    tool_call_id=call_id,
                    tool_name="delegate",
                    arguments={"task": task},
                )
        elif user_text == "parent":
            yield ModelStreamItem(type=StreamItemType.text_delta, text="parent done")
        else:
            self.active_children += 1
            self.max_active_children = max(
                self.max_active_children, self.active_children
            )
            try:
                await asyncio.sleep(0.2)
                yield ModelStreamItem(type=StreamItemType.text_delta, text=user_text)
            finally:
                self.active_children -= 1
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        )
        yield ModelStreamItem(type=StreamItemType.done)


@pytest.mark.asyncio
async def test_delegate_concurrent_children_limit_is_enforced(
    data_dir: Path, workspace: Path
):
    provider = _ConcurrentDelegateProvider()
    harness = create_test_harness(
        data_dir=data_dir, providers={"delegate-test": provider}
    )
    try:
        result = await harness.run(
            RunRequest(
                message="parent",
                provider="delegate-test",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                budget=BudgetConfig(max_concurrent_children=1),
            )
        )
        messages = harness.get_run_messages(result.run_id)
        tree = harness.get_run_tree(result.run_id)
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert provider.max_active_children == 1
    assert len(tree) == 2
    assert any(
        "Max concurrent children 1 reached" in message.content for message in messages
    )


@pytest.mark.asyncio
async def test_search_files_is_literal_and_stops_before_reading_large_tail(
    data_dir: Path, workspace: Path
):
    large = workspace / "large.txt"
    with large.open("wb") as handle:
        handle.write(b"needle\n")
        handle.truncate(16 * 1024 * 1024)
    (workspace / "regex-like.txt").write_text("axb\n", encoding="utf-8")

    harness = create_test_harness(data_dir=data_dir)
    tracemalloc.start()
    try:
        found = await harness.run(
            RunRequest(
                message="[fake:tools]search_files\n"
                + json.dumps({"query": "needle", "max_results": 1}),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        _, peak = tracemalloc.get_traced_memory()
        literal = await harness.run(
            RunRequest(
                message="[fake:tools]search_files\n"
                + json.dumps({"query": "a.b", "glob": "regex-like.txt"}),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        found_messages = harness.get_run_messages(found.run_id)
        literal_messages = harness.get_run_messages(literal.run_id)
    finally:
        tracemalloc.stop()
        await harness.aclose()

    assert any("large.txt:1:needle" in message.content for message in found_messages)
    assert peak < 8 * 1024 * 1024
    assert any("No matches" in message.content for message in literal_messages)


@pytest.mark.asyncio
async def test_read_file_limit_does_not_load_large_tail(
    data_dir: Path, workspace: Path
):
    large = workspace / "large-read.txt"
    with large.open("wb") as handle:
        handle.write(b"head\n")
        handle.truncate(16 * 1024 * 1024)

    harness = create_test_harness(data_dir=data_dir)
    tracemalloc.start()
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]read_file\n"
                + json.dumps({"path": "large-read.txt", "limit": 1}),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        _, peak = tracemalloc.get_traced_memory()
        messages = harness.get_run_messages(result.run_id)
    finally:
        tracemalloc.stop()
        await harness.aclose()

    assert result.status == RunStatus.completed
    # The requested prefix remains verbatim; the model-visible suffix carries the
    # whole-file version needed for optimistic writes.
    assert any(
        message.content.startswith("head\n[agentharness:file_version sha256=")
        for message in messages
    )
    assert peak < 8 * 1024 * 1024


@pytest.mark.asyncio
async def test_shell_does_not_inherit_agentharness_secret_environment(
    data_dir: Path, workspace: Path, monkeypatch
):
    monkeypatch.setenv("AGENTHARNESS_SECRET", "never-forward-this-value")
    harness = create_test_harness(data_dir=data_dir)

    async def approve(_request):
        from agentharness.contracts import ApprovalDecision

        return ApprovalDecision.allow_once

    harness.set_approval_callback(approve)
    python = sys.executable.replace("\\", "/")
    command = (
        f'"{python}" -c "import os; '
        "print('present' if os.environ.get('AGENTHARNESS_SECRET') else 'absent')\""
    )
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]shell\n"
                + json.dumps({"command": command, "timeout_s": 5}),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        messages = harness.get_run_messages(result.run_id)
    finally:
        await harness.aclose()

    tool_outputs = [message.content for message in messages if message.role == "tool"]
    assert any("absent" in output for output in tool_outputs)
    assert all("present" not in output for output in tool_outputs)


@pytest.mark.asyncio
async def test_shell_output_limit_is_memory_bounded(data_dir: Path, workspace: Path):
    harness = create_test_harness(data_dir=data_dir)

    async def approve(_request):
        from agentharness.contracts import ApprovalDecision

        return ApprovalDecision.allow_once

    harness.set_approval_callback(approve)
    python = sys.executable.replace("\\", "/")
    command = f'"{python}" -c "import sys;sys.stdout.write(\'x\'*(8*1024*1024))"'
    tracemalloc.start()
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]shell\n"
                + json.dumps({"command": command, "timeout_s": 15}),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        _, peak = tracemalloc.get_traced_memory()
        messages = harness.get_run_messages(result.run_id)
    finally:
        tracemalloc.stop()
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert any("...[artifact:" in message.content for message in messages)
    assert peak < 4 * 1024 * 1024


@pytest.mark.asyncio
async def test_mcp_errors_and_logs_use_harness_redactor(caplog):
    secret = "SECRET_MCP_LOG_SENTINEL_77889"

    class RaisingSession:
        async def call_tool(self, tool, arguments):
            raise RuntimeError(f"connection failed with {secret}")

    bridge = MCPBridge(redactor=Redactor(extra_sentinels=[secret]))
    bridge._sessions["local"] = {"session": RaisingSession()}

    output = await bridge.call_tool("local", "failing", {})

    assert secret not in output
    assert secret not in caplog.text
    assert "REDACTED" in output
