"""OpenAI provider adapter: Responses API + Chat Completions (OpenAI-compatible).

Official OpenAI defaults to the Responses API. Custom ``base_url`` gateways
(ModelScope, vLLM, OneAPI, etc.) usually only expose Chat Completions and are
selected automatically. Override with ``OPENAI_API_MODE=chat|responses``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from agentharness.contracts import (
    ModelRequest,
    ModelStreamItem,
    StreamItemType,
    Usage,
)

ApiMode = Literal["auto", "chat", "responses"]


def normalize_openai_base_url(base_url: str | None) -> str | None:
    """Accept a provider root while preserving an explicitly supplied API path."""

    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.netloc and parsed.path in {"", "/"}:
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1", "", ""))
    return raw


class _ProviderProtocolError(ValueError):
    pass


@dataclass(slots=True)
class _ToolCallState:
    key: str
    call_id: str | None = None
    name: str = ""
    argument_parts: list[str] = field(default_factory=list)
    emitted_parts: int = 0
    final_arguments: str | None = None
    started: bool = False
    ended: bool = False


class _ToolCallAccumulator:
    """Normalize provider fragments without exposing provisional call ids."""

    def __init__(self) -> None:
        self._states: dict[str, _ToolCallState] = {}
        self._call_owners: dict[str, str] = {}

    def observe(
        self,
        key: str,
        *,
        call_id: str | None = None,
        name: str | None = None,
        delta: str | None = None,
    ) -> list[ModelStreamItem]:
        state = self._states.setdefault(key, _ToolCallState(key=key))
        if state.ended and delta:
            raise _ProviderProtocolError("received arguments after tool call completed")
        if call_id:
            normalized_id = str(call_id).strip()
            if not normalized_id:
                raise _ProviderProtocolError("tool call id is empty")
            if state.call_id and state.call_id != normalized_id:
                raise _ProviderProtocolError("tool call id changed during streaming")
            owner = self._call_owners.get(normalized_id)
            if owner is not None and owner != key:
                raise _ProviderProtocolError(
                    f"duplicate tool call id: {normalized_id}"
                )
            state.call_id = normalized_id
            self._call_owners[normalized_id] = key
        if name:
            normalized_name = str(name).strip()
            if state.name and state.name != normalized_name:
                raise _ProviderProtocolError("tool name changed during streaming")
            state.name = normalized_name
        if delta:
            state.argument_parts.append(str(delta))
        return self._emit_ready(state)

    def arguments_done(
        self,
        key: str,
        raw: str,
        *,
        call_id: str | None = None,
        name: str | None = None,
    ) -> list[ModelStreamItem]:
        events = self.observe(key, call_id=call_id, name=name)
        state = self._states[key]
        normalized_raw = str(raw)
        if state.final_arguments is not None:
            if state.final_arguments != normalized_raw:
                raise _ProviderProtocolError(
                    "tool arguments changed after completion"
                )
            return events
        state.final_arguments = normalized_raw
        events.extend(self._emit_ready(state))
        return events

    def finalize(self) -> list[ModelStreamItem]:
        events: list[ModelStreamItem] = []
        for state in self._states.values():
            events.extend(self._emit_ready(state))
            if not state.ended:
                raise _ProviderProtocolError(
                    "provider ended before tool call identity or arguments completed"
                )
        return events

    def complete_buffered(self) -> list[ModelStreamItem]:
        events: list[ModelStreamItem] = []
        for state in self._states.values():
            if state.final_arguments is None:
                events.extend(
                    self.arguments_done(state.key, "".join(state.argument_parts))
                )
        return events

    def _emit_ready(self, state: _ToolCallState) -> list[ModelStreamItem]:
        if not state.call_id:
            return []
        events: list[ModelStreamItem] = []
        if not state.started:
            state.started = True
            events.append(
                ModelStreamItem(
                    type=StreamItemType.tool_call_start,
                    tool_call_id=state.call_id,
                    tool_name=state.name or None,
                )
            )
        for part in state.argument_parts[state.emitted_parts :]:
            events.append(
                ModelStreamItem(
                    type=StreamItemType.tool_call_delta,
                    tool_call_id=state.call_id,
                    tool_name=state.name or None,
                    arguments_delta=part,
                )
            )
        state.emitted_parts = len(state.argument_parts)
        if state.final_arguments is None or state.ended:
            return events
        if not state.name:
            raise _ProviderProtocolError("tool call completed without a name")
        try:
            arguments = json.loads(state.final_arguments)
        except json.JSONDecodeError as exc:
            raise _ProviderProtocolError(
                f"tool call {state.call_id} arguments are invalid JSON"
            ) from exc
        if not isinstance(arguments, dict):
            raise _ProviderProtocolError(
                f"tool call {state.call_id} arguments must be a JSON object"
            )
        state.ended = True
        events.append(
            ModelStreamItem(
                type=StreamItemType.tool_call_end,
                tool_call_id=state.call_id,
                tool_name=state.name,
                arguments=arguments,
            )
        )
        return events


def resolve_openai_api_mode(
    base_url: str | None,
    *,
    explicit: str | None = None,
) -> Literal["chat", "responses"]:
    """Pick chat vs responses. Custom base_url → chat; official OpenAI → responses."""
    raw = (explicit or os.environ.get("OPENAI_API_MODE") or "auto").strip().lower()
    if raw in {"chat", "completions", "chat_completions", "chat.completions"}:
        return "chat"
    if raw in {"responses", "response"}:
        return "responses"
    # auto
    if not base_url:
        return "responses"
    host = base_url.lower()
    if "api.openai.com" in host:
        return "responses"
    return "chat"


def _classify_error(exc: BaseException) -> tuple[str, str]:
    msg = str(exc)
    kind = "provider"
    low = msg.lower()
    status = getattr(exc, "status_code", None)
    if status is None:
        # openai.APIStatusError exposes .status on some versions
        status = getattr(exc, "status", None)
    if "rate" in low or status == 429:
        kind = "rate_limit"
    elif "timeout" in low or "timeout" in type(exc).__name__.lower():
        kind = "timeout"
    elif isinstance(status, int) and 500 <= status <= 599:
        kind = "server_error"
    elif "connection" in low or "connect" in type(exc).__name__.lower():
        kind = "connection"
    elif "cancel" in low:
        kind = "cancelled"
    return msg, kind


def _retry_after_seconds(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    header_sources = (getattr(response, "headers", None), getattr(exc, "headers", None))
    for headers in header_sources:
        if headers is None or not hasattr(headers, "get"):
            continue
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw is None:
            continue
        value = str(raw).strip()
        try:
            return min(86_400.0, max(0.0, float(value)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return min(
                86_400.0,
                max(0.0, (retry_at - datetime.now(UTC)).total_seconds()),
            )
    return None


def _error_item(exc: BaseException) -> ModelStreamItem:
    msg, kind = _classify_error(exc)
    return ModelStreamItem(
        type=StreamItemType.error,
        error=msg,
        error_kind=kind,
        retry_after_s=_retry_after_seconds(exc),
    )


def _is_not_found(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status == 404:
        return True
    low = str(exc).lower()
    return "404" in low or "not found" in low


def _stream_options_unsupported(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    message = str(exc).lower()
    return status in {400, 422} and (
        "stream_options" in message or "include_usage" in message
    )


def _attr_or_key(raw: Any, name: str) -> Any:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw.get(name)
    return getattr(raw, name, None)


def _cached_input_tokens(raw: Any) -> int:
    """Prompt-cache reads: Chat exposes prompt_tokens_details, Responses input_tokens_details."""
    for details_name in ("prompt_tokens_details", "input_tokens_details"):
        details = _attr_or_key(raw, details_name)
        cached = _attr_or_key(details, "cached_tokens")
        if cached is not None:
            try:
                return max(0, int(cached))
            except (TypeError, ValueError):
                return 0
    return 0


def _usage_item(raw: Any) -> Usage | None:
    if raw is None:
        return None
    input_tokens = int(
        getattr(raw, "input_tokens", 0)
        or getattr(raw, "prompt_tokens", 0)
        or 0
    )
    output_tokens = int(
        getattr(raw, "output_tokens", 0)
        or getattr(raw, "completion_tokens", 0)
        or 0
    )
    total_tokens = int(
        getattr(raw, "total_tokens", 0) or input_tokens + output_tokens
    )
    if not (input_tokens or output_tokens or total_tokens):
        return None
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=min(_cached_input_tokens(raw), input_tokens),
    )


def _event_error_message(event: Any) -> str:
    error = getattr(event, "error", None)
    response = getattr(event, "response", None)
    if error is None and response is not None:
        error = getattr(response, "error", None) or getattr(
            response, "incomplete_details", None
        )
    return str(
        getattr(event, "message", None)
        or getattr(error, "message", None)
        or getattr(error, "code", None)
        or error
        or getattr(event, "type", "provider error")
    )


async def _close_provider_stream(stream: Any) -> None:
    for name in ("close", "aclose"):
        close = getattr(stream, name, None)
        if not callable(close):
            continue
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - cleanup must not replace the run outcome
            pass
        return


class OpenAIResponsesAdapter:
    """OpenAI-compatible model adapter (name kept for import stability)."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        api_mode: str | None = None,
        use_env: bool = True,
    ) -> None:
        self.api_key = (
            (api_key or os.environ.get("OPENAI_API_KEY")) if use_env else api_key
        )
        configured_base_url = (
            (base_url or os.environ.get("OPENAI_BASE_URL")) if use_env else base_url
        )
        self.base_url = normalize_openai_base_url(configured_base_url)
        # Prefer explicit arg, then env, then hard default (arg must be None to allow env)
        self.default_model = (
            (default_model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")
            if use_env
            else (default_model or "gpt-4o-mini")
        )
        self.api_mode: Literal["chat", "responses"] = resolve_openai_api_mode(
            self.base_url, explicit=api_mode
        )
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
            # Some OpenAI-compatible gateways block openai-python's default
            # User-Agent at the edge before the API request is authenticated.
            kwargs["default_headers"] = {"User-Agent": "agentharness"}
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.close()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        if self.api_mode == "chat":
            async for item in self._stream_chat(request):
                yield item
            return

        # Responses first; on create() 404 fall back to chat (OAI-compatible gateways).
        try:
            async for item in self._stream_responses(request):
                yield item
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                self.api_mode = "chat"
                async for item in self._stream_chat(request):
                    yield item
                return
            yield _error_item(exc)

    # ------------------------------------------------------------------
    # Responses API (official OpenAI)
    # ------------------------------------------------------------------

    async def _stream_responses(
        self, request: ModelRequest
    ) -> AsyncIterator[ModelStreamItem]:
        client = self._get_client()
        model = request.model or self.default_model
        input_items = self._to_input(request)
        tools = self._to_tools_responses(request)
        kwargs: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            if request.parallel_tool_calls is not None:
                kwargs["parallel_tool_calls"] = request.parallel_tool_calls
        if request.reasoning_effort:
            kwargs["reasoning"] = {"effort": request.reasoning_effort}
        if request.max_tokens:
            kwargs["max_output_tokens"] = request.max_tokens
        try:
            stream = await client.responses.create(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                # Only endpoint discovery may switch the adapter to Chat mode.
                raise
            yield _error_item(exc)
            return

        calls = _ToolCallAccumulator()
        try:
            async for event in stream:
                etype = getattr(event, "type", "") or ""
                try:
                    if etype == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            yield ModelStreamItem(
                                type=StreamItemType.text_delta, text=delta
                            )
                    elif etype == "response.output_item.added":
                        item = getattr(event, "item", None)
                        if item is not None and getattr(item, "type", "") == "function_call":
                            item_id = str(getattr(item, "id", "") or "").strip()
                            if not item_id:
                                raise _ProviderProtocolError(
                                    "function call item is missing id"
                                )
                            for normalized in calls.observe(
                                item_id,
                                call_id=getattr(item, "call_id", None),
                                name=getattr(item, "name", None),
                            ):
                                yield normalized
                    elif etype == "response.function_call_arguments.delta":
                        item_id = str(getattr(event, "item_id", "") or "").strip()
                        if not item_id:
                            raise _ProviderProtocolError(
                                "function arguments delta is missing item_id"
                            )
                        for normalized in calls.observe(
                            item_id,
                            delta=str(getattr(event, "delta", "") or ""),
                        ):
                            yield normalized
                    elif etype == "response.function_call_arguments.done":
                        item_id = str(getattr(event, "item_id", "") or "").strip()
                        if not item_id:
                            raise _ProviderProtocolError(
                                "function arguments completion is missing item_id"
                            )
                        raw = getattr(event, "arguments", None)
                        if raw is None:
                            raise _ProviderProtocolError(
                                "function arguments completion is missing arguments"
                            )
                        for normalized in calls.arguments_done(
                            item_id,
                            str(raw),
                            name=getattr(event, "name", None),
                        ):
                            yield normalized
                    elif etype == "response.output_item.done":
                        item = getattr(event, "item", None)
                        if item is not None and getattr(item, "type", "") == "function_call":
                            item_id = str(getattr(item, "id", "") or "").strip()
                            if not item_id:
                                raise _ProviderProtocolError(
                                    "completed function call item is missing id"
                                )
                            raw = getattr(item, "arguments", None)
                            if raw is None:
                                raise _ProviderProtocolError(
                                    "completed function call item is missing arguments"
                                )
                            for normalized in calls.arguments_done(
                                item_id,
                                str(raw),
                                call_id=getattr(item, "call_id", None),
                                name=getattr(item, "name", None),
                            ):
                                yield normalized
                    elif etype == "response.completed":
                        for normalized in calls.finalize():
                            yield normalized
                        response = getattr(event, "response", None)
                        usage = _usage_item(
                            getattr(response, "usage", None) if response else None
                        )
                        if usage is not None:
                            yield ModelStreamItem(
                                type=StreamItemType.usage, usage=usage
                            )
                        yield ModelStreamItem(type=StreamItemType.done)
                        return
                    elif etype == "response.incomplete":
                        yield ModelStreamItem(
                            type=StreamItemType.error,
                            error=_event_error_message(event),
                            error_kind="provider",
                        )
                        return
                    elif etype == "response.cancelled":
                        yield ModelStreamItem(
                            type=StreamItemType.error,
                            error=_event_error_message(event),
                            error_kind="cancelled",
                        )
                        return
                    elif etype == "error" or etype.endswith(".failed"):
                        message = _event_error_message(event)
                        _unused, kind = _classify_error(Exception(message))
                        yield ModelStreamItem(
                            type=StreamItemType.error,
                            error=message,
                            error_kind=kind,
                        )
                        return
                except _ProviderProtocolError as exc:
                    yield ModelStreamItem(
                        type=StreamItemType.error,
                        error=str(exc),
                        error_kind="provider_protocol",
                    )
                    return
            yield ModelStreamItem(
                type=StreamItemType.error,
                error="OpenAI Responses stream ended before response.completed",
                error_kind="connection",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield _error_item(exc)
        finally:
            await _close_provider_stream(stream)

    # ------------------------------------------------------------------
    # Chat Completions (OpenAI-compatible gateways)
    # ------------------------------------------------------------------

    async def _stream_chat(
        self, request: ModelRequest
    ) -> AsyncIterator[ModelStreamItem]:
        client = self._get_client()
        model = request.model or self.default_model
        messages = self._to_chat_messages(request)
        tools = self._to_tools_chat(request)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            if request.parallel_tool_calls is not None:
                kwargs["parallel_tool_calls"] = request.parallel_tool_calls
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        # Best-effort usage on stream; retry without it only when the gateway
        # explicitly rejects this field.
        kwargs["stream_options"] = {"include_usage": True}
        try:
            stream = await client.chat.completions.create(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not _stream_options_unsupported(exc):
                yield _error_item(exc)
                return
            kwargs.pop("stream_options", None)
            try:
                stream = await client.chat.completions.create(**kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as retry_exc:  # noqa: BLE001
                yield _error_item(retry_exc)
                return

        calls = _ToolCallAccumulator()
        finish_reason: str | None = None
        latest_usage: Usage | None = None
        saw_payload = False
        custom_gateway = bool(self.base_url) and "api.openai.com" not in self.base_url.lower()
        try:
            async for chunk in stream:
                usage = _usage_item(getattr(chunk, "usage", None))
                if usage is not None:
                    latest_usage = usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                saw_payload = True
                choice = choices[0]
                chunk_finish = getattr(choice, "finish_reason", None)
                if chunk_finish:
                    normalized_finish = str(chunk_finish)
                    if finish_reason and finish_reason != normalized_finish:
                        raise _ProviderProtocolError(
                            "Chat completion finish reason changed during streaming"
                        )
                    finish_reason = normalized_finish
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                content = getattr(delta, "content", None)
                if content:
                    yield ModelStreamItem(type=StreamItemType.text_delta, text=content)
                tool_calls = getattr(delta, "tool_calls", None) or []
                for tc in tool_calls:
                    idx = int(getattr(tc, "index", 0) or 0)
                    fn = getattr(tc, "function", None)
                    for normalized in calls.observe(
                        f"index:{idx}",
                        call_id=getattr(tc, "id", None),
                        name=getattr(fn, "name", None) if fn is not None else None,
                        delta=(
                            str(getattr(fn, "arguments", None) or "")
                            if fn is not None
                            else None
                        ),
                    ):
                        yield normalized

            # Some explicitly configured OpenAI-compatible gateways close a
            # complete stream without emitting finish_reason. Keep strict EOF
            # detection for the official/unknown endpoint contract.
            if finish_reason is None and (not saw_payload or not custom_gateway):
                yield ModelStreamItem(
                    type=StreamItemType.error,
                    error="OpenAI Chat stream ended before finish_reason",
                    error_kind="connection",
                )
                return
            if finish_reason in {"length", "content_filter"}:
                yield ModelStreamItem(
                    type=StreamItemType.error,
                    error=f"OpenAI Chat completion ended with {finish_reason}",
                    error_kind="provider",
                )
                return
            for normalized in calls.complete_buffered():
                yield normalized
            for normalized in calls.finalize():
                yield normalized
            if latest_usage is not None:
                yield ModelStreamItem(type=StreamItemType.usage, usage=latest_usage)
            yield ModelStreamItem(type=StreamItemType.done)
        except _ProviderProtocolError as exc:
            yield ModelStreamItem(
                type=StreamItemType.error,
                error=str(exc),
                error_kind="provider_protocol",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield _error_item(exc)
        finally:
            await _close_provider_stream(stream)

    # ------------------------------------------------------------------
    # Message / tool conversion
    # ------------------------------------------------------------------

    def _to_input(self, request: ModelRequest) -> list[dict[str, Any]]:
        """Responses API input items."""
        items: list[dict[str, Any]] = []
        if request.system:
            items.append({"role": "system", "content": request.system})
        for m in request.messages:
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            if role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": m.tool_call_id or m.id,
                        "output": m.content,
                    }
                )
            elif role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tc.id,
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        }
                    )
                if m.content:
                    items.append({"role": "assistant", "content": m.content})
            else:
                items.append({"role": role, "content": m.content})
        return items

    def _to_chat_messages(self, request: ModelRequest) -> list[dict[str, Any]]:
        """Chat Completions messages array."""
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for m in request.messages:
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            if role == "tool":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id or m.id,
                        "content": m.content or "",
                    }
                )
            elif role == "assistant" and m.tool_calls:
                tool_calls = []
                for tc in m.tool_calls:
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(
                                    tc.arguments, ensure_ascii=False
                                ),
                            },
                        }
                    )
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": tool_calls,
                }
                messages.append(entry)
            else:
                messages.append({"role": role, "content": m.content})
        return messages

    def _to_tools_responses(self, request: ModelRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in request.tools:
            out.append(
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters
                    or {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": True,
                    },
                }
            )
        return out

    def _to_tools_chat(self, request: ModelRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in request.tools:
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters
                        or {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": True,
                        },
                    },
                }
            )
        return out

    # Back-compat alias used by older tests / callers
    def _to_tools(self, request: ModelRequest) -> list[dict[str, Any]]:
        if self.api_mode == "chat":
            return self._to_tools_chat(request)
        return self._to_tools_responses(request)
