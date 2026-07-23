"""Eval suite schema and loader.

A suite is a list of cases. Each case is a task message plus a lightweight,
provider-agnostic success check. v1 checks only what RunResult already exposes:
terminal status and output substrings. Tool-usage checks (which read the run
tree from Storage) are layered on in the runner without changing this schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    """One scored task."""

    id: str
    prompt: str
    # Success criteria — all provided ones must hold.
    expect_status: str = "completed"
    expect_output_contains: list[str] = Field(default_factory=list)
    expect_output_contains_any: list[str] = Field(default_factory=list)
    expect_tools_used: list[str] = Field(default_factory=list)
    # Per-case overrides (else the suite/CLI defaults apply).
    provider: str | None = None
    model: str | None = None
    system: str | None = None
    cwd: str | None = None
    tags: list[str] = Field(default_factory=list)


class EvalSuite(BaseModel):
    """A named collection of cases with optional suite-wide defaults."""

    name: str = "suite"
    provider: str | None = None
    model: str | None = None
    system: str | None = None
    cases: list[EvalCase] = Field(default_factory=list)


def load_suite(path: str | Path) -> EvalSuite:
    """Load a suite from JSON or JSONL.

    JSON: either a full suite object ({"name", "cases": [...]}) or a bare list
    of case objects. JSONL: one case object per line.
    """
    p = Path(path).expanduser()
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".jsonl":
        cases = [json.loads(line) for line in text.splitlines() if line.strip()]
        return EvalSuite(name=p.stem, cases=[EvalCase(**c) for c in cases])
    data: Any = json.loads(text)
    if isinstance(data, list):
        return EvalSuite(name=p.stem, cases=[EvalCase(**c) for c in data])
    return EvalSuite(**data)
