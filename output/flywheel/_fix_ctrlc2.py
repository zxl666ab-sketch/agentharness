from pathlib import Path
p = Path("tests/test_cli_interactive.py")
t = p.read_text(encoding="utf-8")
old1 = 'process.stdin.write(f"{command}\\n1\\n")'
new1 = 'process.stdin.write(f"{command}\\n")'
old2 = 'run_process.stdin.write("1\\n")\n'
print("has1", old1 in t)
print("has2", old2 in t)
t = t.replace(old1, new1)
# remove standalone approval for auto path; keep a blank no-op line if needed
if old2 in t:
    t = t.replace(old2, "")
p.write_text(t, encoding="utf-8")
for i, line in enumerate(t.splitlines(), 1):
    if "stdin.write" in line:
        print(i, repr(line))
