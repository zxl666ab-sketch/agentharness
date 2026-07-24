from pathlib import Path
p = Path("tests/test_cli_interactive.py")
t = p.read_text(encoding="utf-8")
t = t.replace(
    "        assert run_process.stdin is not None\n                run_process.stdin.flush()\n",
    "        assert run_process.stdin is not None\n        # auto+process shell: no interactive approval needed\n        run_process.stdin.flush()\n",
)
p.write_text(t, encoding="utf-8")
print("indent fixed")
