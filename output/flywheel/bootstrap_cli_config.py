"""Bootstrap ~/.agentharness/cli_config from project .env (no key printing)."""
from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(r'D:\个人通用agentharness')
sys.path.insert(0, str(ROOT / 'src'))
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    k, v = line.split('=', 1)
    os.environ.setdefault(k.strip(), v.strip())

from agentharness.cli.config_store import create_profile, resolve_runtime_settings, update_provider_fields

data_dir = Path.home() / '.agentharness'
key = os.environ.get('OPENAI_API_KEY')
base = os.environ.get('OPENAI_BASE_URL')
model = os.environ.get('OPENAI_MODEL') or 'grok-4.5'
if not key:
    raise SystemExit('missing OPENAI_API_KEY in .env')

create_profile(data_dir, 'grok', provider='openai', model=model, api_key=key, base_url=base)
update_provider_fields(data_dir, 'openai', api_key=key, base_url=base, model=model, set_active=True)
settings = resolve_runtime_settings(data_dir)
print('provider=', settings.provider)
print('model=', settings.model)
print('base_url=', settings.base_url)
print('api_key_set=', bool(settings.api_key))
print('source=', settings.source)
print('profile=', settings.profile)
