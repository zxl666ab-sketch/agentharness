"""内部 token 空值绕过/弱默认修复 + 脱敏 sentinel + 密钥落盘治理的回归测试。"""

from __future__ import annotations

import json

import httpx
import pytest

from agentharness.api.internal_agent import (
    _load_model_config,
    _model_config_path,
    _read_model_config,
    _write_model_config,
)
from agentharness.api.server import create_app
from agentharness.config import resolve_internal_token
from agentharness.harness import Harness
from agentharness.security.redaction import Redactor

VALID_TOKEN = "unit-test-token-" + ("x" * 40)
ENV_KEY = "sk-env-unit-key-abcdef1234567890abcdef1234567890"


# ---------------------------------------------------------------------------
# resolve_internal_token
# ---------------------------------------------------------------------------


def _reset_random_token_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentharness import config as config_module

    monkeypatch.setattr(config_module, "_RANDOM_INTERNAL_TOKEN", None)


def test_resolve_token_blank_env_generates_stable_random(monkeypatch) -> None:
    _reset_random_token_cache(monkeypatch)
    monkeypatch.delenv("AGENT_INTERNAL_TOKEN", raising=False)
    first = resolve_internal_token()
    second = resolve_internal_token()
    assert len(first) >= 32
    assert first == second  # 进程内缓存：两处调用点取到同一个值


def test_resolve_token_whitespace_env_never_authenticates(monkeypatch) -> None:
    _reset_random_token_cache(monkeypatch)
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "   ")
    token = resolve_internal_token()
    assert token.strip() != ""
    assert len(token) >= 32


def test_resolve_token_short_env_fails_fast(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "short-token")
    with pytest.raises(RuntimeError, match="32"):
        resolve_internal_token()


def test_resolve_token_valid_env_passthrough(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", VALID_TOKEN)
    assert resolve_internal_token() == VALID_TOKEN


# ---------------------------------------------------------------------------
# create_app internal-only 端点行为
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_only_rejects_empty_token_header(tmp_path, monkeypatch) -> None:
    """空 env 值绝不允许空 token 头通过认证（旧实现 compare_digest("", "") 恒真）。"""
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "")
    app = create_app(data_dir=tmp_path / "runtime", internal_only=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/api/sessions")).status_code == 401
        empty = await client.get("/api/sessions", headers={"X-Agent-Internal-Token": ""})
        assert empty.status_code == 401


@pytest.mark.asyncio
async def test_internal_only_unset_env_closes_internal_surface(
    tmp_path, monkeypatch
) -> None:
    """env 未设置时回退进程内随机 token：猜测与默认值都进不来，持有者可用。"""
    from agentharness import config as config_module

    monkeypatch.delenv("AGENT_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr(config_module, "_RANDOM_INTERNAL_TOKEN", None)
    app = create_app(data_dir=tmp_path / "runtime", internal_only=True)
    ephemeral = resolve_internal_token()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        assert (await client.get("/api/sessions")).status_code == 401
        guessed = await client.get(
            "/api/sessions", headers={"X-Agent-Internal-Token": "development-only-change-me"}
        )
        assert guessed.status_code == 401
        authorized = await client.get(
            "/api/sessions", headers={"X-Agent-Internal-Token": ephemeral}
        )
        assert authorized.status_code == 200


def test_create_app_short_token_fails_fast(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "tiny")
    with pytest.raises(RuntimeError, match="32"):
        create_app(data_dir=tmp_path / "runtime", internal_only=True)


# ---------------------------------------------------------------------------
# 脱敏：sk-proj- 形态 + sentinel 注册
# ---------------------------------------------------------------------------


def test_redact_covers_sk_proj_key_form() -> None:
    redactor = Redactor()
    text = 'config {"api_key": "sk-proj-abc123XYZ456ghi789jklmnop"} done'
    out = redactor.redact_text(text)
    assert "sk-proj-" not in out
    assert "[REDACTED_API_KEY]" in out


def test_redact_still_covers_classic_sk_key() -> None:
    redactor = Redactor()
    out = redactor.redact_text("bearer sk-ABCDEF0123456789abcdef0123456789 end")
    assert "sk-ABCDEF0123456789" not in out
    assert "[REDACTED_API_KEY]" in out


def test_redact_sentinel_replaces_registered_secret() -> None:
    redactor = Redactor()
    secret = "unit-sentinel-secret-0123456789"
    redactor.add_sentinel(secret)
    out = redactor.redact_text(f"echo {secret} end")
    assert secret not in out
    assert "[REDACTED_SENTINEL]" in out


def test_harness_registers_env_credentials_as_sentinels(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    harness = Harness(data_dir=tmp_path / "runtime")
    try:
        out = harness.redactor.redact_text(f"leak {ENV_KEY} end")
        assert ENV_KEY not in out
        assert "[REDACTED_SENTINEL]" in out
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# 模型配置：密钥不落盘（env 引用形态）+ 旧格式兼容
# ---------------------------------------------------------------------------


def test_write_model_config_env_key_not_persisted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    _write_model_config(tmp_path, {"provider": "openai", "model": "unit-model"})
    raw = _model_config_path(tmp_path).read_text(encoding="utf-8")
    assert ENV_KEY not in raw
    assert '"api_key_from_env": true' in raw
    loaded = _load_model_config(tmp_path)
    assert loaded["api_key"] == ENV_KEY  # 运行时从 env 回填，行为不变
    view = _read_model_config(tmp_path)
    assert view["api_key_configured"] is True
    assert ENV_KEY not in json.dumps(view)


def test_model_config_legacy_raw_key_still_loads(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = _model_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"provider": "openai", "model": "legacy", "api_key": "sk-legacy-key-1234567890"}),
        encoding="utf-8",
    )
    loaded = _load_model_config(tmp_path)
    assert loaded["api_key"] == "sk-legacy-key-1234567890"
    # 再次写入时归一化（P-M9）：旧格式里的明文 key 不再保留，只留 env 引用标记
    _write_model_config(tmp_path, {"provider": "openai", "model": "legacy"})
    raw = path.read_text(encoding="utf-8")
    assert "api_key_from_env" in raw
    assert "sk-legacy-key-1234567890" not in raw
    assert '"api_key_from_env": true' in raw


def test_write_model_config_key_shaped_custom_key_never_reaches_disk(
    tmp_path, monkeypatch
) -> None:
    """P-M9：任何「看起来像密钥」的 api_key 都只落 env 引用标记，明文绝不写盘。

    旧断言（自定义 key 明文落盘 + 0600 兜底）已被推翻：Windows 上 chmod 600
    不生效，而注释承诺的是「明文密钥绝不落盘」。
    """
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    custom = "sk-custom-explicit-key-1234567890abcdef"
    _write_model_config(tmp_path, {"provider": "openai", "model": "m", "api_key": custom})
    raw = _model_config_path(tmp_path).read_text(encoding="utf-8")
    assert custom not in raw
    assert ENV_KEY not in raw
    assert '"api_key_from_env": true' in raw
    # 归一化后运行期回落到 env key（安全优先于便利：真实 key 需要配在 .env）
    assert _load_model_config(tmp_path)["api_key"] == ENV_KEY


def test_write_model_config_non_secret_placeholder_still_persists(
    tmp_path, monkeypatch
) -> None:
    """只有密钥形状的值才被改写；短占位串保持原样（引用标记关闭）。"""
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    _write_model_config(tmp_path, {"provider": "openai", "model": "m", "api_key": "inline"})
    raw = _model_config_path(tmp_path).read_text(encoding="utf-8")
    assert '"api_key": "inline"' in raw
    assert '"api_key_from_env": false' in raw
