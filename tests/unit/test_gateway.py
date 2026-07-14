"""Tests for sentinel.gateway.GatewayConfig.from_env and resolve_model.

Focus: the explicit-provider-arg fix (settings.model.provider -> gateway),
which previously only read SENTINEL_PROVIDER and silently ignored
sentinel.yaml's model.provider, causing a provider/model mismatch (e.g. an
Anthropic client asked for a Gemini model name).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from sentinel.gateway import (
    DEFAULT_MODELS,
    DEFAULT_PROVIDER,
    GatewayConfig,
    GatewayConfigError,
    create_completion,
    resolve_model,
)


class _DummyResponseModel(BaseModel):
    value: str = "ok"


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("SENTINEL_PROVIDER", "SENTINEL_MODEL", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)


class TestProviderSelection:
    def test_no_provider_arg_no_env_defaults_to_anthropic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        config = GatewayConfig.from_env()

        assert config.provider == DEFAULT_PROVIDER == "anthropic"

    def test_env_var_selects_provider_when_no_explicit_arg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("SENTINEL_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEY", "gm-test")

        config = GatewayConfig.from_env()

        assert config.provider == "gemini"

    def test_explicit_provider_arg_overrides_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression test for the sentinel.yaml provider-wiring bug.

        Previously GatewayConfig.from_env() only consulted SENTINEL_PROVIDER,
        so a sentinel.yaml provider that disagreed with (or was set while the
        env var was absent/stale) the env var was silently ignored -- the
        wrong provider's client got built with the right-provider's model
        name. The `provider` kwarg (sourced from settings.model.provider in
        triage/extractor.py) must win over the env var.
        """
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("SENTINEL_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("GEMINI_API_KEY", "gm-test")

        config = GatewayConfig.from_env(provider="gemini")

        assert config.provider == "gemini"

    def test_explicit_provider_arg_used_when_env_var_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact scenario from the bug report: sentinel.yaml says
        provider: gemini, SENTINEL_PROVIDER is unset entirely."""
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", "gm-test")

        config = GatewayConfig.from_env(provider="gemini")

        assert config.provider == "gemini"
        assert config.model == "gemini-flash-latest"

    def test_explicit_provider_arg_is_case_and_whitespace_normalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", "gm-test")

        config = GatewayConfig.from_env(provider="  GEMINI  ")

        assert config.provider == "gemini"

    def test_none_provider_arg_falls_back_to_env_then_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        config = GatewayConfig.from_env(provider=None)

        assert config.provider == "anthropic"

    def test_empty_string_provider_arg_falls_back_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("SENTINEL_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "gq-test")

        config = GatewayConfig.from_env(provider="")

        assert config.provider == "groq"

    def test_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_provider_env(monkeypatch)

        with pytest.raises(GatewayConfigError, match="Unknown SENTINEL_PROVIDER"):
            GatewayConfig.from_env(provider="openai")


class TestApiKeyResolution:
    @pytest.mark.parametrize(
        ("provider", "key_env_var"),
        [
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("gemini", "GEMINI_API_KEY"),
            ("groq", "GROQ_API_KEY"),
        ],
    )
    def test_reads_matching_provider_key(
        self, provider: str, key_env_var: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv(key_env_var, "the-secret-key")

        config = GatewayConfig.from_env(provider=provider)

        assert config.auth_token == "the-secret-key"

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_provider_env(monkeypatch)

        with pytest.raises(GatewayConfigError, match="No API key found"):
            GatewayConfig.from_env(provider="gemini")

    def test_wrong_provider_key_present_does_not_satisfy_selected_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Having ANTHROPIC_API_KEY set must not accidentally authenticate
        a gemini request -- providers' keys are not interchangeable."""
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        with pytest.raises(GatewayConfigError, match="GEMINI_API_KEY"):
            GatewayConfig.from_env(provider="gemini")


class TestModelResolution:
    def test_default_model_used_when_sentinel_model_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", "gm-test")

        config = GatewayConfig.from_env(provider="gemini")

        assert config.model == resolve_model("gemini", DEFAULT_MODELS["gemini"])

    def test_sentinel_model_env_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("SENTINEL_MODEL", "claude-haiku")

        config = GatewayConfig.from_env(provider="anthropic")

        assert config.model == "claude-haiku-4-5-20251001"

    def test_resolve_model_resolves_known_alias(self) -> None:
        assert resolve_model("anthropic", "claude-sonnet") == "claude-sonnet-4-5-20250929"
        assert resolve_model("gemini", "gemini-flash") == "gemini-flash-latest"
        assert resolve_model("groq", "groq-llama") == "llama-3.3-70b-versatile"

    def test_resolve_model_passes_through_unknown_model_name(self) -> None:
        assert resolve_model("anthropic", "some-future-model-id") == "some-future-model-id"

    def test_resolve_model_unknown_provider_passes_through(self) -> None:
        assert resolve_model("unknown-provider", "anything") == "anything"

    def test_model_from_wrong_provider_alias_table_is_not_resolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Documents the failure mode the bug caused: if the wrong provider
        gets selected, a same-named model alias from a different provider's
        table won't resolve and is passed through unchanged (which then
        fails downstream against the wrong provider's API)."""
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("SENTINEL_MODEL", "gemini-flash")

        config = GatewayConfig.from_env(provider="anthropic")

        assert config.provider == "anthropic"
        assert config.model == "gemini-flash"  # not resolved -- not a valid Anthropic model ID


class TestBaseUrl:
    def test_base_url_unset_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        config = GatewayConfig.from_env(provider="anthropic")

        assert config.base_url is None

    def test_anthropic_base_url_env_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com")

        config = GatewayConfig.from_env(provider="anthropic")

        assert config.base_url == "https://proxy.example.com"


class TestCreateCompletion:
    """Per-provider call shape: Anthropic takes system= as a top-level kwarg,
    Gemini/Groq use an OpenAI-style messages list with a "system" role entry.
    A provider picking the wrong shape would fail against the real API but
    these tests catch it without hitting the network, by mocking the
    provider SDK client and Instructor's from_* wrapper factories."""

    def test_anthropic_uses_top_level_system_kwarg_and_from_anthropic(self) -> None:
        config = GatewayConfig(provider="anthropic", auth_token="sk-ant-test", model="claude-sonnet")

        with patch("sentinel.gateway._create_raw_client") as mock_raw_client, patch(
            "instructor.from_anthropic"
        ) as mock_from_anthropic:
            mock_client = MagicMock()
            mock_from_anthropic.return_value = mock_client

            create_completion(
                config,
                model="claude-sonnet",
                system_prompt="SYSTEM",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
            )

            mock_from_anthropic.assert_called_once_with(mock_raw_client.return_value)
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs["system"] == "SYSTEM"
            assert kwargs["messages"] == [{"role": "user", "content": "USER"}]
            assert kwargs["model"] == "claude-sonnet-4-5-20250929"

    def test_gemini_uses_openai_style_system_message_and_from_genai(self) -> None:
        config = GatewayConfig(provider="gemini", auth_token="gm-test", model="gemini-flash")

        with patch("sentinel.gateway._create_raw_client") as mock_raw_client, patch(
            "instructor.from_genai"
        ) as mock_from_genai:
            mock_client = MagicMock()
            mock_from_genai.return_value = mock_client

            create_completion(
                config,
                model="gemini-flash",
                system_prompt="SYSTEM",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
            )

            mock_from_genai.assert_called_once()
            assert mock_from_genai.call_args.args[0] is mock_raw_client.return_value
            _, kwargs = mock_client.chat.completions.create.call_args
            assert kwargs["messages"] == [
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "USER"},
            ]
            assert "system" not in kwargs
            assert kwargs["model"] == "gemini-flash-latest"

    def test_groq_uses_openai_style_system_message_and_from_groq(self) -> None:
        config = GatewayConfig(provider="groq", auth_token="gq-test", model="groq-llama")

        with patch("sentinel.gateway._create_raw_client") as mock_raw_client, patch(
            "instructor.from_groq"
        ) as mock_from_groq:
            mock_client = MagicMock()
            mock_from_groq.return_value = mock_client

            create_completion(
                config,
                model="groq-llama",
                system_prompt="SYSTEM",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
            )

            mock_from_groq.assert_called_once_with(mock_raw_client.return_value)
            _, kwargs = mock_client.chat.completions.create.call_args
            assert kwargs["messages"] == [
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "USER"},
            ]
            assert "system" not in kwargs
            assert kwargs["model"] == "llama-3.3-70b-versatile"

    def test_unknown_provider_raises(self) -> None:
        config = GatewayConfig(provider="openai", auth_token="x", model="gpt-4")

        with patch("sentinel.gateway._create_raw_client"):
            with pytest.raises(GatewayConfigError, match="Unknown provider"):
                create_completion(
                    config,
                    model="gpt-4",
                    system_prompt="SYSTEM",
                    user_prompt="USER",
                    temperature=0.0,
                    max_tokens=100,
                    max_retries=2,
                    response_model=_DummyResponseModel,
                )
