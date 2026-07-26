"""http_request: fail-closed peer verification and no model-chosen credentials."""

from __future__ import annotations

import httpx
import pytest

from agentharness.contracts import ToolContext
from agentharness.security.egress import EgressError, EgressPolicy
from agentharness.tools.http_tool import HttpTool, _strip_credential_headers


def _public_policy() -> EgressPolicy:
    # Resolve every hostname to a public address so the test exercises the peer
    # check rather than the resolver guard.
    return EgressPolicy(resolver=lambda host, port: [(2, "93.184.216.34")])  # noqa: ARG005


def _ctx() -> ToolContext:
    return ToolContext(run_id="r", session_id="s", cwd=".", data_dir=".")


class _NoPeerStream:
    def get_extra_info(self, name: str) -> None:  # noqa: ARG002
        return None


class _RaisingStream:
    def get_extra_info(self, name: str) -> None:  # noqa: ARG002
        raise RuntimeError("stream detached")


class _PeerStream:
    def __init__(self, ip: str) -> None:
        self._ip = ip

    def get_extra_info(self, name: str):  # noqa: ARG002
        return (self._ip, 443)


def _response(extensions: dict) -> httpx.Response:
    return httpx.Response(200, text="ok", extensions=extensions)


def test_verify_peer_fails_closed_without_network_stream():
    tool = HttpTool(policy=_public_policy())
    target = tool.policy.validate("https://example.com/")
    with pytest.raises(EgressError):
        tool._verify_peer(_response({}), target)


def test_verify_peer_fails_closed_when_peer_is_none():
    tool = HttpTool(policy=_public_policy())
    target = tool.policy.validate("https://example.com/")
    with pytest.raises(EgressError):
        tool._verify_peer(_response({"network_stream": _NoPeerStream()}), target)


def test_verify_peer_fails_closed_when_lookup_raises():
    tool = HttpTool(policy=_public_policy())
    target = tool.policy.validate("https://example.com/")
    with pytest.raises(EgressError):
        tool._verify_peer(_response({"network_stream": _RaisingStream()}), target)


def test_verify_peer_rejects_rebound_private_peer():
    tool = HttpTool(policy=_public_policy())
    target = tool.policy.validate("https://example.com/")
    with pytest.raises(EgressError):
        tool._verify_peer(
            _response({"network_stream": _PeerStream("127.0.0.1")}), target
        )


def test_verify_peer_accepts_validated_public_peer():
    tool = HttpTool(policy=_public_policy())
    target = tool.policy.validate("https://example.com/")
    tool._verify_peer(
        _response({"network_stream": _PeerStream("93.184.216.34")}), target
    )


@pytest.mark.parametrize(
    "header",
    [
        "Authorization",
        "authorization",
        "Proxy-Authorization",
        "Cookie",
        "X-Api-Key",
        "X-Auth-Token",
        "X-Amz-Security-Token",
        "Metadata-Flavor",
    ],
)
def test_credential_headers_are_stripped(header: str):
    cleaned = _strip_credential_headers({header: "secret-value", "Accept": "text/html"})
    assert header not in cleaned
    assert "secret-value" not in "".join(cleaned.values())
    assert cleaned["Accept"] == "text/html"


def test_ordinary_headers_survive():
    cleaned = _strip_credential_headers(
        {"Accept": "application/json", "User-Agent": "agentharness", "X-Trace": "1"}
    )
    assert cleaned == {
        "Accept": "application/json",
        "User-Agent": "agentharness",
        "X-Trace": "1",
    }


def test_non_dict_headers_are_ignored():
    assert _strip_credential_headers("Authorization: leak") == {}  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_reports_peer_verification_failure_as_blocked(monkeypatch):
    """An unverifiable peer surfaces as an egress block, not a silent success."""
    tool = HttpTool(policy=_public_policy())

    async def fake_send(self, request, **kwargs):  # noqa: ANN001, ARG001
        return httpx.Response(200, text="body", extensions={}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    result = await tool.run(_ctx(), {"url": "https://example.com/"})

    assert result.is_error
    assert "blocked by egress policy" in result.content
    assert "body" not in result.content


@pytest.mark.asyncio
async def test_write_transport_failure_is_not_declared_retryable(monkeypatch):
    tool = HttpTool(policy=_public_policy())

    async def fail(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("connection lost after send")

    monkeypatch.setattr(tool, "_request_with_redirects", fail)
    result = await tool.run(
        _ctx(),
        {"url": "https://example.com/update", "method": "POST", "body": "value"},
    )

    assert result.is_error is True
    assert result.error_code == "outcome_indeterminate"
    assert result.error_category == "recovery"
    assert result.retryable is False
    assert "Inspect the remote outcome" in (result.recovery_hint or "")
