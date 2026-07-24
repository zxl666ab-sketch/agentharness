"""CLI `eval` command via Typer CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentharness.cli.main import app
from agentharness.storage.sqlite import Storage

runner = CliRunner()


def _write_suite(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_eval_help() -> None:
    result = runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == 0
    assert "--report-json" in result.output
    assert "--baseline" in result.output
    assert "--fail-on-regression" in result.output


def test_eval_smoke_offline_json_and_junit(tmp_path: Path) -> None:
    suite = _write_suite(
        tmp_path / "s.yaml",
        """
name: cli-smoke
defaults:
  provider: fake
cases:
  - id: hello
    prompt: "[fake:text]cli hello"
    assert:
      status: completed
      contains: ["cli hello"]
""",
    )
    out_json = tmp_path / "r.json"
    out_xml = tmp_path / "r.xml"
    data = tmp_path / "data"
    result = runner.invoke(
        app,
        [
            "eval",
            str(suite),
            "--report-json",
            str(out_json),
            "--report-junit",
            str(out_xml),
            "--data-dir",
            str(data),
            "--concurrency",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_json.exists()
    assert out_xml.exists()
    data_json = json.loads(out_json.read_text(encoding="utf-8"))
    assert data_json["schema_version"] == 1
    assert data_json["passed"] == data_json["total"]
    assert "report" in result.output.lower() or str(out_json) in result.output


def test_eval_case_failure_exit_1(tmp_path: Path) -> None:
    suite = _write_suite(
        tmp_path / "s.yaml",
        """
name: fail
cases:
  - id: bad
    prompt: "[fake:text]x"
    assert:
      contains: ["never-match"]
""",
    )
    result = runner.invoke(app, ["eval", str(suite), "--data-dir", str(tmp_path / "d")])
    assert result.exit_code == 1, result.output


def test_eval_bad_suite_exit_2(tmp_path: Path) -> None:
    suite = _write_suite(
        tmp_path / "s.yaml",
        "cases:\n  - id: a\n    prompt: hi\n    typpo: 1\n",
    )
    result = runner.invoke(app, ["eval", str(suite)])
    assert result.exit_code == 2, result.output


def test_eval_missing_baseline_exit_2(tmp_path: Path) -> None:
    suite = _write_suite(
        tmp_path / "s.yaml",
        'cases:\n  - id: a\n    prompt: "[fake:text]ok"\n    assert:\n      status: completed\n',
    )
    result = runner.invoke(
        app,
        [
            "eval",
            str(suite),
            "--baseline",
            str(tmp_path / "missing.json"),
            "--data-dir",
            str(tmp_path / "d"),
        ],
    )
    assert result.exit_code == 2, result.output


def test_eval_fail_on_regression(tmp_path: Path) -> None:
    suite = _write_suite(
        tmp_path / "s.yaml",
        """
name: reg
cases:
  - id: a
    prompt: "[fake:text]now"
    assert:
      contains: ["now"]
  - id: b
    prompt: "[fake:text]x"
    assert:
      contains: ["never"]
""",
    )
    # baseline: both passed historically
    baseline = {
        "schema_version": 1,
        "suite": "reg",
        "total": 2,
        "passed": 2,
        "pass_rate": 1.0,
        "mean_score": 1.0,
        "total_tokens": 20,
        "mean_latency_s": 0.1,
        "results": [
            {
                "case_id": "a",
                "passed": True,
                "score": 1.0,
                "total_tokens": 10,
                "latency_s": 0.1,
                "status": "completed",
                "run_id": "old-a",
                "steps": 1,
                "reasons": [],
            },
            {
                "case_id": "b",
                "passed": True,
                "score": 1.0,
                "total_tokens": 10,
                "latency_s": 0.1,
                "status": "completed",
                "run_id": "old-b",
                "steps": 1,
                "reasons": [],
            },
        ],
    }
    bp = tmp_path / "base.json"
    bp.write_text(json.dumps(baseline), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "eval",
            str(suite),
            "--baseline",
            str(bp),
            "--fail-on-regression",
            "--data-dir",
            str(tmp_path / "d"),
        ],
    )
    # case failure already exit 1; regression also exit 1
    assert result.exit_code == 1, result.output
    storage = Storage(tmp_path / "d")
    try:
        failed = next(
            row
            for row in storage.list_runs(limit=20)
            if "[fake:text]x" in str(row.get("user_summary"))
        )
        metadata = json.loads(failed["metadata_json"])
        retained = metadata["evaluation"]["regression"]
        assert retained["gate_decision"]["passed"] is False
        assert retained["gate_decision"]["exit_code"] == 1
        assert retained["baseline_diff"]["score_delta"] < 0
    finally:
        storage.close()
