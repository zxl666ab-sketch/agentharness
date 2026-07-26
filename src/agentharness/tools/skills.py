"""SKILL.md progressive loading — name/description at start, body on match."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentharness.contracts import EffectKind, ToolContext, ToolResult, ToolSpec


def discover_skills(dirs: list[str] | None) -> list[dict[str, str]]:
    """Load only name, description, path — not full body."""
    found: list[dict[str, str]] = []
    for d in dirs or []:
        root = Path(d)
        if not root.exists():
            continue
        for skill_md in root.rglob("SKILL.md"):
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = skill_md.parent.name
            desc = ""
            # YAML frontmatter or first heading
            fm = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
            if fm:
                for line in fm.group(1).splitlines():
                    if line.lower().startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip("\"'")
                    if line.lower().startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip("\"'")
            if not desc:
                m = re.search(r"^#\s+(.+)$", text, re.M)
                if m:
                    desc = m.group(1).strip()
                else:
                    desc = text[:120].replace("\n", " ")
            found.append(
                {
                    "name": name,
                    "description": desc,
                    "path": str(skill_md),
                    "root": str(root),
                }
            )
    return found


def load_matching_skills(dirs: list[str] | None, task: str, limit: int = 3) -> list[str]:
    """Match skills to task by name/description keywords; load full body + refs."""
    skills = discover_skills(dirs)
    if not skills or not task:
        return []
    task_l = task.lower()
    scored: list[tuple[int, dict[str, str]]] = []
    for s in skills:
        score = 0
        for token in re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", s["name"] + " " + s["description"]):
            if token.lower() in task_l:
                score += 2
            if len(token) > 3 and token.lower() in task_l:
                score += 1
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    bodies: list[str] = []
    for _, s in scored[:limit]:
        body = _load_skill_body(Path(s["path"]), root=Path(s["root"]) if s.get("root") else None)
        bodies.append(f"### Skill: {s['name']}\n{body}")
    return bodies


_MAX_REF_BYTES = 4000


def _safe_ref_path(ref: str, skill_path: Path, root: Path) -> Path | None:
    """Resolve a SKILL.md reference, refusing anything that escapes ``root``.

    Refs come from a file an agent may be able to write, so they are untrusted
    input: absolute paths, dot-segments, symlinks and oversized files are all
    rejected rather than read into model context.
    """
    if not ref or "\x00" in ref:
        return None
    candidate = Path(ref)
    if candidate.is_absolute() or candidate.drive or candidate.root:
        return None
    try:
        resolved = (skill_path.parent / candidate).resolve()
        root_resolved = root.resolve()
    except OSError:
        return None
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return None
    try:
        if resolved.is_symlink() or not resolved.is_file():
            return None
        if resolved.stat().st_size > _MAX_REF_BYTES:
            return None
    except OSError:
        return None
    return resolved


def _load_skill_body(path: Path, root: Path | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(failed to load: {exc})"
    # strip frontmatter
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)
    # load referenced relative files if mentioned as [ref](./file)
    ref_root = root if root is not None else path.parent
    refs = re.findall(r"\((?:\./)?([^)]+\.(?:md|txt|py))\)", text)
    extras: list[str] = []
    for ref in refs[:5]:
        ref_path = _safe_ref_path(ref, path, ref_root)
        if ref_path is None:
            continue
        try:
            extras.append(
                f"\n#### Ref: {ref}\n"
                + ref_path.read_text(encoding="utf-8", errors="replace")[:_MAX_REF_BYTES]
            )
        except OSError:
            pass
    return text[:8000] + "".join(extras)


class ListSkillsTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_skills",
            description="List available SKILL.md skills (name + description only).",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.pure,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        dirs = (ctx.metadata or {}).get("skills_dirs") or []
        skills = discover_skills(dirs)
        if not skills:
            return ToolResult(tool_call_id="", name="list_skills", content="No skills found")
        lines = [f"- {s['name']}: {s['description']}" for s in skills]
        return ToolResult(tool_call_id="", name="list_skills", content="\n".join(lines))
