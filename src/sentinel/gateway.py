"""Sentinel LLM client factory — multi-provider (Anthropic / Gemini / Groq).

Environment variables:
    SENTINEL_PROVIDER   — "anthropic" (default), "gemini", or "groq"
    ANTHROPIC_API_KEY   — required when provider is "anthropic"
    GEMINI_API_KEY      — required when provider is "gemini"
    GROQ_API_KEY        — required when provider is "groq"
    ANTHROPIC_BASE_URL  — override base URL for Anthropic (optional; proxy use)
    SENTINEL_MODEL      — model alias or full model ID (optional; provider-specific default)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

Provider = str  # "anthropic" | "gemini" | "groq"

API_KEY_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}

DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet",
    "gemini": "gemini-flash",
    "groq": "groq-llama",
}

DEFAULT_PROVIDER = "anthropic"

# Short aliases (what you'd write in sentinel.yaml) -> real provider model IDs.
MODEL_ALIASES_BY_PROVIDER: dict[str, dict[str, str]] = {
    "anthropic": {
        "claude-sonnet": "claude-sonnet-4-5-20250929",
        "claude-haiku": "claude-haiku-4-5-20251001",
        "claude-opus": "claude-opus-4-1-20250805",
        # Common explicit names pass through unchanged.
        "claude-sonnet-4-5-20250929": "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
        "claude-opus-4-1-20250805": "claude-opus-4-1-20250805",
    },
    "gemini": {
        "gemini-flash": "gemini-flash-latest",
        "gemini-pro": "gemini-pro-latest",
    },
    "groq": {
        "groq-llama": "llama-3.3-70b-versatile",
    },
}

# Backwards-compatible alias for callers that only care about Anthropic models.
MODEL_ALIASES = MODEL_ALIASES_BY_PROVIDER["anthropic"]


class GatewayConfigError(Exception):
    """Raised when the LLM provider configuration is missing or invalid."""

    pass


@dataclass
class GatewayConfig:
    """Configuration for the active LLM provider connection."""

    provider: str
    auth_token: str
    model: str
    base_url: str | None = None

    @classmethod
    def from_env(cls, provider: str | None = None) -> "GatewayConfig":
        """Load config from environment variables.

        Args:
            provider: Explicit provider (e.g. from sentinel.yaml's model.provider).
                Takes priority over SENTINEL_PROVIDER when given. Falls back to
                the SENTINEL_PROVIDER env var, then "anthropic", when omitted.

        Reads the matching API key env var (ANTHROPIC_API_KEY / GEMINI_API_KEY /
        GROQ_API_KEY) for whichever provider is selected. Optionally reads
        ANTHROPIC_BASE_URL (Anthropic only, e.g. for a proxy) and SENTINEL_MODEL.
        """
        provider = (
            (provider or "").strip().lower()
            or os.environ.get("SENTINEL_PROVIDER", "").strip().lower()
            or DEFAULT_PROVIDER
        )

        if provider not in API_KEY_ENV_VARS:
            raise GatewayConfigError(
                f"Unknown SENTINEL_PROVIDER '{provider}'. "
                f"Supported providers: {', '.join(sorted(API_KEY_ENV_VARS))}"
            )

        key_env_var = API_KEY_ENV_VARS[provider]
        auth_token = os.environ.get(key_env_var, "")

        if not auth_token:
            raise GatewayConfigError(
                f"No API key found for provider '{provider}'. Set {key_env_var}.\n"
                f"export {key_env_var}=..."
            )

        base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
        model = os.environ.get("SENTINEL_MODEL", "") or DEFAULT_MODELS[provider]
        model = resolve_model(provider, model)

        return cls(provider=provider, auth_token=auth_token, model=model, base_url=base_url)


def resolve_model(provider: str, model_name: str) -> str:
    """Resolve a short alias to its full provider-specific model ID.

    Examples:
        resolve_model("anthropic", "claude-sonnet") -> "claude-sonnet-4-5-20250929"
        resolve_model("gemini", "gemini-flash") -> "gemini-flash-latest"
    """
    aliases = MODEL_ALIASES_BY_PROVIDER.get(provider, {})
    return aliases.get(model_name, model_name)


def _create_raw_client(config: GatewayConfig) -> Any:
    """Instantiate the raw provider SDK client for the given config."""
    if config.provider == "anthropic":
        from anthropic import Anthropic

        client_kwargs: dict = {"api_key": config.auth_token}
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        return Anthropic(**client_kwargs)

    if config.provider == "gemini":
        from google import genai

        return genai.Client(api_key=config.auth_token)

    if config.provider == "groq":
        from groq import Groq

        return Groq(api_key=config.auth_token)

    raise GatewayConfigError(f"Unknown provider '{config.provider}'")


def create_client(config: GatewayConfig | None = None) -> Any:
    """Create a raw provider SDK client.

    Args:
        config: Configuration. If None, loads from environment.

    Returns:
        Configured provider SDK client (Anthropic, google.genai.Client, or Groq).
    """
    if config is None:
        config = GatewayConfig.from_env()

    return _create_raw_client(config)


def create_completion(
    config: GatewayConfig,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    max_retries: int,
    response_model: type,
) -> Any:
    """Run a structured-output completion via Instructor, regardless of provider.

    Hides the Anthropic vs. OpenAI-style call shape difference: Anthropic takes
    `system=` as a top-level kwarg, while Gemini (via from_genai) and Groq
    (via from_groq) use OpenAI-style chat messages with a "system" role entry.
    """
    import instructor

    raw_client = _create_raw_client(config)
    resolved_model = resolve_model(config.provider, model)

    if config.provider == "anthropic":
        client = instructor.from_anthropic(raw_client)
        return client.messages.create(
            model=resolved_model,
            max_tokens=max_tokens,
            temperature=temperature,
            max_retries=max_retries,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            response_model=response_model,
        )

    if config.provider == "gemini":
        # GENAI_STRUCTURED_OUTPUTS avoids a bug in the default TOOLS mode
        # where enum fields come back as plain strings and fail pydantic
        # validation instead of being coerced to the enum member.
        client = instructor.from_genai(
            raw_client, mode=instructor.Mode.GENAI_STRUCTURED_OUTPUTS
        )
    elif config.provider == "groq":
        client = instructor.from_groq(raw_client)
    else:
        raise GatewayConfigError(f"Unknown provider '{config.provider}'")

    return client.chat.completions.create(
        model=resolved_model,
        max_tokens=max_tokens,
        temperature=temperature,
        max_retries=max_retries,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_model=response_model,
    )
