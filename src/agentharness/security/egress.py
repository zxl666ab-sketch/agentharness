"""Shared outbound egress policy — SSRF defense for http_request, browser, MCP HTTP.

A single ``EgressPolicy`` is created by the Harness and injected into every tool that
opens an outbound connection. The policy is *default-secure*: only HTTP/HTTPS, and
every resolved A/AAAA address must be a public unicast address. Loopback, private,
link-local, reserved, multicast and unspecified ranges are rejected. Validation runs
again on every redirect hop.

Rebinding is handled *after* connect, not by pinning the socket: ``validate`` records
the address it approved in ``ValidatedTarget.pinned_ip``, and the caller checks the
actual connected peer with ``peer_ip_allowed`` before any response body is read,
failing closed when the peer cannot be determined. A DNS answer that changes between
validation and connect therefore cannot return data, though the TCP connection to the
changed address is briefly established — full socket pinning is not implemented.

Trusted hosts / CIDRs may be injected by Harness configuration (never by model
arguments) to allow, e.g., a local service in tests or an on-prem allowlist.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Resolver signature: (host, port) -> list of (family, sockaddr) like socket.getaddrinfo.
Resolver = Callable[[str, int | None], list[tuple[int, str]]]
Origin = tuple[str, str, int]

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class EgressError(PermissionError):
    """Raised when an outbound request is blocked by the egress policy."""


def _default_resolver(host: str, port: int | None) -> list[tuple[int, str]]:
    """Resolve host to (family, ip) pairs using the system resolver."""
    infos = socket.getaddrinfo(host, port or 0, proto=socket.IPPROTO_TCP)
    out: list[tuple[int, str]] = []
    seen: set[str] = set()
    for family, _type, _proto, _canon, sockaddr in infos:
        ip = str(sockaddr[0])
        if ip not in seen:
            seen.add(ip)
            out.append((int(family), ip))
    return out


def _is_public_unicast(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for globally routable unicast addresses.

    Rejects loopback, private, link-local, reserved, multicast, unspecified, and
    (for IPv6) IPv4-mapped/compat addresses whose embedded v4 is itself non-public.
    """
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    # Require a globally-routable address. This fails closed for ranges whose
    # private/reserved classification varies across Python versions (e.g. CGNAT
    # 100.64.0.0/10, RFC 6598). is_global is only defined on leaf addresses.
    if not ip.is_global:
        return False
    # IPv6 with an embedded IPv4 address (mapped/compat/6to4/teredo): validate the
    # embedded v4 too, so ::ffff:127.0.0.1 or 2002:7f00:1:: cannot bypass the check.
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or getattr(ip, "sixtofour", None) or _teredo_v4(ip)
        if mapped is not None and not _is_public_unicast(mapped):
            return False
    return True


def _teredo_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    teredo = getattr(ip, "teredo", None)
    if teredo:
        # teredo -> (server, client); client is the interesting embedded v4
        return teredo[1]
    return None


@dataclass
class EgressPolicy:
    """Validates outbound URLs and pins connections to a vetted IP.

    ``allow_hosts`` / ``allow_cidrs`` are trusted escape hatches injected by the
    Harness configuration — model tool arguments can never widen them.
    """

    allow_hosts: frozenset[str] = field(default_factory=frozenset)
    allow_cidrs: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    allow_schemes: frozenset[str] = _ALLOWED_SCHEMES
    max_redirects: int = 5
    max_response_bytes: int = 50_000
    timeout_s: float = 30.0
    resolver: Resolver = _default_resolver
    _scoped_origins: ContextVar[frozenset[Origin]] = field(
        default_factory=lambda: ContextVar(
            "agentharness_egress_scoped_origins", default=frozenset()
        ),
        repr=False,
        compare=False,
    )

    @classmethod
    def from_config(
        cls,
        *,
        allow_hosts: Sequence[str] | None = None,
        allow_cidrs: Sequence[str] | None = None,
        resolver: Resolver | None = None,
        max_redirects: int = 5,
        max_response_bytes: int = 50_000,
        timeout_s: float = 30.0,
    ) -> EgressPolicy:
        hosts = frozenset(h.strip().lower() for h in (allow_hosts or []) if h.strip())
        cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for raw in allow_cidrs or []:
            raw = raw.strip()
            if not raw:
                continue
            cidrs.append(ipaddress.ip_network(raw, strict=False))
        return cls(
            allow_hosts=hosts,
            allow_cidrs=tuple(cidrs),
            resolver=resolver or _default_resolver,
            max_redirects=max_redirects,
            max_response_bytes=max_response_bytes,
            timeout_s=timeout_s,
        )

    def _host_trusted(self, host: str) -> bool:
        return host.lower() in self.allow_hosts

    def _ip_trusted(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(ip in net for net in self.allow_cidrs)

    def _origin_trusted(self, scheme: str, host: str, port: int) -> bool:
        return (scheme.lower(), host.lower(), port) in self._scoped_origins.get()

    @contextmanager
    def allow_exact_origins(self, origins: Sequence[str]) -> Iterator[None]:
        """Temporarily allow exact origins in the current async context.

        This is intentionally narrower than the persistent host/CIDR allowlist: the
        scheme, host, and port must all match, and the permission does not leak to
        concurrent Agent runs.
        """
        normalized: set[Origin] = set()
        for raw in origins:
            parts = urlsplit(raw)
            if (
                parts.scheme.lower() not in self.allow_schemes
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
            ):
                raise EgressError(f"invalid trusted origin: {raw}")
            port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
            normalized.add((parts.scheme.lower(), parts.hostname.lower(), port))
        token = self._scoped_origins.set(
            self._scoped_origins.get() | frozenset(normalized)
        )
        try:
            yield
        finally:
            self._scoped_origins.reset(token)

    def check_scheme(self, url: str) -> tuple[str, str, int]:
        """Validate the scheme and return (scheme, host, port). Raise EgressError."""
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        if scheme not in self.allow_schemes:
            raise EgressError(f"scheme not allowed: {scheme or '(none)'}")
        host = parts.hostname
        if not host:
            raise EgressError("missing host in URL")
        port = parts.port or (443 if scheme == "https" else 80)
        return scheme, host, port

    def validate(self, url: str) -> ValidatedTarget:
        """Resolve and validate a URL. Returns a validated target or raises EgressError.

        A literal IP host is validated directly. A hostname is resolved; *every*
        returned address must be allowed (or trusted) — one bad answer fails closed.
        The first allowed address is recorded as ``pinned_ip`` for the caller to
        compare against the real connected peer via ``peer_ip_allowed``; it does not
        itself constrain which address the socket connects to.
        """
        scheme, host, port = self.check_scheme(url)
        host_trusted = self._host_trusted(host)
        origin_trusted = self._origin_trusted(scheme, host, port)

        literal = _as_ip(host)
        if literal is not None:
            if (
                not host_trusted
                and not origin_trusted
                and not self._ip_trusted(literal)
                and not _is_public_unicast(literal)
            ):
                raise EgressError(f"blocked non-public address: {host}")
            return ValidatedTarget(url=url, scheme=scheme, host=host, port=port, pinned_ip=host)

        try:
            resolved = self.resolver(host, port)
        except OSError as exc:
            raise EgressError(f"DNS resolution failed for {host}: {exc}") from exc
        if not resolved:
            raise EgressError(f"no addresses resolved for {host}")

        pinned: str | None = None
        for _family, ip_str in resolved:
            ip = _as_ip(ip_str)
            if ip is None:
                raise EgressError(f"unparseable resolved address: {ip_str}")
            allowed = (
                host_trusted
                or origin_trusted
                or self._ip_trusted(ip)
                or _is_public_unicast(ip)
            )
            if not allowed:
                raise EgressError(f"blocked non-public address for {host}: {ip_str}")
            if pinned is None:
                pinned = ip_str
        assert pinned is not None
        return ValidatedTarget(url=url, scheme=scheme, host=host, port=port, pinned_ip=pinned)

    def peer_ip_allowed(
        self,
        ip_str: str,
        host: str,
        *,
        scheme: str | None = None,
        port: int | None = None,
    ) -> bool:
        """Re-validate the *actually connected* peer IP (defense vs DNS rebinding).

        The HTTP client resolves DNS itself, independently of our resolver, so an
        attacker could hand us a public answer and the client a private one. After
        the socket connects we check the real peer address here and abort if it is
        not allowed — closing the rebinding window our own resolver cannot see.
        """
        ip = _as_ip(ip_str)
        if ip is None:
            return False
        origin_trusted = (
            scheme is not None
            and port is not None
            and self._origin_trusted(scheme, host, port)
        )
        return (
            self._host_trusted(host)
            or origin_trusted
            or self._ip_trusted(ip)
            or _is_public_unicast(ip)
        )


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    scheme: str
    host: str
    port: int
    pinned_ip: str


def _as_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    # Strip IPv6 brackets if present.
    candidate = value.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def default_policy() -> EgressPolicy:
    """A default-secure policy with no trusted allowlist."""
    return EgressPolicy()
