"""Tests for sentinel.config.load_settings.

All tests pass an explicit config_path (pointing into tmp_path) rather than
relying on the default './sentinel.yaml' lookup, so these never touch the
repo's real config file or depend on cwd.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sentinel.config import load_settings


def _clear_sentinel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith("SENTINEL_"):
            monkeypatch.delenv(key, raising=False)


def test_no_yaml_file_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_sentinel_env(monkeypatch)

    settings = load_settings(config_path=tmp_path / "does-not-exist.yaml")

    assert settings.model.provider == "together"
    assert settings.model.default == "together-qwen-7b"
    assert settings.log_level == "INFO"


def test_empty_yaml_file_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_sentinel_env(monkeypatch)
    config_path = tmp_path / "sentinel.yaml"
    config_path.write_text("")

    settings = load_settings(config_path=config_path)

    assert settings.model.default == "together-qwen-7b"


def test_malformed_yaml_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_sentinel_env(monkeypatch)
    config_path = tmp_path / "sentinel.yaml"
    config_path.write_text("model:\n  default: [unclosed")

    with pytest.raises(yaml.YAMLError):
        load_settings(config_path=config_path)


def test_yaml_value_applied_when_no_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_sentinel_env(monkeypatch)
    config_path = tmp_path / "sentinel.yaml"
    config_path.write_text("model:\n  default: claude-haiku\n")

    settings = load_settings(config_path=config_path)

    assert settings.model.default == "claude-haiku"


def test_env_value_applied_when_field_absent_from_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_sentinel_env(monkeypatch)
    config_path = tmp_path / "sentinel.yaml"
    # yaml sets a *different* field so the file is non-empty but doesn't
    # touch model.default.
    config_path.write_text("model:\n  max_tokens: 2048\n")
    monkeypatch.setenv("SENTINEL_MODEL__DEFAULT", "claude-haiku")

    settings = load_settings(config_path=config_path)

    assert settings.model.default == "claude-haiku"
    assert settings.model.max_tokens == 2048


def test_yaml_takes_precedence_over_env_for_the_same_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documents ACTUAL behavior, which contradicts load_settings' own docstring.

    load_settings()'s docstring claims env vars are highest priority, merged
    "under" sentinel.yaml. In practice, yaml_overrides are passed as
    constructor kwargs to Settings(**yaml_overrides), and pydantic-settings'
    default source order gives init kwargs priority over env vars. So when a
    field is set in *both* sentinel.yaml and the environment, the yaml value
    wins -- the opposite of what's documented. This test pins the current
    (buggy-relative-to-docs) behavior; if load_settings is fixed to match its
    docstring, update this test's expected value to "from-env".
    """
    _clear_sentinel_env(monkeypatch)
    config_path = tmp_path / "sentinel.yaml"
    config_path.write_text("model:\n  default: from-yaml\n")
    monkeypatch.setenv("SENTINEL_MODEL__DEFAULT", "from-env")

    settings = load_settings(config_path=config_path)

    assert settings.model.default == "from-yaml"


def test_logging_level_hoisted_to_log_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_sentinel_env(monkeypatch)
    config_path = tmp_path / "sentinel.yaml"
    config_path.write_text("logging:\n  level: DEBUG\n")

    settings = load_settings(config_path=config_path)

    assert settings.log_level == "DEBUG"


def test_logging_block_without_level_key_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_sentinel_env(monkeypatch)
    config_path = tmp_path / "sentinel.yaml"
    config_path.write_text("logging:\n  format: json\n")

    settings = load_settings(config_path=config_path)

    assert settings.log_level == "INFO"


def test_nested_qdrant_config_env_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QdrantConfig has its own env_prefix (SENTINEL_QDRANT_), not the
    top-level nested-delimiter form -- verify it's actually wired through
    Settings.qdrant."""
    _clear_sentinel_env(monkeypatch)
    monkeypatch.setenv("SENTINEL_QDRANT_MODE", "server")

    settings = load_settings(config_path=tmp_path / "does-not-exist.yaml")

    assert settings.qdrant.mode == "server"
