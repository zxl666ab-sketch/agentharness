"""S7 consecutive daily-assistant live tasks on Grok."""
from __future__ import annotations
import asyncio, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r'D:\个人通用agentharness')
sys.path.insert(0, str(ROOT / 'src'))
os.chdir(ROOT)
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    k, v = line.split('=', 1)
    os.environ[k.strip()] = v.strip()

from agentharness.contracts import ApprovalMode, RunRequest
from agentharness.harness import Harness

OUT = ROOT / 'output' / 'flywheel' / f's7-consecutive-{datetime.now().strftime("%Y%m%d-%H%M%S")}.jsonl'
SANDBOX = ROOT / 'output' / 'flywheel' / f'sandbox-s7-{datetime.now().strftime("%Y%m%d-%H%M%S")}'
SANDBOX.mkdir(parents=True, exist_ok=True)
MODEL = os.environ.get('OPENAI_MODEL') or 'grok-4.5'

def log(row):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(json.dumps(row, ensure_ascii=False)[:500], flush=True)

async def run_case(h, name, message, session_id=None, cwd=None):
    if session_id:
        session_id = h.resolve_session_id(session_id)
    req = RunRequest(
        message=message,
        session_id=session_id,
        provider='openai',
        model=MODEL,
        approval=ApprovalMode.auto,
        cwd=cwd or str(ROOT),
    )
    result = await h.run(req)
    row = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'case': name,
        'status': result.status.value if hasattr(result.status, 'value') else str(result.status),
        'run_id': result.run_id,
        'session_id': result.session_id,
        'session_len': len(result.session_id or ''),
        'error': result.error,
        'output': (result.output or '')[:600],
        'usage': result.usage.model_dump() if result.usage else None,
        'provider': 'openai',
        'model': MODEL,
    }
    log(row)
    return row

async def main():
    h = Harness()
    try:
        r1 = await run_case(h, 'T1_remember', '日常助理。请记住：我的名字是飞轮测试员，偏好简短中文回复。只回复：收到。不要改文件。')
        sid = r1['session_id']
        await run_case(h, 'T2_recall', '我的名字和偏好是什么？用一句话回答。', session_id=sid)
        await run_case(h, 'T3_read_pyproject', '用 read_file 读 pyproject.toml 里 [project] 的 name 字段值，只回复 name 的字符串。不要改文件。')
        await run_case(h, 'T4_shell', '用 shell 执行 cmd /c echo S7_SHELL_OK，只报告输出。不要删文件。', cwd=str(SANDBOX))
        await run_case(h, 'T5_note', '在当前目录写 todo.txt，内容一行：买咖啡。然后读回确认。不要改仓库其它文件。', cwd=str(SANDBOX))
        await run_case(h, 'T6_continue', '根据你记住的偏好，用一句话总结你刚才帮我做了什么。', session_id=sid)
    finally:
        await h.aclose()
    print(f'LOG={OUT}', flush=True)
    print(f'SANDBOX={SANDBOX}', flush=True)

if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
