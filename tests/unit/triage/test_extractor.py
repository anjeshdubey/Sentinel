"""Tests for sentinel.triage.extractor.extract_incident.

extract_incident is a thin wiring layer over GatewayConfig.from_env +
create_completion -- these tests verify the wiring (what gets passed where),
not the LLM call itself. Both dependencies are patched at their
sentinel.triage.extractor import site, matching the patching style already
used in tests/unit/test_gateway.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sentinel.gateway import GatewayConfig
from sentinel.triage.extractor import LLMIncidentExtraction, extract_incident
from tests.unit.triage.fakes import make_extraction


def _fake_config() -> GatewayConfig:
    return GatewayConfig(provider="anthropic", auth_token="original-key", model="claude-sonnet")


class TestGatewayConfigWiring:
    def test_provider_arg_forwarded_to_gateway_config_from_env(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.from_env", return_value=_fake_config()
            ) as mock_from_env,
            patch("sentinel.triage.extractor.create_completion"),
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
                provider="gemini",
            )

        mock_from_env.assert_called_once_with(provider="gemini")

    def test_provider_none_by_default(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.from_env", return_value=_fake_config()
            ) as mock_from_env,
            patch("sentinel.triage.extractor.create_completion"),
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
            )

        mock_from_env.assert_called_once_with(provider=None)


class TestApiKeyOverride:
    def test_api_key_override_replaces_auth_token(self) -> None:
        captured_config = {}

        def _capture_config(config: GatewayConfig, **kwargs: object) -> MagicMock:
            captured_config["config"] = config
            return MagicMock()

        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.from_env", return_value=_fake_config()
            ),
            patch(
                "sentinel.triage.extractor.create_completion", side_effect=_capture_config
            ),
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
                api_key="override-key",
            )

        assert captured_config["config"].auth_token == "override-key"

    def test_no_api_key_leaves_config_auth_token_untouched(self) -> None:
        captured_config = {}

        def _capture_config(config: GatewayConfig, **kwargs: object) -> MagicMock:
            captured_config["config"] = config
            return MagicMock()

        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.from_env", return_value=_fake_config()
            ),
            patch(
                "sentinel.triage.extractor.create_completion", side_effect=_capture_config
            ),
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
            )

        assert captured_config["config"].auth_token == "original-key"

    def test_empty_string_api_key_does_not_override(self) -> None:
        captured_config = {}

        def _capture_config(config: GatewayConfig, **kwargs: object) -> MagicMock:
            captured_config["config"] = config
            return MagicMock()

        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.from_env", return_value=_fake_config()
            ),
            patch(
                "sentinel.triage.extractor.create_completion", side_effect=_capture_config
            ),
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
                api_key="",
            )

        assert captured_config["config"].auth_token == "original-key"


class TestCreateCompletionWiring:
    def test_forwards_all_call_args_and_response_model(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.from_env", return_value=_fake_config()
            ),
            patch("sentinel.triage.extractor.create_completion") as mock_create,
        ):
            extract_incident(
                system_prompt="SYSTEM PROMPT",
                user_prompt="USER PROMPT",
                model="claude-sonnet",
                temperature=0.5,
                max_tokens=2048,
                max_retries=4,
            )

            _, kwargs = mock_create.call_args
            assert kwargs["model"] == "claude-sonnet"
            assert kwargs["system_prompt"] == "SYSTEM PROMPT"
            assert kwargs["user_prompt"] == "USER PROMPT"
            assert kwargs["temperature"] == 0.5
            assert kwargs["max_tokens"] == 2048
            assert kwargs["max_retries"] == 4
            assert kwargs["response_model"] is LLMIncidentExtraction

    def test_default_max_retries_is_two(self) -> None:
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.from_env", return_value=_fake_config()
            ),
            patch("sentinel.triage.extractor.create_completion") as mock_create,
        ):
            extract_incident(
                system_prompt="SYS",
                user_prompt="USER",
                model="claude-sonnet",
                temperature=0.0,
                max_tokens=100,
            )

            assert mock_create.call_args.kwargs["max_retries"] == 2

    def test_returns_create_completion_result_unchanged(self) -> None:
        sentinel_result = object()
        with (
            patch(
                "sentinel.triage.extractor.GatewayConfig.from_env", return_value=_fake_config()
            ),
            patch(
                "sentinel.triage.extractor.create_completion", return_value=sentinel_result
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
