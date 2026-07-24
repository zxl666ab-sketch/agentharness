"""Stable JSON + JUnit XML report writers for eval suites."""

from __future__ import annotations

import html
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

from agentharness.eval.runner import SuiteReport

SCHEMA_VERSION = 1


def suite_report_to_dict(report: SuiteReport) -> dict[str, Any]:
    """Serialize a SuiteReport to a stable, ordered dict suitable as baseline."""
    groups = []
    for g in report.groups:
        groups.append(
            OrderedDict(
                [
                    ("provider", g.provider),
                    ("model", g.model),
                    ("total", g.total),
                    ("passed", g.passed),
                    ("pass_rate", round(g.pass_rate, 4)),
                    ("mean_score", round(g.mean_score, 4)),
                    ("avg_latency_s", round(g.avg_latency_s, 3)),
                    ("avg_tokens", round(g.avg_tokens, 1)),
                    ("avg_steps", round(g.avg_steps, 2)),
                ]
            )
        )

    results = []
    for r in report.results:
        results.append(
            OrderedDict(
                [
                    ("case_id", r.case_id),
                    ("logical_case_id", r.logical_case_id or r.case_id),
                    ("run_id", r.run_id),
                    ("status", r.status),
                    ("passed", r.passed),
                    ("score", round(float(r.score), 4)),
                    ("latency_s", round(float(r.latency_s), 3)),
                    ("input_tokens", int(r.input_tokens)),
                    ("output_tokens", int(r.output_tokens)),
                    ("total_tokens", int(r.total_tokens)),
                    ("steps", int(r.steps)),
                    ("reasons", list(r.reasons)),
                    ("provider", r.provider),
                    ("model", r.model),
                    ("tags", list(r.tags)),
                ]
            )
        )

    payload: OrderedDict[str, Any] = OrderedDict(
        [
            ("schema_version", SCHEMA_VERSION),
            ("suite", report.suite),
            ("total", report.total),
            ("passed", report.passed),
            ("pass_rate", round(report.pass_rate, 4)),
            ("mean_score", round(report.mean_score, 4)),
            ("total_tokens", report.total_tokens),
            ("mean_latency_s", round(report.mean_latency_s, 3)),
            ("data_dir", report.data_dir),
            ("groups", groups),
            ("results", results),
        ]
    )
    return payload


def write_json_report(report: SuiteReport, path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = suite_report_to_dict(report)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def write_junit_xml(report: SuiteReport, path: str | Path) -> Path:
    """GitHub Actions-friendly JUnit: one testcase per expanded case."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    failures = sum(1 for r in report.results if not r.passed)
    suite_name = html.escape(report.suite or "eval")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<testsuite name="{suite_name}" tests="{report.total}" '
            f'failures="{failures}" errors="0" '
            f'time="{report.mean_latency_s * max(report.total, 1):.3f}">'
        ),
    ]
    for r in report.results:
        name = html.escape(r.case_id)
        classname = html.escape(r.provider or "eval")
        time_s = f"{float(r.latency_s):.3f}"
        lines.append(
            f'  <testcase classname="{classname}" name="{name}" time="{time_s}">'
        )
        if not r.passed:
            msg = html.escape("; ".join(r.reasons) or r.status or "failed")
            body = html.escape("\n".join(r.reasons) or r.status or "failed")
            lines.append(f'    <failure message="{msg}">{body}</failure>')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
