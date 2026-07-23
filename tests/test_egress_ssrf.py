"""SSRF regression tests for the shared EgressPolicy.

Covers literal IPv4/IPv6, hostname resolution (all A/AAAA must pass), DNS-rebinding
via peer verification, redirect re-validation, and the trusted-host/CIDR allowlist.
The resolver is injected so no real DNS is performed and no network is touched.
"""

from __future__ import annotations

import pytest

from agentharness.security.egress import (
    EgressError,
    EgressPolicy,
    default_policy,
)


def _static_resolver(mapping: dict[str, list[str]]):
    """Return a resolver that maps host -> list of IPs (family inferred)."""

    def resolver(host: str, port: int | None):  # noqa: ARG001
        ips = mapping.get(host)
        if ips is None:
            raise OSError(f"no such host: {host}")
        out: list[tuple[int, str]] = []
        for ip in ips:
            family = 10 if ":" in ip else 2  # AF_INET6=10, AF_INET=2 (value unused)
            out.append((family, ip))
        return out

    return resolver


# -- scheme allowlist -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "gopher://example.com",
        "data:text/plain,hi",
        "//example.com/x",  # missing scheme
    ],
)
def test_non_http_schemes_are_blocked(url: str):
    policy = EgressPolicy.from_config(resolver=_static_resolver({}))
    with pytest.raises(EgressError):
        policy.validate(url)


def test_missing_host_is_blocked():
    policy = default_policy()
    with pytest.raises(EgressError):
        policy.validate("http:///nohost")


# -- literal private / loopback IPv4 ---------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.5.5.5",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata link-local
        "0.0.0.0",
        "100.64.0.1",  # CGNAT / shared (reserved)
    ],
)
def test_literal_private_ipv4_blocked(host: str):
    policy = EgressPolicy.from_config(resolver=_static_resolver({}))
    with pytest.raises(EgressError):
        policy.validate(f"http://{host}/")


def test_literal_public_ipv4_allowed():
    policy = EgressPolicy.from_config(resolver=_static_resolver({}))
    target = policy.validate("http://93.184.216.34/")
    assert target.pinned_ip == "93.184.216.34"


# -- literal IPv6 (loopback, mapped, 6to4) ---------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "[::1]",  # loopback
        "[::ffff:127.0.0.1]",  # IPv4-mapped loopback
        "[::ffff:10.0.0.1]",  # IPv4-mapped private
        "[fe80::1]",  # link-local
        "[fc00::1]",  # unique-local (private)
        "[2002:7f00:0001::]",  # 6to4 embedding 127.0.0.1
        "[::]",  # unspecified
    ],
)
def test_literal_private_ipv6_blocked(host: str):
    policy = EgressPolicy.from_config(resolver=_static_resolver({}))
    with pytest.raises(EgressError):
        policy.validate(f"http://{host}/")


def test_literal_public_ipv6_allowed():
    policy = EgressPolicy.from_config(resolver=_static_resolver({}))
    target = policy.validate("http://[2606:2800:220:1:248:1893:25c8:1946]/")
    assert ":" in target.pinned_ip


# -- hostname resolution: every resolved address must pass ------------------


def test_hostname_all_public_allowed():
    resolver = _static_resolver({"good.example": ["93.184.216.34", "93.184.216.35"]})
    policy = EgressPolicy.from_config(resolver=resolver)
    target = policy.validate("http://good.example/path")
    # First allowed address is pinned for the connection.
    assert target.pinned_ip == "93.184.216.34"
    assert target.host == "good.example"


def test_hostname_with_one_private_answer_fails_closed():
    # A single private answer among public ones must reject the whole hostname.
    resolver = _static_resolver(
        {"rebind.example": ["93.184.216.34", "127.0.0.1"]}
    )
    policy = EgressPolicy.from_config(resolver=resolver)
    with pytest.raises(EgressError):
        policy.validate("http://rebind.example/")


def test_hostname_resolving_only_private_blocked():
    resolver = _static_resolver({"internal.example": ["10.1.2.3"]})
    policy = EgressPolicy.from_config(resolver=resolver)
    with pytest.raises(EgressError):
        policy.validate("http://internal.example/")


def test_hostname_dns_failure_blocked():
    policy = EgressPolicy.from_config(resolver=_static_resolver({}))
    with pytest.raises(EgressError):
        policy.validate("http://nonexistent.example/")


# -- trusted host / CIDR allowlist (injected config, never model args) ------


def test_trusted_host_allows_loopback():
    resolver = _static_resolver({"localhost": ["127.0.0.1"]})
    policy = EgressPolicy.from_config(
        allow_hosts=["localhost"], resolver=resolver
    )
    target = policy.validate("http://localhost:8080/")
    assert target.pinned_ip == "127.0.0.1"


def test_trusted_cidr_allows_literal_private_ip():
    policy = EgressPolicy.from_config(allow_cidrs=["127.0.0.0/8"])
    target = policy.validate("http://127.0.0.1/")
    assert target.pinned_ip == "127.0.0.1"


def test_untrusted_host_still_blocked_when_allowlist_present():
    resolver = _static_resolver({"other.example": ["10.0.0.5"]})
    policy = EgressPolicy.from_config(
        allow_hosts=["localhost"], resolver=resolver
    )
    with pytest.raises(EgressError):
        policy.validate("http://other.example/")


def test_default_policy_has_empty_allowlist():
    policy = default_policy()
    assert policy.allow_hosts == frozenset()
    assert policy.allow_cidrs == ()


# -- peer verification (post-connect DNS-rebinding guard) -------------------


def test_peer_ip_allowed_rejects_private():
    policy = default_policy()
    assert policy.peer_ip_allowed("93.184.216.34", "good.example") is True
    assert policy.peer_ip_allowed("127.0.0.1", "good.example") is False
    assert policy.peer_ip_allowed("10.0.0.1", "good.example") is False


def test_peer_ip_allowed_honors_trusted_host():
    policy = EgressPolicy.from_config(allow_hosts=["localhost"])
    # The connected peer is loopback but the host is trusted → allowed.
    assert policy.peer_ip_allowed("127.0.0.1", "localhost") is True
    # A different host with the same private peer is still rejected.
    assert policy.peer_ip_allowed("127.0.0.1", "evil.example") is False


def test_peer_ip_allowed_rejects_unparseable():
    policy = default_policy()
    assert policy.peer_ip_allowed("not-an-ip", "good.example") is False


# -- redirect re-validation -------------------------------------------------


def test_redirect_target_is_revalidated():
    # Same policy.validate() is invoked per hop by HttpTool; a redirect to a
    # private host must be rejected exactly like a direct request.
    resolver = _static_resolver({"good.example": ["93.184.216.34"]})
    policy = EgressPolicy.from_config(resolver=resolver)
    # Direct public host validates.
    assert policy.validate("http://good.example/").pinned_ip == "93.184.216.34"
    # A redirect Location pointing at metadata IP fails on re-validation.
    with pytest.raises(EgressError):
        policy.validate("http://169.254.169.254/latest/meta-data/")


# -- tool integration: http_request, browser, MCP all share the policy -----


@pytest.mark.asyncio
async def test_http_tool_blocks_private_url():
    from agentharness.contracts import ToolContext
    from agentharness.tools.http_tool import HttpTool

    tool = HttpTool(policy=default_policy())
    ctx = ToolContext(run_id="r", session_id="s", cwd=".", data_dir=".")
    result = await tool.run(ctx, {"url": "http://169.254.169.254/latest/meta-data/"})
    assert result.is_error
    assert "blocked by egress policy" in result.content


@pytest.mark.asyncio
async def test_browser_goto_blocks_private_url(data_dir, workspace):
    from agentharness.contracts import ToolContext
    from agentharness.tools.browser import BrowserTool

    tool = BrowserTool(policy=default_policy())
    # Pre-seed a fake page so _goto reaches URL validation without launching.
    tool._browsers["default"] = {"page": object(), "run_ids": {"r"}}
    ctx = ToolContext(
        run_id="r", session_id="s", cwd=str(workspace), data_dir=str(data_dir)
    )
    result = await tool.run(
        ctx, {"action": "goto", "context_id": "default", "url": "http://10.0.0.1/"}
    )
    assert result.is_error
    assert "blocked by egress policy" in result.content


@pytest.mark.asyncio
async def test_mcp_connect_http_blocks_private_url():
    from agentharness.tools.mcp_tool import MCPBridge

    bridge = MCPBridge(policy=default_policy())
    msg = await bridge.connect_http("evil", "http://127.0.0.1:9/")
    assert "blocked by egress policy" in msg


# -- MCP dynamic effects + stdio env whitelist ------------------------------


def test_mcp_effect_for_is_dynamic():
    from agentharness.contracts import EffectKind
    from agentharness.tools.mcp_tool import MCPTool, mcp_effect_for

    assert mcp_effect_for({"action": "list_tools"}) == EffectKind.pure
    assert mcp_effect_for({"action": "connect_http"}) == EffectKind.network
    assert mcp_effect_for({"action": "connect_stdio"}) == EffectKind.destructive
    assert mcp_effect_for({"action": "call_tool"}) == EffectKind.destructive
    assert mcp_effect_for({"action": "unknown"}) == EffectKind.destructive
    # The tool spec's static effect stays destructive (upper bound).
    assert MCPTool().spec.effect == EffectKind.destructive
    assert MCPTool().effect_for({"action": "list_tools"}) == EffectKind.pure


def test_mcp_env_whitelist_drops_secrets(monkeypatch):
    from agentharness.tools.mcp_tool import mcp_env_whitelist

    monkeypatch.setenv("AGENTHARNESS_SECRET_KEY", "super-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = mcp_env_whitelist()
    assert "AGENTHARNESS_SECRET_KEY" not in env
    assert "PATH" in env
