from pathlib import Path
p = Path("tests/test_cli_interactive.py")
t = p.read_text(encoding="utf-8")
t = t.replace(
'''        input="shell echo approved-by-auto
/quit
",
''',
'        input="shell echo approved-by-auto\\n/quit\\n",\n',
)
t = t.replace(
'''        input="shell echo approved-by-user
1
/quit
",
''',
'        input="shell echo approved-by-user\\n1\\n/quit\\n",\n',
)
p.write_text(t, encoding="utf-8")
# verify syntax
import ast
ast.parse(t)
print("syntax ok")
