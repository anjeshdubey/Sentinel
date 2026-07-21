"""Sentinel LLM client factory — multi-provider (Together AI / Groq / Gemini / Anthropic).

Environment variables:
    SENTINEL_PROVIDER        — "together" (default), "groq", "gemini", or "anthropic"
    TOGETHER_API_KEY         — required when provider is "together"
    GROQ_API_KEY             — required when provider is "groq"
    GEMINI_API_KEY           — required when provider is "gemini"
    ANTHROPIC_API_KEY        — required when provider is "anthropic"
    ANTHROPIC_BASE_URL       — override base URL for Anthropic (optional; proxy use)
    SENTINEL_MODEL           — model alias or full model ID (optional; provider-specific default)
    UPSTASH_REDIS_REST_URL   — Upstash Redis REST endpoint (optional, enables response caching)
    UPSTASH_REDIS_REST_TOKEN — Upstash Redis REST token (optional, enables response caching)

`GatewayConfig.chain_from_env()` builds a priority-ordered provider chain
from whichever API keys are set — Together AI, then Groq, then Gemini, then
Anthropic. Together AI leads because it's a metered paid tier with no
free-tier rate-limit throttling; Anthropic sits last because it's the most
expensive per call. `create_completion_with_fallback()` walks that chain,
trying each provider in order until one succeeds — an incident triage
pipeline should never hard-fail just because one provider is down.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

Provider = str  # "together" | "groq" | "gemini" | "anthropic"

API_KEY_ENV_VARS: dict[str, str] = {
    "together": "TOGETHER_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

DEFAULT_MODELS: dict[str, str] = {
    "together": "together-qwen-7b",
    "groq": "groq-llama",
    "gemini": "gemini-flash",
    "anthropic": "claude-sonnet",
}

DEFAULT_PROVIDER = "together"

# Priority order for automatic fallback — cheapest/most-reliable-paid-tier
# first, most expensive last (emergency-only).
PROVIDER_PRIORITY: tuple[str, ...] = ("together", "groq", "gemini", "anthropic")

# Short aliases (what you'd write in sentinel.yaml) -> real provider model IDs.
MODEL_ALIASES_BY_PROVIDER: dict[str, dict[str, str]] = {
    "together": {
        "together-qwen-7b": "Qwen/Qwen2.5-7B-Instruct-Turbo",
        "together-llama-70b": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    },
    "groq": {
        "groq-llama": "llama-3.3-70b-versatile",
    },
    "gemini": {
        "gemini-flash": "gemini-flash-latest",
        "gemini-pro": "gemini-pro-latest",
    },
    "anthropic": {
        "claude-sonnet": "claude-sonnet-4-5-20250929",
        "claude-haiku": "claude-haiku-4-5-20251001",
        "claude-opus": "claude-opus-4-1-20250805",
        # Common explicit names pass through unchanged.
        "claude-sonnet-4-5-20250929": "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
        "claude-opus-4-1-20250805": "claude-opus-4-1-20250805",
    },
}

# Backwards-compatible alias for callers that only care about Anthropic models.
MODEL_ALIASES = MODEL_ALIASES_BY_PROVIDER["anthropic"]

TOGETHER_BASE_URL = "https://api.together.xyz/v1"

# Demo/triage prompts repeat often (similar alerts), so a cached extraction
# stays valid until the prompt itself changes — cache for 7 days.
CACHE_TTL_SECONDS = 60 * 60 * 24 * 7


class GatewayConfigError(Exception):
    """Raised when the LLM provider configuration is missing or invalid."""

    pass


@dataclass
class GatewayConfig:
    """Configuration for one LLM provider connection."""

    provider: str
    auth_token: str
    model: str
    base_url: str | None = None

    @classmethod
    def from_env(cls, provider: str | None = None) -> GatewayConfig:
        """Load config for a single, specific provider from environment variables.

        Args:
            provider: Explicit provider (e.g. from sentinel.yaml's model.provider).
                Takes priority over SENTINEL_PROVIDER when given. Falls back to
                the SENTINEL_PROVIDER env var, then "together", when omitted.

        Reads the matching API key env var for whichever provider is selected.
        Raises if that specific provider has no key configured -- this is the
        strict, single-provider entry point. Use chain_from_env() for
        automatic multi-provider fallback.
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

        base_url = (
            os.environ.get("ANTHROPIC_BASE_URL") if provider == "anthropic" else None
        )
        model = os.environ.get("SENTINEL_MODEL", "") or DEFAULT_MODELS[provider]
        model = resolve_model(provider, model)

        return cls(
            provider=provider, auth_token=auth_token, model=model, base_url=base_url
        )

    @classmethod
    def chain_from_env(cls, primary_provider: str | None = None) -> list[GatewayConfig]:
        """Build a priority-ordered provider chain from whichever API keys are set.

        Order: primary_provider (if given and configured) first, then whatever
        remaining providers from PROVIDER_PRIORITY have a key set. Used by
        create_completion_with_fallback() so a failing provider automatically
        falls through to the next one instead of hard-erroring.

        SENTINEL_MODEL, if set, is applied to whichever provider's own alias
        table it belongs to; providers whose alias table doesn't recognize it
        fall back to their own DEFAULT_MODELS entry rather than sending a
        foreign model name to the wrong API.
        """
        primary_provider = (primary_provider or "").strip().lower() or None
        if primary_provider and primary_provider not in API_KEY_ENV_VARS:
            raise GatewayConfigError(
                f"Unknown SENTINEL_PROVIDER '{primary_provider}'. "
                f"Supported providers: {', '.join(sorted(API_KEY_ENV_VARS))}"
            )

        model_override = os.environ.get("SENTINEL_MODEL", "")
        order = PROVIDER_PRIORITY
        if primary_provider:
            order = (primary_provider,) + tuple(
                p for p in PROVIDER_PRIORITY if p != primary_provider
            )

        chain: list[GatewayConfig] = []
        for provider in order:
            auth_token = os.environ.get(API_KEY_ENV_VARS[provider], "")
            if not auth_token:
                continue
            base_url = (
                os.environ.get("ANTHROPIC_BASE_URL")
                if provider == "anthropic"
                else None
            )
            model = resolve_model(
                provider, _pick_model_for_provider(provider, model_override)
            )
            chain.append(
                cls(
                    provider=provider,
                    auth_token=auth_token,
                    model=model,
                    base_url=base_url,
                )
            )

        if not chain:
            raise GatewayConfigError(
                "No API key found for any provider. Set one of: "
                + ", ".join(API_KEY_ENV_VARS[p] for p in PROVIDER_PRIORITY)
            )
        return chain


def _pick_model_for_provider(provider: str, override: str) -> str:
    """Resolve SENTINEL_MODEL for one provider's alias table.

    If the override names a model belonging to a *different* provider (e.g.
    SENTINEL_MODEL=claude-sonnet while building the Groq fallback config),
    ignore it and fall back to this provider's own default — a fallback
    provider should never be forced onto another provider's model id.
    """
    if not override:
        return DEFAULT_MODELS[provider]
    own_aliases = MODEL_ALIASES_BY_PROVIDER.get(provider, {})
    if override in own_aliases:
        return override
    for other_provider, aliases in MODEL_ALIASES_BY_PROVIDER.items():
        if other_provider != provider and override in aliases:
            return DEFAULT_MODELS[provider]
    return override  # Unknown string — treat as an explicit model id for this provider.


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

    if config.provider == "together":
        from openai import OpenAI

        return OpenAI(api_key=config.auth_token, base_url=TOGETHER_BASE_URL)

    raise GatewayConfigError(f"Unknown provider '{config.provider}'")


def create_client(config: GatewayConfig | None = None) -> Any:
    """Create a raw provider SDK client.

    Args:
        config: Configuration. If None, loads from environment.

    Returns:
        Configured provider SDK client (Anthropic, google.genai.Client, Groq, or OpenAI).
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
    `system=` as a top-level kwarg, while Gemini (via from_genai), Groq (via
    from_groq), and Together AI (via from_openai) use OpenAI-style chat
    messages with a "system" role entry.
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
    elif config.provider == "together":
        client = instructor.from_openai(raw_client)
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


def create_completion_with_fallback(
    chain: list[GatewayConfig],
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    max_retries: int,
    response_model: type,
    use_cache: bool = True,
) -> Any:
    """Run create_completion() against each provider in `chain` until one succeeds.

    Serves from the Upstash cache first when configured. On a cache miss,
    tries providers in order -- catching broadly (any Exception from the call
    itself, since a rate limit, timeout, or 5xx from any of the four
    different provider SDKs should all trigger the same "try the next
    provider" response) -- and caches the result once a provider succeeds.

    Every provider failing raises GatewayConfigError wrapping the last error,
    so callers get one clear exception instead of whichever provider happened
    to fail last.
    """
    if not chain:
        raise GatewayConfigError(
            "create_completion_with_fallback requires a non-empty chain"
        )

    primary = chain[0]
    cache_key = None
    if use_cache:
        cache_key = _cache_key(
            primary.provider,
            primary.model,
            system_prompt,
            user_prompt,
            temperature,
            max_tokens,
            response_model,
        )
        cached = _cache_command("GET", cache_key)
        if cached is not None:
            return response_model.model_validate_json(cached)

    last_error: Exception | None = None
    for config in chain:
        try:
            result = create_completion(
                config,
                model=config.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=max_retries,
                response_model=response_model,
            )
        except Exception as e:  # noqa: BLE001 - intentionally broad, see docstring
            logger.warning(
                "Provider %s failed, trying next in chain: %s", config.provider, e
            )
            last_error = e
            continue

        if cache_key is not None and config.provider == primary.provider:
            _cache_command(
                "SETEX", cache_key, CACHE_TTL_SECONDS, result.model_dump_json()
            )
        return result

    raise GatewayConfigError(
        f"All providers in the chain failed. Last error: {last_error}"
    )


# --- Upstash Redis cache (exact-match, keyed by request payload) ---


def _cache_command(*command: str | int) -> Any:
    """Execute a single Redis command via the Upstash REST API.

    Returns None (cache disabled or unreachable) instead of raising -- caching
    is a latency/cost optimization, never a hard dependency for triage to run.
    """
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    if not url or not token:
        return None

    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=list(command),
            timeout=2.0,
        )
        resp.raise_for_status()
        return resp.json().get("result")
    except httpx.HTTPError as e:
        logger.warning("Upstash cache request failed, continuing without cache: %s", e)
        return None


def _cache_key(
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    response_model: type,
) -> str:
    """Deterministic cache key for a structured-extraction request."""
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_model": response_model.__name__,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sentinel:llm:{digest}"
