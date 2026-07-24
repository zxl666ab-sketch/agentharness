import subprocess
from pathlib import Path
root = Path(r"D:/个人通用agentharness")
p = subprocess.run(
    [
        "uv",
        "run",
        "agentharness",
        "run",
        "--approval",
        "auto",
        "Reply with exactly: CLI_OK. No tools. Do not modify files.",
    ],
    cwd=root,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)
(root / "output/flywheel/cli-oneshot2.out.txt").write_text(p.stdout or "", encoding="utf-8")
(root / "output/flywheel/cli-oneshot2.err.txt").write_text(p.stderr or "", encoding="utf-8")
(root / "output/flywheel/cli-oneshot2.exit").write_text(str(p.returncode), encoding="utf-8")
print("done", p.returncode)
