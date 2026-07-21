"""Tests for sentinel.triage.extractor.extract_incident.

extract_incident is a thin wiring layer over
GatewayConfig.chain_from_env() + create_completion_with_fallback() --
these tests verify the wiring (what gets passed where, and how the
explicit `model`/`api_key` args are applied to just the primary chain
entry), not the LLM call itself. Both dependencies are patched at their
sentinel.triage.extractor import site, matching the patching style already
used in tests/unit/test_gateway.py.
"""

from __future__ import annotations

from unittest.mock import patch

from sentinel.gateway import GatewayConfig
from sentinel.triage.extractor import LLMIncidentExtraction, extract_incident
from tests.unit.triage.fakes import make_extraction


def _fake_chain() -> list[GatewayConfig]:
    return [
        GatewayConfig(
            provider="anthropic", auth_token="original-key", model="claude-sonnet"
        ),
        GatewayConfig(provider="groq", auth_token="groq-key", model="groq-llama"),
    ]


class TestGatewayChainWiring:
    def test_provider_arg_forwarded_to_chain_from_env(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ) as mock_chain_from_env,
            patch("sentinel.triage.extractor.create_completion_with_fallback"),
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
                provider="gemini",
            )

        mock_chain_from_env.assert_called_once_with(primary_provider="gemini")

    def test_provider_none_by_default(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ) as mock_chain_from_env,
            patch("sentinel.triage.extractor.create_completion_with_fallback"),
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
            )

        mock_chain_from_env.assert_called_once_with(primary_provider=None)

    def test_fallback_entries_are_passed_through_unchanged(self) -> None:
        """Only the primary (chain[0]) entry should be rebuilt with the
        explicit model/api_key -- fallback providers keep their own
        env-resolved config."""
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ),
            patch(
                "sentinel.triage.extractor.create_completion_with_fallback"
            ) as mock_create,
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
            )

        chain_arg = mock_create.call_args.args[0]
        assert len(chain_arg) == 2
        assert chain_arg[1].provider == "groq"
        assert chain_arg[1].auth_token == "groq-key"
        assert chain_arg[1].model == "groq-llama"


class TestApiKeyOverride:
    def test_api_key_override_replaces_primary_auth_token(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ),
            patch(
                "sentinel.triage.extractor.create_completion_with_fallback"
            ) as mock_create,
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
                api_key="override-key",
            )

        assert mock_create.call_args.args[0][0].auth_token == "override-key"

    def test_no_api_key_leaves_primary_auth_token_untouched(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ),
            patch(
                "sentinel.triage.extractor.create_completion_with_fallback"
            ) as mock_create,
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
            )

        assert mock_create.call_args.args[0][0].auth_token == "original-key"

    def test_empty_string_api_key_does_not_override(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ),
            patch(
                "sentinel.triage.extractor.create_completion_with_fallback"
            ) as mock_create,
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
                api_key="",
            )

        assert mock_create.call_args.args[0][0].auth_token == "original-key"


class TestModelResolution:
    def test_explicit_model_resolved_against_primary_providers_alias_table(
        self,
    ) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ),
            patch(
                "sentinel.triage.extractor.create_completion_with_fallback"
            ) as mock_create,
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-haiku",
                temperature=0.0,
                max_tokens=100,
            )

        assert mock_create.call_args.args[0][0].model == "claude-haiku-4-5-20251001"


class TestCreateCompletionWithFallbackWiring:
    def test_forwards_all_call_args_and_response_model(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ),
            patch(
                "sentinel.triage.extractor.create_completion_with_fallback"
            ) as mock_create,
        ):
            extract_incident(
                system_prompt="SYSTEM PROMPT",
                user_prompt="USER PROMPT",
                model="claude-sonnet",
                temperature=0.5,
                max_tokens=2048,
                max_retries=4,
                use_cache=False,
            )

            _, kwargs = mock_create.call_args
            assert kwargs["system_prompt"] == "SYSTEM PROMPT"
            assert kwargs["user_prompt"] == "USER PROMPT"
            assert kwargs["temperature"] == 0.5
            assert kwargs["max_tokens"] == 2048
            assert kwargs["max_retries"] == 4
            assert kwargs["response_model"] is LLMIncidentExtraction
            assert kwargs["use_cache"] is False

    def test_default_max_retries_is_two(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ),
            patch(
                "sentinel.triage.extractor.create_completion_with_fallback"
            ) as mock_create,
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
            )

            assert mock_create.call_args.kwargs["max_retries"] == 2

    def test_default_use_cache_is_true(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ),
            patch(
                "sentinel.triage.extractor.create_completion_with_fallback"
            ) as mock_create,
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
            )

            assert mock_create.call_args.kwargs["use_cache"] is True

    def test_returns_create_completion_with_fallback_result_unchanged(self) -> None:
        sentinel_result = object()
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.chain_from_env",
                return_value=_fake_chain(),
            ),
            patch(
                "sentinel.triage.extractor.create_completion_with_fallback",
                return_value=sentinel_result,
            ),
        ):
            result = extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
            )

        assert result is sentinel_result


class TestExtractionSchema:
    """The LLM-filled schema now carries proposed_remediation (Week 5)."""

    def test_proposed_remediation_defaults_to_none(self) -> None:
        assert make_extraction().proposed_remediation is None

    def test_proposed_remediation_round_trips(self) -> None:
        extraction = make_extraction(
            proposed_remediation="Restart checkout pods and drain the queue per runbook rb-1."
        )

        assert extraction.proposed_remediation == (
            "Restart checkout pods and drain the queue per runbook rb-1."
        )
