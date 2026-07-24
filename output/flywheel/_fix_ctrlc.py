from pathlib import Path
p = Path("tests/test_cli_interactive.py")
t = p.read_text(encoding="utf-8")
# under auto, shell no longer needs the "1" approval choice
t = t.replace('process.stdin.write(f"{command}\n1\n")', 'process.stdin.write(f"{command}\n")')
# if a separate approval line remains for auto shell starts, drop lone 1 before waiting for pid
t = t.replace('run_process.stdin.write("1\n")\n', '')
p.write_text(t, encoding="utf-8")
print("patched ctrl-c inputs")
# show remaining 1\n writes
for i, line in enumerate(t.splitlines(), 1):
    if "stdin.write" in line:
        print(i, line.strip())
