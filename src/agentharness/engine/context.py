"""Deep context-planning module: stable selection, budgeting, and manifests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agentharness.contracts import (
    ContextBundle,
    ContextManifest,
    ContextManifestItem,
    ContextPinnedItem,
    ContextState,
    Message,
    MessageRole,
    RunRequest,
    ToolSpec,
    Usage,
)
from agentharness.security.redaction import Redactor, default_redactor

_RULE_FILES = ("AGENTS.md", "WORKBUDDY.md")
_MAX_RULE_BYTES = 64 * 1024

# CJK ideographs, kana, hangul and CJK punctuation consume roughly one token
# per character in Chinese-capable tokenizers (DeepSeek, Qwen, GLM, GPT
# cl100k); ASCII stays at ~4 chars/token.
_CJK_RE = re.compile(
    "[\u2E80-\u9FFF\u3040-\u30FF\uAC00-\uD7AF\uF900-\uFAFF"
    "\uFF00-\uFFEF\u3000-\u303F]"
)


class ContextBudgetError(ValueError):
    """Essential context cannot fit without violating planner invariants."""


def estimate_tokens(text: str) -> int:
    """Deterministic CJK-aware token estimate when provider count is unavailable.

    ASCII text is estimated at ~4 chars/token. CJK characters are estimated at
    1 token/char so Chinese-heavy prompts no longer systematically undercount,
    which used to delay compaction and distort context budgets.
    """
    if not text:
        return 0
    cjk = sum(1 for char in text if _CJK_RE.match(char))
    ascii_chars = len(text) - cjk
    return max(1, cjk + (ascii_chars + 3) // 4)


def billable_turn_usage(
    *,
    provider_usage: Usage,
    local_input_estimate: int,
    output_text: str = "",
    inflation_ratio: int = 8,
) -> Usage:
    """Return defensible per-turn token charges despite broken gateway counters."""
    local_out = estimate_tokens(output_text) if output_text else 0
    prov_in = max(0, int(provider_usage.input_tokens or 0))
    prov_out = max(0, int(provider_usage.output_tokens or 0))
    local_in = max(0, int(local_input_estimate or 0))
    used_estimate = bool(provider_usage.estimated)

    if local_in > 0 and prov_in > max(local_in * inflation_ratio, local_in + 4096):
        bill_in = local_in
        used_estimate = True
    elif prov_in > 0:
        bill_in = prov_in
    else:
        bill_in = local_in
        if bill_in:
            used_estimate = True

    if local_out > 0 and prov_out > max(local_out * inflation_ratio, local_out + 1024):
        bill_out = local_out
        used_estimate = True
    elif prov_out > 0:
        bill_out = prov_out
    else:
        bill_out = local_out
        if bill_out:
            used_estimate = True

    return Usage(
        input_tokens=bill_in,
        output_tokens=bill_out,
        total_tokens=bill_in + bill_out,
        estimated=used_estimate,
    )


def estimate_messages_tokens(messages: list[Message]) -> int:
    total = 0
    for message in messages:
        total += estimate_tokens(message.content)
        if message.tool_calls:
            for call in message.tool_calls:
                total += estimate_tokens(call.name)
                total += estimate_tokens(_stable_json(call.arguments))
    return total


def estimate_tool_tokens(tool: ToolSpec) -> int:
    return estimate_tokens(tool.name + tool.description + _stable_json(tool.parameters))


class ContextPlanner:
    """Plan complete provider context behind one stable, testable interface.

    The caller supplies run lifecycle facts and persists the returned opaque state.
    Workspace-rule discovery, deterministic ordering, compaction, externalization,
    hashing, and manifest construction stay inside this module.
    """

    def __init__(
        self,
        *,
        storage: Any | None = None,
        artifacts: Any | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.storage = storage
        self.artifacts = artifacts
        self.redactor = redactor or default_redactor

    def plan(
        self,
        *,
        run_id: str,
        request: RunRequest,
        messages: list[Message],
        tools: list[ToolSpec],
        model_turn: int,
        state: ContextState | dict[str, Any] | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> ContextBundle:
        """Return the exact budgeted provider input and redaction-safe evidence."""
        budget = int(max_tokens or request.budget.max_context_tokens)
        if budget <= 0:
            raise ContextBudgetError("context token budget must be positive")
        pinned = self._coerce_or_select_state(state, request, system=system)
        tools_out = sorted((tool.model_copy(deep=True) for tool in tools), key=lambda t: t.name)
        system_text = self._render_stable_prefix(pinned)
        prefix_fingerprint = _sha256(
            _stable_json(
                {
                    "system": system_text,
                    "tools": [tool.model_dump(mode="json") for tool in tools_out],
                }
            )
        )

        manifest_items = self._state_manifest_items(pinned)
        tool_tokens = 0
        for tool in tools_out:
            tokens = estimate_tool_tokens(tool)
            tool_tokens += tokens
            manifest_items.append(
                ContextManifestItem(
                    section="tool_schemas",
                    source=tool.name,
                    content_hash=_sha256(_stable_json(tool.model_dump(mode="json"))),
                    token_estimate=tokens,
                    included=True,
                    reason="enabled for this run",
                    preview=self.redactor.redact_text(tool.description[:160]),
                )
            )

        planned_messages, message_items, compacted = self._budget_messages(
            messages=messages,
            base_tokens=estimate_tokens(system_text or "") + tool_tokens,
            budget=budget,
            summarized_ids=set(pinned.summarized_message_ids),
        )
        manifest_items.extend(message_items)
        total = estimate_tokens(system_text or "") + tool_tokens + estimate_messages_tokens(
            planned_messages
        )
        if total > budget:
            raise ContextBudgetError(
                f"essential context requires {total} tokens but budget is {budget}"
            )

        manifest = ContextManifest(
            run_id=run_id,
            model_turn=model_turn,
            budget_tokens=budget,
            total_tokens=total,
            prefix_fingerprint=prefix_fingerprint,
            compacted=compacted,
            items=manifest_items,
        )
        # Defense in depth: sources/previews/state are persistence candidates.
        safe_manifest = ContextManifest.model_validate(
            self.redactor.redact_obj(manifest.model_dump(mode="json"))
        )
        safe_state = ContextState.model_validate(
            self.redactor.redact_obj(pinned.model_dump(mode="json"))
        )
        return ContextBundle(
            system=system_text,
            messages=planned_messages,
            tools=tools_out,
            manifest=safe_manifest,
            state=safe_state,
        )

    def _coerce_or_select_state(
        self,
        state: ContextState | dict[str, Any] | None,
        request: RunRequest,
        *,
        system: str | None,
    ) -> ContextState:
        if state is not None:
            return ContextState.model_validate(state).model_copy(deep=True)
        items = [
            self._pinned(
                "system",
                "request.system" if request.system else "harness.default_system",
                system or request.system or self._default_system(request),
                "required safety and run instructions",
            )
        ]
        items.extend(self._discover_workspace_rules(request))
        return ContextState(items=items)

    def _pinned(
        self,
        section: str,
        source: str,
        content: str,
        reason: str,
        *,
        selected: bool = True,
    ) -> ContextPinnedItem:
        safe_content = self.redactor.redact_text(content) if selected else ""
        return ContextPinnedItem(
            section=section,
            source=self.redactor.redact_text(source),
            content=safe_content,
            content_hash=_sha256(safe_content) if safe_content else "",
            token_estimate=estimate_tokens(safe_content),
            selected=selected,
            reason=reason,
        )

    def _discover_workspace_rules(self, request: RunRequest) -> list[ContextPinnedItem]:
        cwd = Path(request.cwd or ".").expanduser().resolve()
        legal_roots = _existing_resolved_dirs([request.cwd or ".", *request.extra_dirs])
        if cwd not in legal_roots:
            legal_roots.append(cwd)

        scan_dirs: list[Path] = []
        ancestors = [root for root in legal_roots if _is_relative_to(cwd, root)]
        if ancestors:
            root = min(ancestors, key=lambda value: (len(value.parts), str(value)))
            relative = cwd.relative_to(root)
            current = root
            scan_dirs.append(current)
            for part in relative.parts:
                current = current / part
                scan_dirs.append(current)
        else:
            scan_dirs.append(cwd)
        for root in legal_roots:
            if root not in scan_dirs and not _is_relative_to(cwd, root):
                scan_dirs.append(root)

        items: list[ContextPinnedItem] = []
        seen: set[Path] = set()
        for directory in scan_dirs:
            for filename in _RULE_FILES:
                candidate = directory / filename
                if candidate in seen or not candidate.exists():
                    continue
                seen.add(candidate)
                source = str(candidate)
                try:
                    if candidate.is_symlink():
                        items.append(
                            self._pinned(
                                "workspace_rules",
                                source,
                                "",
                                "excluded: symbolic links are not allowed",
                                selected=False,
                            )
                        )
                        continue
                    resolved = candidate.resolve(strict=True)
                    if not any(_is_relative_to(resolved, root) for root in legal_roots):
                        items.append(
                            self._pinned(
                                "workspace_rules",
                                source,
                                "",
                                "excluded: outside legal workspace roots",
                                selected=False,
                            )
                        )
                        continue
                    size = resolved.stat().st_size
                    if size > _MAX_RULE_BYTES:
                        items.append(
                            self._pinned(
                                "workspace_rules",
                                source,
                                "",
                                f"excluded: file size {size} exceeds {_MAX_RULE_BYTES} bytes",
                                selected=False,
                            )
                        )
                        continue
                    content = resolved.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    items.append(
                        self._pinned(
                            "workspace_rules",
                            source,
                            "",
                            f"excluded: unreadable ({type(exc).__name__})",
                            selected=False,
                        )
                    )
                    continue
                items.append(
                    self._pinned(
                        "workspace_rules",
                        source,
                        content,
                        "workspace rule from root-to-cwd hierarchy",
                    )
                )
        return items

    def _render_stable_prefix(self, state: ContextState) -> str | None:
        selected = [item for item in state.items if item.selected]
        system = next((item.content for item in selected if item.section == "system"), "")
        parts = [system] if system else []
        for section, title in (
            ("workspace_rules", "Workspace rules"),
            ("history_summary", "Conversation summary"),
        ):
            section_items = [item for item in selected if item.section == section]
            if not section_items:
                continue
            rendered = []
            for item in section_items:
                if section == "history_summary":
                    source = "summary of earlier conversation (auto-compacted)"
                else:
                    source = item.source
                rendered.append(f"### {source}\n{item.content}")
            parts.append(f"## {title}\n" + "\n\n".join(rendered))
        return "\n\n".join(parts) if parts else None

    def _state_manifest_items(self, state: ContextState) -> list[ContextManifestItem]:
        return [
            ContextManifestItem(
                section=item.section,
                source=item.source,
                content_hash=item.content_hash,
                token_estimate=item.token_estimate,
                included=item.selected,
                reason=item.reason,
                compression="none" if item.selected else "excluded",
                artifact_id=item.artifact_id,
                preview=self.redactor.redact_text(item.content[:160]) if item.selected else "",
            )
            for item in state.items
        ]

    def _budget_messages(
        self,
        *,
        messages: list[Message],
        base_tokens: int,
        budget: int,
        summarized_ids: set[str] | None = None,
    ) -> tuple[list[Message], list[ContextManifestItem], bool]:
        copied = [message.model_copy(deep=True) for message in messages]
        groups = _message_groups(copied)
        summarized = summarized_ids or set()
        last_user = max(
            (index for index, message in enumerate(copied) if message.role == MessageRole.user),
            default=-1,
        )
        items: list[ContextManifestItem] = []
        for group in groups:
            covered = bool(group["valid"]) and all(
                message.id in summarized for message in group["messages"]
            )
            group["covered"] = covered
            tokens = estimate_messages_tokens(group["messages"])
            group["tokens"] = tokens
            if covered:
                reason = "summarized into rolling history summary"
            elif group["valid"]:
                reason = "conversation history"
            else:
                reason = "excluded: orphaned tool pair"
            group["item"] = ContextManifestItem(
                section="messages",
                source=f"message:{group['messages'][0].id}",
                content_hash=_sha256(
                    _stable_json([message.model_dump(mode="json") for message in group["messages"]])
                ),
                token_estimate=tokens,
                included=bool(group["valid"]) and not covered,
                reason=reason,
                compression=(
                    "summarized" if covered else "none" if group["valid"] else "excluded"
                ),
                preview=self.redactor.redact_text(group["messages"][0].content[:160]),
            )
            items.append(group["item"])

        included = [
            group for group in groups if group["valid"] and not group["covered"]
        ]
        total = base_tokens + sum(int(group["tokens"]) for group in included)
        compacted = len(included) != len(groups)

        # Oldest complete groups are externalized first. The newest user goal and
        # everything after it remain structurally intact.
        for group in included:
            if total <= budget:
                break
            if int(group["end"]) >= last_user:
                continue
            total -= int(group["tokens"])
            group["included"] = False
            item: ContextManifestItem = group["item"]
            item.included = False
            item.reason = "excluded by priority: older conversation history"
            item.compression = "externalized" if self.artifacts is not None else "excluded"
            item.artifact_id = self._externalize_messages(group["messages"])
            compacted = True

        final_groups = [group for group in included if group.get("included", True)]
        final_messages = [message for group in final_groups for message in group["messages"]]

        # If the active tool pair itself is large, externalize result bodies but
        # retain both the assistant call and its matching tool-result message.
        if total > budget:
            for group in final_groups:
                if total <= budget:
                    break
                if not group.get("is_tool_pair"):
                    continue
                changed = False
                for message in group["messages"]:
                    if message.role != MessageRole.tool or len(message.content) < 160:
                        continue
                    artifact_id = self._externalize_messages([message])
                    if not artifact_id:
                        continue
                    pointer = f"[tool result externalized as artifact:{artifact_id}]"
                    old_tokens = estimate_tokens(message.content)
                    message.content = pointer
                    total -= max(0, old_tokens - estimate_tokens(pointer))
                    changed = True
                if changed:
                    item = group["item"]
                    item.token_estimate = estimate_messages_tokens(group["messages"])
                    item.reason = "included with large tool result externalized"
                    item.compression = "externalized"
                    compacted = True

        if total > budget:
            raise ContextBudgetError(
                f"essential context requires {total} tokens but budget is {budget}"
            )
        return final_messages, items, compacted

    def _externalize_messages(self, messages: list[Message]) -> str | None:
        if self.artifacts is None:
            return None
        try:
            meta = self.artifacts.put_json(
                [message.model_dump(mode="json") for message in messages],
                summary="Context content externalized by budget planner",
            )
            if self.storage is not None:
                meta["id"] = self.storage.register_artifact(meta)
            return str(meta.get("id") or "") or None
        except Exception:  # noqa: BLE001
            return None

    def select_state(
        self, request: RunRequest, *, system: str | None = None
    ) -> ContextState:
        """Materialize the initial pinned selection without running a full plan.

        The engine needs this when auto-compaction fires before the first
        ``plan`` call of a run (e.g. a resumed session with long history):
        compaction must extend the real selection, not an empty state.
        """
        return self._coerce_or_select_state(None, request, system=system)

    def externalize_messages(self, messages: list[Message]) -> str | None:
        """Persist original messages as an artifact; returns its id (audit trail)."""
        return self._externalize_messages(messages)

    def apply_compaction(
        self,
        state: ContextState,
        *,
        summary_text: str,
        covered_ids: list[str],
        artifact_id: str | None = None,
        reason: str | None = None,
    ) -> ContextState:
        """Fold covered messages into the single rolling ``history_summary`` item.

        The summary replaces any previous one (the caller chains prior summary
        text into the new summarization input), covered ids accumulate, and the
        planner excludes covered groups from every later ``plan`` call.
        """
        updated = ContextState.model_validate(state).model_copy(deep=True)
        updated.items = [
            item for item in updated.items if item.section != "history_summary"
        ]
        count = updated.compaction_count + 1
        item = self._pinned(
            "history_summary",
            f"compaction:{count}",
            summary_text,
            reason or f"auto-compacted {len(covered_ids)} earlier messages",
        )
        item.artifact_id = artifact_id
        updated.items.append(item)
        existing = set(updated.summarized_message_ids)
        updated.summarized_message_ids.extend(
            message_id for message_id in covered_ids if message_id not in existing
        )
        updated.compaction_count = count
        return updated

    @staticmethod
    def summary_text(state: ContextState | None) -> str:
        """Current rolling summary content, empty when no compaction happened."""
        if state is None:
            return ""
        for item in state.items:
            if item.section == "history_summary" and item.selected:
                return item.content
        return ""

    @staticmethod
    def _default_system(request: RunRequest) -> str:
        return (
            "You are a capable agent running inside Agent Harness. "
            "Follow safety and workspace rules, use tools when needed, and be concise and accurate. "
            f"Workspace cwd: {request.cwd or '.'}."
        )


def _message_groups(messages: list[Message]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == MessageRole.assistant and message.tool_calls:
            call_ids = {call.id for call in message.tool_calls}
            grouped = [message]
            found: set[str] = set()
            cursor = index + 1
            while cursor < len(messages) and messages[cursor].role == MessageRole.tool:
                tool_message = messages[cursor]
                if tool_message.tool_call_id not in call_ids:
                    break
                grouped.append(tool_message)
                if tool_message.tool_call_id:
                    found.add(tool_message.tool_call_id)
                cursor += 1
            groups.append(
                {
                    "messages": grouped,
                    "start": index,
                    "end": cursor - 1,
                    "valid": found == call_ids,
                    "is_tool_pair": True,
                    "included": True,
                }
            )
            index = cursor
            continue
        groups.append(
            {
                "messages": [message],
                "start": index,
                "end": index,
                "valid": message.role != MessageRole.tool,
                "is_tool_pair": False,
                "included": True,
            }
        )
        index += 1
    return groups


def _existing_resolved_dirs(values: list[str]) -> list[Path]:
    roots: list[Path] = []
    for value in values:
        try:
            path = Path(value).expanduser().resolve(strict=True)
        except OSError:
            continue
        if path.is_dir() and path not in roots:
            roots.append(path)
    return sorted(roots, key=lambda path: (len(path.parts), str(path)))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
