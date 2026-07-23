"""Scriptable CLI commands and exit-code behavior."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentharness.cli.main import app
from agentharness.cli.provider_defaults import resolve_default_provider
from agentharness.contracts import Checkpoint, Message, MessageRole, RunStatus
from agentharness.storage.sqlite import Storage

runner = CliRunner()


def test_resolve_default_provider_order(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert resolve_default_provider(None) == "fake"
    assert resolve_default_provider("openai") == "openai"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert resolve_default_provider(None) == "openai"
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-test")
    assert resolve_default_provider(None) == "anthropic"
    assert resolve_default_provider("fake") == "fake"


def test_run_oneshot_exits_without_entering_interactive(
    data_dir: Path, workspace: Path
) -> None:
    started = time.monotonic()
    result = runner.invoke(
        app,
        [
            "run",
            "[fake:text]cli one shot",
            "--provider",
            "fake",
            "--approval",
            "auto",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "completed" in result.output
    assert "cli one shot" in result.output
    assert time.monotonic() - started < 15


@pytest.mark.parametrize(
    ("message", "status"),
    [
        pytest.param("[fake:error:provider]", "failed", id="failed"),
        pytest.param("[fake:error:rate_limit]", "failed", id="rate-limit"),
        pytest.param("[fake:error:timeout]", "interrupted", id="interrupted"),
        pytest.param("[fake:cancel]", "cancelled", id="cancelled"),
    ],
)
def test_run_unsuccessful_result_exits_nonzero(
    message: str, status: str, data_dir: Path, workspace: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            message,
            "--provider",
            "fake",
            "--approval",
            "auto",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
    )

    assert result.exit_code != 0
    assert status in result.output


def test_resume_failed_result_exits_nonzero(data_dir: Path, workspace: Path) -> None:
    store = Storage(data_dir)
    session_id = store.create_session()
    run_id = "cli-resume-failure"
    store.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.interrupted,
        provider="fake",
        approval="auto",
        cwd=str(workspace),
    )
    store.save_checkpoint(
        Checkpoint(
            run_id=run_id,
            phase="model_turn",
            step=0,
            messages=[Message(role=MessageRole.user, content="[fake:error:provider]")],
            status=RunStatus.interrupted,
        )
    )
    store.close()

    result = runner.invoke(
        app,
        ["resume", run_id, "--approval", "auto", "--data-dir", str(data_dir)],
    )

    assert result.exit_code != 0
    assert "failed" in result.output


def test_runs_and_doctor_use_requested_data_dir(
    data_dir: Path, workspace: Path
) -> None:
    runner.invoke(
        app,
        [
            "run",
            "[fake:text]x",
            "--provider",
            "fake",
            "--approval",
            "auto",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
    )
    runs = runner.invoke(app, ["runs", "--data-dir", str(data_dir)])
    doctor = runner.invoke(app, ["doctor", "--data-dir", str(data_dir)])

    assert runs.exit_code == 0
    assert "completed" in runs.output
    assert doctor.exit_code == 0
    assert str(data_dir) in doctor.output.replace("\n", "") or "data_dir" in doctor.output


def test_removed_run_ui_option_is_a_usage_error(data_dir: Path) -> None:
    result = runner.invoke(
        app,
        ["run", "hello", "--ui", "--provider", "fake", "--data-dir", str(data_dir)],
    )

    assert result.exit_code == 2
    assert "No such option" in result.output


def test_cancel_and_resume_missing_run_fail_cleanly(data_dir: Path) -> None:
    cancelled = runner.invoke(
        app, ["cancel", "missing-run", "--data-dir", str(data_dir)]
    )
    resumed = runner.invoke(
        app, ["resume", "missing-run", "--data-dir", str(data_dir)]
    )

    for result in (cancelled, resumed):
        assert result.exit_code == 1
        assert "not found" in result.output.lower()
        assert "Traceback" not in result.output


def test_cancel_completed_run_is_rejected_without_stop_request(
    data_dir: Path, workspace: Path
) -> None:
    completed = runner.invoke(
        app,
        [
            "run",
            "[fake:text]done",
            "--provider",
            "fake",
            "--approval",
            "auto",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
    )
    assert completed.exit_code == 0
    storage = Storage(data_dir)
    run_id = storage.list_runs()[0]["id"]
    storage.close()

    cancelled = runner.invoke(app, ["cancel", run_id, "--data-dir", str(data_dir)])

    assert cancelled.exit_code == 1
    assert "completed" in cancelled.output
    assert "Traceback" not in cancelled.output
    storage = Storage(data_dir)
    try:
        assert storage.get_run(run_id)["status"] == RunStatus.completed.value
        assert storage.get_stop_request(run_id) is None
    finally:
        storage.close()
