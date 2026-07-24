"""DSL loader: YAML / JSON / JSONL, strict validation, suite-relative paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentharness.eval import EvalConfigError, load_suite


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def test_load_yaml_suite_with_defaults_and_assertions(tmp_path: Path) -> None:
    suite_path = _write(
        tmp_path / "s.yaml",
        """
        name: demo
        defaults:
          provider: fake
          model: m1
          system: be terse
          budget:
            max_steps: 10
        cases:
          - id: hello
            prompt: "[fake:text]hello world"
            tags: [smoke]
            assert:
              status: completed
              contains: ["hello"]
              contains_any: ["world", "planet"]
              regex: "he..o"
              max_tokens: 100
              max_steps: 5
              max_latency_s: 30
          - id: uses-tool
            prompt: "[fake:tools]read_file"
            provider: fake
            assert:
              tools_used: [read_file]
              tools_order: [read_file]
        """,
    )
    suite = load_suite(suite_path)
    assert suite.name == "demo"
    assert len(suite.cases) == 2
    c0 = suite.cases[0]
    # suite defaults propagate onto the resolved case
    assert c0.provider == "fake"
    assert c0.model == "m1"
    assert c0.system == "be terse"
    assert c0.budget is not None and c0.budget.get("max_steps") == 10
    assert c0.tags == ["smoke"]
    a = c0.assertions
    assert a.status == "completed"
    assert a.contains == ["hello"]
    assert a.contains_any == ["world", "planet"]
    assert a.regex == "he..o"
    assert a.max_tokens == 100
    assert a.max_steps == 5
    assert a.max_latency_s == 30
    assert suite.cases[1].assertions.tools_order == ["read_file"]


def test_load_json_suite_object(tmp_path: Path) -> None:
    suite = load_suite(
        _write(
            tmp_path / "s.json",
            json.dumps({"name": "j", "cases": [{"id": "a", "prompt": "hi"}]}),
        )
    )
    assert suite.name == "j"
    assert suite.cases[0].id == "a"


def test_load_json_bare_list(tmp_path: Path) -> None:
    suite = load_suite(
        _write(tmp_path / "s.json", json.dumps([{"id": "a", "prompt": "hi"}]))
    )
    assert suite.cases[0].id == "a"


def test_load_jsonl_one_case_per_line(tmp_path: Path) -> None:
    suite = load_suite(
        _write(
            tmp_path / "s.jsonl",
            '{"id":"a","prompt":"x"}\n{"id":"b","prompt":"y"}\n',
        )
    )
    assert [c.id for c in suite.cases] == ["a", "b"]


def test_strict_rejects_unknown_case_field(tmp_path: Path) -> None:
    with pytest.raises(EvalConfigError) as exc:
        load_suite(
            _write(
                tmp_path / "s.yaml",
                "cases:\n  - id: a\n    prompt: hi\n    typpo: 1\n",
            )
        )
    assert "typpo" in str(exc.value)


def test_strict_rejects_unknown_assertion_field(tmp_path: Path) -> None:
    with pytest.raises(EvalConfigError):
        load_suite(
            _write(
                tmp_path / "s.yaml",
                "cases:\n  - id: a\n    prompt: hi\n    assert:\n      bogus: 1\n",
            )
        )


def test_missing_required_case_field_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(EvalConfigError):
        load_suite(_write(tmp_path / "s.yaml", "cases:\n  - prompt: no-id\n"))


def test_repeat_defaults_to_one_and_validates(tmp_path: Path) -> None:
    suite = load_suite(
        _write(tmp_path / "s.yaml", "cases:\n  - id: a\n    prompt: hi\n    repeat: 3\n")
    )
    assert suite.cases[0].repeat == 3
    with pytest.raises(EvalConfigError):
        load_suite(
            _write(tmp_path / "b.yaml", "cases:\n  - id: a\n    prompt: hi\n    repeat: 0\n")
        )


def test_relative_prompt_file_resolves_against_suite_dir(tmp_path: Path) -> None:
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "prompt.txt").write_text("loaded from file", encoding="utf-8")
    suite = load_suite(
        _write(
            sub / "s.yaml",
            "cases:\n  - id: a\n    prompt_file: prompt.txt\n",
        )
    )
    assert suite.cases[0].prompt == "loaded from file"


def test_invalid_yaml_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(EvalConfigError):
        load_suite(_write(tmp_path / "s.yaml", "cases: [::::\n"))


def test_missing_file_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(EvalConfigError):
        load_suite(tmp_path / "does-not-exist.yaml")
