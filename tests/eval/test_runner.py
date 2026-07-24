"""Headless eval runner integration tests (fake provider only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentharness.eval.dataset import load_suite
from agentharness.eval.report import write_json_report
from agentharness.eval.runner import run_suite
from agentharness.harness import Harness


@pytest.mark.asyncio
async def test_run_suite_smoke_case_passes(tmp_path: Path) -> None:
    suite_path = tmp_path / "s.yaml"
    suite_path.write_text(
        """
name: unit
defaults:
  provider: fake
cases:
  - id: hello
    prompt: "[fake:text]hello eval"
    assert:
      status: completed
      contains: ["hello eval"]
""",
        encoding="utf-8",
    )
    suite = load_suite(suite_path)
    data = tmp_path / "data"
    report = await run_suite(suite, data_dir=data, concurrency=2)
    assert report.total == 1
    assert report.passed == 1
    assert report.results[0].run_id
    assert report.results[0].score == 1.0
    # data dir used for inspector deep links
    assert (data / "agentharness.db").exists() or any(data.iterdir())


@pytest.mark.asyncio
async def test_run_suite_failure_does_not_abort_suite(tmp_path: Path) -> None:
    suite_path = tmp_path / "s.yaml"
    suite_path.write_text(
        """
name: partial
defaults:
  provider: fake
cases:
  - id: good
    prompt: "[fake:text]ok"
    assert:
      status: completed
      contains: ["ok"]
  - id: bad
    prompt: "[fake:text]nope"
    assert:
      status: completed
      contains: ["must-have"]
""",
        encoding="utf-8",
    )
    report = await run_suite(load_suite(suite_path), data_dir=tmp_path / "d")
    assert report.total == 2
    assert report.passed == 1
    bad = next(r for r in report.results if r.case_id == "bad")
    assert not bad.passed
    assert bad.reasons


@pytest.mark.asyncio
async def test_repeat_expands_case_ids_and_pass_rate(tmp_path: Path) -> None:
    suite_path = tmp_path / "s.yaml"
    suite_path.write_text(
        """
name: rep
defaults:
  provider: fake
cases:
  - id: r
    prompt: "[fake:text]once"
    repeat: 3
    assert:
      status: completed
      contains: ["once"]
""",
        encoding="utf-8",
    )
    report = await run_suite(load_suite(suite_path), data_dir=tmp_path / "d")
    assert report.total == 3
    assert {r.case_id for r in report.results} == {"r#1", "r#2", "r#3"}
    assert report.pass_rate == 1.0
    assert all(r.logical_case_id == "r" for r in report.results)


@pytest.mark.asyncio
async def test_default_tmp_data_dir_does_not_touch_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    suite_path = tmp_path / "s.yaml"
    suite_path.write_text(
        """
name: tmp
cases:
  - id: a
    prompt: "[fake:text]x"
    assert:
      status: completed
""",
        encoding="utf-8",
    )
    report = await run_suite(load_suite(suite_path), concurrency=1)
    assert report.total == 1
    # default home harness dir must not be created for eval
    assert not (home / ".agentharness").exists()


@pytest.mark.asyncio
async def test_provider_resolution_case_over_cli(tmp_path: Path) -> None:
    suite_path = tmp_path / "s.yaml"
    suite_path.write_text(
        """
name: res
defaults:
  provider: anthropic
  model: default-m
cases:
  - id: a
    provider: fake
    model: case-m
    prompt: "[fake:text]hi"
    assert:
      status: completed
""",
        encoding="utf-8",
    )
    report = await run_suite(
        load_suite(suite_path),
        provider="openai",
        model="cli-m",
        data_dir=tmp_path / "d",
    )
    assert report.results[0].provider == "fake"
    assert report.results[0].model == "case-m"


@pytest.mark.asyncio
async def test_injected_harness_not_closed(tmp_path: Path) -> None:
    h = Harness(data_dir=tmp_path / "h")
    suite_path = tmp_path / "s.yaml"
    suite_path.write_text(
        'cases:\n  - id: a\n    prompt: "[fake:text]z"\n    assert:\n      status: completed\n',
        encoding="utf-8",
    )
    report = await run_suite(load_suite(suite_path), harness=h)
    assert report.passed == 1
    # still usable
    assert h.get_run(report.results[0].run_id) is not None
    await h.aclose()


@pytest.mark.asyncio
async def test_json_report_from_runner(tmp_path: Path) -> None:
    suite_path = tmp_path / "s.yaml"
    suite_path.write_text(
        'name: j\ncases:\n  - id: a\n    prompt: "[fake:text]out"\n    assert:\n      contains: ["out"]\n',
        encoding="utf-8",
    )
    report = await run_suite(load_suite(suite_path), data_dir=tmp_path / "d")
    path = write_json_report(report, tmp_path / "out.json")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert '"schema_version": 1' in text
