"""Tests for sentinel.gateway: provider selection, model resolution, the
automatic fallback chain, and per-provider completion call shapes.

Focus: the explicit-provider-arg fix (settings.model.provider -> gateway),
which previously only read SENTINEL_PROVIDER and silently ignored
sentinel.yaml's model.provider, causing a provider/model mismatch (e.g. an
Anthropic client asked for a Gemini model name) -- plus the automatic
multi-provider fallback chain (Together AI -> Groq -> Gemini -> Anthropic)
added on top of that single-provider resolution.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from sentinel.gateway import (
    DEFAULT_MODELS,
    DEFAULT_PROVIDER,
    PROVIDER_PRIORITY,
    GatewayConfig,
    GatewayConfigError,
    create_completion,
    create_completion_with_fallback,
    resolve_model,
)


class _DummyResponseModel(BaseModel):
    value: str = "ok"


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("SENTINEL_PROVIDER", "SENTINEL_MODEL", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for key in (
        "TOGETHER_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    for key in ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"):
        monkeypatch.delenv(key, raising=False)


class TestProviderSelection:
    def test_no_provider_arg_no_env_defaults_to_together(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")

        config = GatewayConfig.from_env()

        assert config.provider == DEFAULT_PROVIDER == "together"

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
        monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")

        config = GatewayConfig.from_env(provider=None)

        assert config.provider == "together"

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
            ("together", "TOGETHER_API_KEY"),
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
        assert (
            resolve_model("anthropic", "claude-sonnet") == "claude-sonnet-4-5-20250929"
        )
        assert resolve_model("gemini", "gemini-flash") == "gemini-flash-latest"
        assert resolve_model("groq", "groq-llama") == "llama-3.3-70b-versatile"
        assert (
            resolve_model("together", "together-qwen-7b")
            == "Qwen/Qwen2.5-7B-Instruct-Turbo"
        )

    def test_resolve_model_passes_through_unknown_model_name(self) -> None:
        assert (
            resolve_model("anthropic", "some-future-model-id") == "some-future-model-id"
        )

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
        assert (
            config.model == "gemini-flash"
        )  # not resolved -- not a valid Anthropic model ID


class TestBaseUrl:
    def test_base_url_unset_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        config = GatewayConfig.from_env(provider="anthropic")

        assert config.base_url is None

    def test_anthropic_base_url_env_applied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com")

        config = GatewayConfig.from_env(provider="anthropic")

        assert config.base_url == "https://proxy.example.com"


class TestChainFromEnv:
    """GatewayConfig.chain_from_env() builds the automatic fallback chain."""

    def test_only_configured_providers_are_included(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        chain = GatewayConfig.chain_from_env()

        assert [c.provider for c in chain] == ["together", "anthropic"]

    def test_default_priority_order_is_together_groq_gemini_anthropic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
        monkeypatch.setenv("GROQ_API_KEY", "gq-test")
        monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")

        chain = GatewayConfig.chain_from_env()

        assert [c.provider for c in chain] == list(PROVIDER_PRIORITY)
        assert PROVIDER_PRIORITY == ("together", "groq", "gemini", "anthropic")

    def test_primary_provider_is_moved_to_front(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")

        chain = GatewayConfig.chain_from_env(primary_provider="anthropic")

        assert [c.provider for c in chain] == ["anthropic", "together"]

    def test_primary_provider_without_key_is_skipped_not_errored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pinning a provider that has no key configured shouldn't hard-fail
        the whole chain -- fall through to whatever else is configured."""
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", "gq-test")

        chain = GatewayConfig.chain_from_env(primary_provider="anthropic")

        assert [c.provider for c in chain] == ["groq"]

    def test_no_keys_configured_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_provider_env(monkeypatch)

        with pytest.raises(
            GatewayConfigError, match="No API key found for any provider"
        ):
            GatewayConfig.chain_from_env()

    def test_unknown_primary_provider_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")

        with pytest.raises(GatewayConfigError, match="Unknown SENTINEL_PROVIDER"):
            GatewayConfig.chain_from_env(primary_provider="openai")

    def test_model_override_belonging_to_other_provider_is_ignored_per_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SENTINEL_MODEL=claude-sonnet is only meaningful for the Anthropic
        entry -- other providers in the chain must use their own default
        rather than sending "claude-sonnet" to Together/Groq/Gemini's API."""
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("SENTINEL_MODEL", "claude-sonnet")

        chain = GatewayConfig.chain_from_env()
        by_provider = {c.provider: c.model for c in chain}

        assert by_provider["anthropic"] == "claude-sonnet-4-5-20250929"
        assert by_provider["together"] == resolve_model(
            "together", DEFAULT_MODELS["together"]
        )

    def test_model_override_applies_when_it_belongs_to_the_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")
        monkeypatch.setenv("SENTINEL_MODEL", "together-llama-70b")

        chain = GatewayConfig.chain_from_env()

        assert chain[0].model == "meta-llama/Llama-3.3-70B-Instruct-Turbo"


class TestCreateCompletion:
    """Per-provider call shape: Anthropic takes system= as a top-level kwarg,
    Gemini/Groq/Together use an OpenAI-style messages list with a "system"
    role entry. A provider picking the wrong shape would fail against the
    real API but these tests catch it without hitting the network, by
    mocking the provider SDK client and Instructor's from_* wrapper
    factories."""

    def test_anthropic_uses_top_level_system_kwarg_and_from_anthropic(self) -> None:
        config = GatewayConfig(
            provider="anthropic", auth_token="sk-ant-test", model="claude-sonnet"
        )

        with (
            patch("sentinel.gateway._create_raw_client") as mock_raw_client,
            patch("instructor.from_anthropic") as mock_from_anthropic,
        ):
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
        config = GatewayConfig(
            provider="gemini", auth_token="gm-test", model="gemini-flash"
        )

        with (
            patch("sentinel.gateway._create_raw_client") as mock_raw_client,
            patch("instructor.from_genai") as mock_from_genai,
        ):
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
        config = GatewayConfig(
            provider="groq", auth_token="gq-test", model="groq-llama"
        )

        with (
            patch("sentinel.gateway._create_raw_client") as mock_raw_client,
            patch("instructor.from_groq") as mock_from_groq,
        ):
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

    def test_together_uses_openai_style_system_message_and_from_openai(self) -> None:
        config = GatewayConfig(
            provider="together", auth_token="tg-test", model="together-qwen-7b"
        )

        with (
            patch("sentinel.gateway._create_raw_client") as mock_raw_client,
            patch("instructor.from_openai") as mock_from_openai,
        ):
            mock_client = MagicMock()
            mock_from_openai.return_value = mock_client

            create_completion(
                config,
                model="together-qwen-7b",
                system_prompt="SYSTEM",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
            )

            mock_from_openai.assert_called_once_with(mock_raw_client.return_value)
            _, kwargs = mock_client.chat.completions.create.call_args
            assert kwargs["messages"] == [
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "USER"},
            ]
            assert "system" not in kwargs
            assert kwargs["model"] == "Qwen/Qwen2.5-7B-Instruct-Turbo"

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


class TestCreateCompletionWithFallback:
    """create_completion_with_fallback() walks the chain on any exception
    from create_completion() and only raises once every provider has failed.
    Caching is exercised separately in TestCaching -- these tests run with
    Upstash env vars cleared so caching is a no-op."""

    def _chain(self, *providers: str) -> list[GatewayConfig]:
        return [
            GatewayConfig(provider=p, auth_token=f"{p}-key", model=f"{p}-model")
            for p in providers
        ]

    def test_primary_success_never_touches_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        chain = self._chain("together", "groq")
        expected = _DummyResponseModel(value="from-together")

        with patch(
            "sentinel.gateway.create_completion", return_value=expected
        ) as mock_create:
            result = create_completion_with_fallback(
                chain,
                system_prompt="SYS",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
                use_cache=False,
            )

        assert result is expected
        assert mock_create.call_count == 1
        assert mock_create.call_args.args[0].provider == "together"

    def test_primary_failure_falls_through_to_next_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        chain = self._chain("together", "groq", "anthropic")
        expected = _DummyResponseModel(value="from-groq")

        with patch(
            "sentinel.gateway.create_completion",
            side_effect=[RuntimeError("together rate limited"), expected],
        ) as mock_create:
            result = create_completion_with_fallback(
                chain,
                system_prompt="SYS",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
                use_cache=False,
            )

        assert result is expected
        assert mock_create.call_count == 2
        assert [c.args[0].provider for c in mock_create.call_args_list] == [
            "together",
            "groq",
        ]

    def test_any_exception_type_triggers_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The broad except is deliberate -- a rate limit, timeout, or
        connection error from any of the four different provider SDKs (each
        with its own exception hierarchy) must all trigger the same
        'try the next provider' response."""
        _clear_provider_env(monkeypatch)
        chain = self._chain("together", "groq")
        expected = _DummyResponseModel(value="ok")

        for exc in (
            TimeoutError("timeout"),
            ConnectionError("conn"),
            ValueError("bad response"),
        ):
            with patch(
                "sentinel.gateway.create_completion", side_effect=[exc, expected]
            ):
                result = create_completion_with_fallback(
                    chain,
                    system_prompt="SYS",
                    user_prompt="USER",
                    temperature=0.0,
                    max_tokens=100,
                    max_retries=2,
                    response_model=_DummyResponseModel,
                    use_cache=False,
                )
            assert result is expected

    def test_all_providers_failing_raises_gateway_config_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        chain = self._chain("together", "groq")

        with patch(
            "sentinel.gateway.create_completion",
            side_effect=[RuntimeError("together down"), RuntimeError("groq down")],
        ):
            with pytest.raises(
                GatewayConfigError, match="All providers in the chain failed"
            ):
                create_completion_with_fallback(
                    chain,
                    system_prompt="SYS",
                    user_prompt="USER",
                    temperature=0.0,
                    max_tokens=100,
                    max_retries=2,
                    response_model=_DummyResponseModel,
                    use_cache=False,
                )

    def test_empty_chain_raises_immediately(self) -> None:
        with pytest.raises(GatewayConfigError, match="non-empty chain"):
            create_completion_with_fallback(
                [],
                system_prompt="SYS",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
                use_cache=False,
            )


class TestCaching:
    """Upstash caching in create_completion_with_fallback(). Upstash's REST
    API is mocked at the httpx layer, matching the pattern used for the
    provider SDK mocks above -- no real network calls."""

    def test_cache_disabled_when_upstash_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        chain = [GatewayConfig(provider="together", auth_token="k", model="m")]
        expected = _DummyResponseModel(value="ok")

        with (
            patch("sentinel.gateway.create_completion", return_value=expected),
            patch("sentinel.gateway.httpx.post") as mock_post,
        ):
            create_completion_with_fallback(
                chain,
                system_prompt="SYS",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
            )

        mock_post.assert_not_called()

    def test_cache_hit_returns_cached_value_without_calling_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://cache.example.com")
        monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "upstash-token")
        chain = [GatewayConfig(provider="together", auth_token="k", model="m")]
        cached_json = _DummyResponseModel(value="cached").model_dump_json()

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": cached_json}
        mock_response.raise_for_status.return_value = None

        with (
            patch(
                "sentinel.gateway.httpx.post", return_value=mock_response
            ) as mock_post,
            patch("sentinel.gateway.create_completion") as mock_create,
        ):
            result = create_completion_with_fallback(
                chain,
                system_prompt="SYS",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
            )

        assert result.value == "cached"
        mock_create.assert_not_called()
        assert mock_post.call_count == 1

    def test_successful_primary_response_is_cached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://cache.example.com")
        monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "upstash-token")
        chain = [GatewayConfig(provider="together", auth_token="k", model="m")]
        expected = _DummyResponseModel(value="fresh")

        mock_get_response = MagicMock()
        mock_get_response.json.return_value = {"result": None}
        mock_get_response.raise_for_status.return_value = None

        with (
            patch(
                "sentinel.gateway.httpx.post", return_value=mock_get_response
            ) as mock_post,
            patch("sentinel.gateway.create_completion", return_value=expected),
        ):
            create_completion_with_fallback(
                chain,
                system_prompt="SYS",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
            )

        # First call is the cache GET, second is the SETEX write-back.
        assert mock_post.call_count == 2
        setex_call = mock_post.call_args_list[1]
        assert setex_call.kwargs["json"][0] == "SETEX"

    def test_fallback_response_is_not_cached_under_primary_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only cache a response that actually came from the primary
        provider -- caching a fallback's (possibly lower-quality) answer
        under the primary's cache key would serve it as if it were the
        primary's own response on the next identical request."""
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://cache.example.com")
        monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "upstash-token")
        chain = [
            GatewayConfig(provider="together", auth_token="k1", model="m1"),
            GatewayConfig(provider="groq", auth_token="k2", model="m2"),
        ]
        expected = _DummyResponseModel(value="from-fallback")

        mock_get_response = MagicMock()
        mock_get_response.json.return_value = {"result": None}
        mock_get_response.raise_for_status.return_value = None

        with (
            patch(
                "sentinel.gateway.httpx.post", return_value=mock_get_response
            ) as mock_post,
            patch(
                "sentinel.gateway.create_completion",
                side_effect=[RuntimeError("together down"), expected],
            ),
        ):
            create_completion_with_fallback(
                chain,
                system_prompt="SYS",
                user_prompt="USER",
                temperature=0.0,
                max_tokens=100,
                max_retries=2,
                response_model=_DummyResponseModel,
            )

        # Only the cache GET should have happened -- no SETEX, since the
        # fallback (groq), not the primary (together), produced the result.
        assert mock_post.call_count == 1
