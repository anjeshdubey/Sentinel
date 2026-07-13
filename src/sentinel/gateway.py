"""
Sentinel LLM client factory — direct Anthropic API.

Environment variables:
    ANTHROPIC_API_KEY   — Anthropic API key (required)
    ANTHROPIC_BASE_URL  — Override base URL (optional; defaults to api.anthropic.com)
    SENTINEL_MODEL      — Model alias or full model ID (optional; defaults to "claude-sonnet")
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from anthropic import Anthropic

DEFAULT_MODEL = "claude-sonnet"

# Short aliases (what you'd write in sentinel.yaml) -> real Anthropic model IDs.
MODEL_ALIASES: dict[str, str] = {
    "claude-sonnet": "claude-sonnet-4-5-20250929",
    "claude-haiku": "claude-haiku-4-5-20251001",
    "claude-opus": "claude-opus-4-1-20250805",
    # Common explicit names pass through unchanged.
    "claude-sonnet-4-5-20250929": "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
    "claude-opus-4-1-20250805": "claude-opus-4-1-20250805",
}


class GatewayConfigError(Exception):
    """Raised when the Anthropic client configuration is missing or invalid."""

    pass


@dataclass
class GatewayConfig:
    """Configuration for the Anthropic API connection."""

    auth_token: str
    model: str
    base_url: str | None = None

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        """Load config from environment variables.

        Requires ANTHROPIC_API_KEY. Optionally reads ANTHROPIC_BASE_URL
        (only needed for e.g. a proxy) and SENTINEL_MODEL.
        """
        auth_token = os.environ.get("ANTHROPIC_API_KEY", "")

        if not auth_token:
            raise GatewayConfigError(
                "No API key found. Set ANTHROPIC_API_KEY to your Anthropic API key.\n"
                "export ANTHROPIC_API_KEY=sk-ant-..."
            )

        base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
        model = os.environ.get("SENTINEL_MODEL", "") or DEFAULT_MODEL
        model = resolve_model(model)

        return cls(auth_token=auth_token, model=model, base_url=base_url)


def create_client(config: GatewayConfig | None = None) -> Anthropic:
    """Create an Anthropic client.

    Args:
        config: Configuration. If None, loads from environment.

    Returns:
        Configured Anthropic client.
    """
    if config is None:
        config = GatewayConfig.from_env()

    client_kwargs: dict = {"api_key": config.auth_token}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url

    return Anthropic(**client_kwargs)


def resolve_model(model_name: str) -> str:
    """Resolve a short alias to its full Anthropic model ID.

    Examples:
        "claude-sonnet" -> "claude-sonnet-4-5-20250929"
        "claude-sonnet-4-5-20250929" -> unchanged (already resolved)
    """
    return MODEL_ALIASES.get(model_name, model_name)
