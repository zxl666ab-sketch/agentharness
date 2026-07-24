"""Eval suite DSL: YAML / JSON / JSONL, strict Pydantic validation.

A suite is suite-wide ``defaults`` plus a list of ``cases``. Each case is a
prompt (inline or loaded from a suite-relative file) plus an ``assert`` block of
provider-agnostic success criteria. Loading resolves defaults onto every case
and expands ``prompt_file`` so the runner sees fully-materialized cases.

All load-time problems (bad YAML/JSON, unknown fields, missing required fields,
out-of-range values, missing files) surface as :class:`EvalConfigError` so the
CLI can map them to a distinct config exit code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class EvalConfigError(Exception):
    """Raised for any malformed suite: syntax, schema, or missing file."""


class AssertionSpec(BaseModel):
    """Deterministic success criteria — every provided one must hold."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    contains: list[str] = Field(default_factory=list)
    contains_any: list[str] = Field(default_factory=list)
    regex: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    tools_order: list[str] = Field(default_factory=list)
    max_tokens: int | None = Field(default=None, ge=0)
    max_steps: int | None = Field(default=None, ge=0)
    max_latency_s: float | None = Field(default=None, ge=0)
    # Optional natural-language rubric for the (default-off) LLM judge.
    rubric: str | None = None


class EvalCase(BaseModel):
    """One scored task. ``assert`` in the file maps to ``assertions`` here."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    prompt: str | None = None
    prompt_file: str | None = None
    provider: str | None = None
    model: str | None = None
    system: str | None = None
    cwd: str | None = None
    tags: list[str] = Field(default_factory=list)
    repeat: int = Field(default=1, ge=1)
    budget: dict[str, Any] | None = None
    assertions: AssertionSpec = Field(default_factory=AssertionSpec, alias="assert")


class SuiteDefaults(BaseModel):
    """Suite-wide fallbacks applied to any case that omits the field."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None
    system: str | None = None
    cwd: str | None = None
    budget: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)


class EvalSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "suite"
    defaults: SuiteDefaults = Field(default_factory=SuiteDefaults)
    cases: list[EvalCase] = Field(default_factory=list)


def _parse_document(path: Path) -> Any:
    """Read + parse one suite file into raw Python data (dict or list)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EvalConfigError(f"suite file not found: {path}") from exc
    except OSError as exc:
        raise EvalConfigError(f"cannot read suite file {path}: {exc}") from exc

    suffix = path.suffix.lower()
    try:
        if suffix == ".jsonl":
            cases = [json.loads(line) for line in text.splitlines() if line.strip()]
            return {"name": path.stem, "cases": cases}
        if suffix == ".json":
            return json.loads(text)
        # YAML is a JSON superset; use it for .yaml/.yml and anything else.
        return yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise EvalConfigError(f"invalid {suffix or 'suite'} in {path}: {exc}") from exc


def _normalize(data: Any, *, default_name: str) -> dict[str, Any]:
    """Coerce a bare list of cases into a suite dict; leave dicts as-is."""
    if isinstance(data, list):
        return {"name": default_name, "cases": data}
    if isinstance(data, dict):
        return data
    raise EvalConfigError(
        f"suite must be a mapping or a list of cases, got {type(data).__name__}"
    )


def _resolve_case(case: EvalCase, defaults: SuiteDefaults, base_dir: Path) -> EvalCase:
    """Apply suite defaults and expand prompt_file, relative to the suite dir."""
    prompt = case.prompt
    if case.prompt_file is not None:
        fp = Path(case.prompt_file)
        if not fp.is_absolute():
            fp = base_dir / fp
        try:
            prompt = fp.read_text(encoding="utf-8")
        except OSError as exc:
            raise EvalConfigError(
                f"case {case.id!r}: cannot read prompt_file {fp}: {exc}"
            ) from exc
    if prompt is None:
        raise EvalConfigError(f"case {case.id!r}: prompt or prompt_file is required")

    cwd = case.cwd or defaults.cwd
    if cwd is not None:
        cp = Path(cwd)
        if not cp.is_absolute():
            cwd = str((base_dir / cp).resolve())

    return case.model_copy(
        update={
            "prompt": prompt,
            "prompt_file": None,
            "provider": case.provider or defaults.provider,
            "model": case.model or defaults.model,
            "system": case.system or defaults.system,
            "cwd": cwd,
            "budget": case.budget if case.budget is not None else defaults.budget,
            "tags": case.tags or list(defaults.tags),
        }
    )


def load_suite(path: str | Path) -> EvalSuite:
    """Load and fully resolve a suite from YAML, JSON, or JSONL.

    Raises :class:`EvalConfigError` for any malformed input. Cases returned have
    defaults merged in and ``prompt`` materialized (``prompt_file`` cleared).
    """
    p = Path(path).expanduser()
    raw = _parse_document(p)
    payload = _normalize(raw, default_name=p.stem)
    try:
        suite = EvalSuite.model_validate(payload)
    except ValidationError as exc:
        raise EvalConfigError(f"invalid suite {p}: {exc}") from exc
    base_dir = p.resolve().parent
    suite.cases = [_resolve_case(c, suite.defaults, base_dir) for c in suite.cases]
    return suite
