from pathlib import Path

p = Path("tests/test_cli_interactive.py")
t = p.read_text(encoding="utf-8")
start = t.index("def test_interactive_prompts_for_destructive_shell_approval")
end = t.index("def _process_is_alive")
new = '''def test_interactive_auto_allows_process_shell_without_prompt(
    data_dir: Path, workspace: Path
) -> None:
    """Daily-assistant default: --approval auto runs shell (process) without a prompt."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "--provider",
            "fake",
            "--approval",
            "auto",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
        input="shell echo approved-by-auto\n/quit\n",
        text=True,
        capture_output=True,
        env=_cli_env(),
        timeout=15,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "审批请求" not in output
    assert "approved-by-auto" in output
    assert "status=completed" in output


def test_interactive_ask_still_prompts_for_shell(
    data_dir: Path, workspace: Path
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "--provider",
            "fake",
            "--approval",
            "ask",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
        input="shell echo approved-by-user\n1\n/quit\n",
        text=True,
        capture_output=True,
        env=_cli_env(),
        timeout=15,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "审批请求" in output
    assert "approved-by-user" in output
    assert "status=completed" in output


'''
p.write_text(t[:start] + new + t[end:], encoding="utf-8")
print("ok")
