"""Unified redaction sink — all persist / log / SSE / HTML paths go through here."""

from __future__ import annotations

import re
from typing import Any

# Patterns that look like secrets / credentials
_DEFAULT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)([^\s,]{8,})"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)(api[_-]?key|secret|token|password|passwd|authorization|bearer)"
            r"\s*[=:]\s*['\"]?([^\s'\"\\,]{8,})['\"]?"
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (
        re.compile(
            r"(?i)(-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
            r"[\s\S]*?"
            r"(-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
]

_PUBLIC_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/][^\r\n\s\"'<>|]+"),
    re.compile(
        r"(?<![A-Za-z0-9_])/(?:home|Users|root|tmp|etc|var|app|data|opt|usr|srv)"
        r"/[^\r\n\s\"'<>]+"
    ),
]

_SENSITIVE_KEYS = {
    "authorization",
    "proxyauthorization",
    "apikey",
    "xapikey",
    "token",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "password",
    "passwd",
    "secret",
    "clientsecret",
    "privatekey",
    "secretaccesskey",
    "cookie",
    "setcookie",
}

# Substring words: a normalized key containing any of these is treated as
# sensitive. "token" is matched separately (see _is_sensitive_key) so that
# plural token counters such as max_tokens / total_tokens are not redacted.
_SENSITIVE_KEY_WORDS = (
    "apikey",
    "password",
    "passwd",
    "authorization",
    "credential",
    "privatekey",
    "secret",
)


def _is_sensitive_key(normalized_key: str) -> bool:
    """Exact set first, then substring matching on credential words.

    ``token`` needs special handling: only keys that *end* with "token"
    (github_token, access_token, auth_token, ...) carry a credential value.
    Structural counters such as max_tokens / total_tokens / token_estimate /
    token_method / token_budget must not be redacted or the context manifest
    and usage accounting break.
    """
    if normalized_key in _SENSITIVE_KEYS:
        return True
    if normalized_key.endswith("token"):
        return True
    return any(word in normalized_key for word in _SENSITIVE_KEY_WORDS)


class Redactor:
    """Thread-safe-ish redactor with optional injected sentinel secrets."""

    def __init__(self, extra_sentinels: list[str] | None = None) -> None:
        self._sentinels: list[str] = list(extra_sentinels or [])
        self._patterns = list(_DEFAULT_PATTERNS)

    def add_sentinel(self, value: str) -> None:
        if value and value not in self._sentinels:
            self._sentinels.append(value)

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        out = text
        for sentinel in self._sentinels:
            if sentinel and sentinel in out:
                out = out.replace(sentinel, "[REDACTED_SENTINEL]")
        for pattern, repl in self._patterns:
            out = pattern.sub(repl, out)
        return out

    def redact_obj(self, obj: Any) -> Any:
        return self._redact_obj(obj, public=False)

    def redact_public_text(self, text: str) -> str:
        """Redact secrets plus identifying absolute paths at public API boundaries."""
        out = self.redact_text(text)
        for pattern in _PUBLIC_PATH_PATTERNS:
            out = pattern.sub("[REDACTED_PATH]", out)
        return out

    def redact_public_obj(self, obj: Any) -> Any:
        return self._redact_obj(obj, public=True)

    def _redact_obj(self, obj: Any, *, public: bool) -> Any:
        if obj is None:
            return None
        if isinstance(obj, str):
            return self.redact_public_text(obj) if public else self.redact_text(obj)
        if isinstance(obj, (bytes, bytearray, memoryview)):
            text = bytes(obj).decode("utf-8", errors="replace")
            return self.redact_public_text(text) if public else self.redact_text(text)
        if isinstance(obj, dict):
            redacted: dict[Any, Any] = {}
            for key, value in obj.items():
                safe_key = self._redact_obj(key, public=public)
                if isinstance(safe_key, (dict, list, set, tuple)):
                    safe_key = str(safe_key)
                normalized_key = (
                    re.sub(r"[^a-z0-9]", "", key.lower())
                    if isinstance(key, str)
                    else ""
                )
                redacted[safe_key] = (
                    "[REDACTED]"
                    if _is_sensitive_key(normalized_key)
                    else self._redact_obj(value, public=public)
                )
            return redacted
        if isinstance(obj, list):
            return [self._redact_obj(v, public=public) for v in obj]
        if isinstance(obj, tuple):
            return tuple(self._redact_obj(v, public=public) for v in obj)
        if isinstance(obj, (set, frozenset)):
            return [self._redact_obj(v, public=public) for v in obj]
        return obj


# Process-wide default redactor; tests may inject sentinels.
default_redactor = Redactor()
