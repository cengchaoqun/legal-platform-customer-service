"""DeepSeek 接入回归：默认路由、环境密钥与真实模式隔离。"""
from unittest.mock import patch

import pytest

from atguigu.common.config import Settings
from atguigu.agent.llm.adapter import ModelAdapter, ModelConfigurationError


def test_deepseek_defaults_and_environment_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-not-a-real-key")
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "deepseek"
    assert settings.llm_model == "deepseek-flash"
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_api_key == "test-secret-not-a-real-key"
    assert settings.policy_runtime_mode == "deployed"


def test_native_tool_client_uses_non_thinking_deepseek():
    settings = Settings(_env_file=None, llm_api_key="test-key")
    with patch("atguigu.agent.llm.adapter.get_settings", return_value=settings), patch(
        "atguigu.agent.llm.adapter.ChatDeepSeek"
    ) as factory:
        ModelAdapter.create_model()
    assert factory.call_args.kwargs["model"] == "deepseek-flash"
    assert factory.call_args.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_missing_key_does_not_silently_fall_back(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    settings = Settings(_env_file=None, llm_api_key="")
    with patch("atguigu.agent.llm.adapter.get_settings", return_value=settings):
        with pytest.raises(ModelConfigurationError, match="API_KEY"):
            ModelAdapter.create_model()


def test_fixture_cannot_be_enabled_in_production():
    with pytest.raises(ValueError, match="生产环境"):
        Settings(_env_file=None, app_env="prod", policy_runtime_mode="fixture", allow_fixture_model=True)
