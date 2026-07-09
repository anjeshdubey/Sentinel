"""
Sentinel Gateway — LLM client factory for Salesforce internal gateway.

Replicates the FlowForge gateway pattern. The gateway is Anthropic-compatible but requires:
- Custom base URL (eng-ai-model-gateway)
- Auth token (ANTHROPIC_AUTH_TOKEN, not ANTHROPIC_API_KEY)
- Model name mapping (global.anthropic.* prefix)
- Corporate TLS cert for SSL verification

Environment variables:
    ANTHROPIC_AUTH_TOKEN     — Gateway auth token (primary)
    ANTHROPIC_API_KEY        — Fallback for local dev with direct API
    ANTHROPIC_BASE_URL      — Gateway base URL
    SSL_CERT_FILE           — Path to corporate CA cert (optional)
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass

import httpx
from anthropic import Anthropic


# Default gateway configuration (Salesforce internal)
DEFAULT_BASE_URL = "https://eng-ai-model-gateway.sfproxy.devx-preprod.aws-esvc1-useast2.aws.sfdc.cl"
DEFAULT_MODEL = "global.anthropic.claude-sonnet-4-6"
DEFAULT_CERT_PATH = os.path.expanduser("~/.aisuite/conf/npm-sfdc-certs.pem")


# Model alias mapping — short names to gateway model IDs
MODEL_ALIASES: dict[str, str] = {
    # Short names (what you'd write in sentinel.yaml)
    "claude-sonnet": "global.anthropic.claude-sonnet-4-6",
    "claude-haiku": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-opus": "global.anthropic.claude-opus-4-7",
    # Standard Anthropic model names → gateway equivalents
    "claude-sonnet-4-20250514": "global.anthropic.claude-sonnet-4-6",
    "claude-opus-4-20250514": "global.anthropic.claude-opus-4-7",
    "claude-sonnet-4-6": "global.anthropic.claude-sonnet-4-6",
    "claude-opus-4-7": "global.anthropic.claude-opus-4-7",
    # Already-resolved gateway names pass through
    "global.anthropic.claude-sonnet-4-6": "global.anthropic.claude-sonnet-4-6",
    "global.anthropic.claude-opus-4-7": "global.anthropic.claude-opus-4-7",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
}


class GatewayConfigError(Exception):
    """Raised when gateway configuration is missing or invalid."""

    pass


@dataclass
class GatewayConfig:
    """Configuration for the LLM gateway connection."""

    base_url: str
    auth_token: str
    model: str
    cert_path: str | None = None

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        """Load gateway config from environment variables.

        Priority:
            1. ANTHROPIC_AUTH_TOKEN (gateway token)
            2. ANTHROPIC_API_KEY (fallback for direct Anthropic)
        """
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        if not auth_token:
            auth_token = os.environ.get("ANTHROPIC_API_KEY", "")

        if not auth_token:
            raise GatewayConfigError(
                "No auth token found. Set ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY.\n"
                "For Salesforce gateway: export ANTHROPIC_AUTH_TOKEN=<your-token>\n"
                "For direct Anthropic: export ANTHROPIC_API_KEY=<your-key>"
            )

        base_url = os.environ.get("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL)
        model = os.environ.get("SENTINEL_MODEL", DEFAULT_MODEL)
        cert_path = os.environ.get("SSL_CERT_FILE", DEFAULT_CERT_PATH)

        # Resolve model alias
        model = resolve_model(model)

        # Check cert exists
        if cert_path and not os.path.exists(cert_path):
            cert_path = None  # Will use system default

        return cls(
            base_url=base_url,
            auth_token=auth_token,
            model=model,
            cert_path=cert_path,
        )

    @property
    def is_gateway(self) -> bool:
        """True if connected to the Salesforce gateway (not direct Anthropic)."""
        return "sfproxy" in self.base_url or "sfdc" in self.base_url


def create_client(config: GatewayConfig | None = None) -> Anthropic:
    """Create an Anthropic client configured for the LLM gateway.

    Args:
        config: Gateway configuration. If None, loads from environment.

    Returns:
        Configured Anthropic client pointing at the gateway.
    """
    if config is None:
        config = GatewayConfig.from_env()

    # Build httpx client with custom TLS if cert provided
    http_client = None
    if config.cert_path:
        ssl_context = ssl.create_default_context(cafile=config.cert_path)
        http_client = httpx.Client(verify=ssl_context)

    client_kwargs: dict = {
        "api_key": config.auth_token,
        "base_url": config.base_url,
    }

    if http_client:
        client_kwargs["http_client"] = http_client

    return Anthropic(**client_kwargs)


def resolve_model(model_name: str) -> str:
    """Resolve a short or standard model name to the gateway's full model ID.

    Examples:
        "claude-sonnet" -> "global.anthropic.claude-sonnet-4-6"
        "claude-sonnet-4-20250514" -> "global.anthropic.claude-sonnet-4-6"
        "global.anthropic.claude-opus-4-7" -> unchanged (already resolved)
    """
    return MODEL_ALIASES.get(model_name, model_name)
