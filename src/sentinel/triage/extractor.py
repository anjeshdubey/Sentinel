"""Structured extraction via Instructor (multi-provider)."""

from typing import Optional

from pydantic import BaseModel, Field

from sentinel.gateway import GatewayConfig, create_completion
from sentinel.models.enums import Severity, Urgency


class LLMIncidentExtraction(BaseModel):
    """Schema the LLM fills in. Excludes computed fields (IDs, hashes, timestamps).

    This is what Instructor extracts from the LLM response.
    The triage engine then enriches it with deterministic fields.
    """

    title: str = Field(max_length=120, description="Human-readable incident summary")
    severity: Severity = Field(
        description=(
            "Incident severity. Use 'critical' for full outages or >50% error rate. "
            "'high' for partial outages or >10% error rate. "
            "'medium' for degraded performance. 'low' for warnings."
        )
    )
    service: str = Field(
        description="The primary affected service, as a lowercase hyphenated slug."
    )
    environment: str = Field(description="Environment: prod, staging, dev, sandbox, unknown")
    symptom: str = Field(
        description=(
            "One sentence describing what is failing and how, in plain English. "
            "Example: 'Payment gateway returning HTTP 500 at 87% error rate.'"
        )
    )
    blast_radius: list[str] = Field(
        default_factory=list,
        description=(
            "List of affected regions, tenants, or user groups. "
            "Use strings like 'us-east-1', 'eu-west-1', 'all-regions', "
            "'enterprise-tier', 'free-tier'. Empty list if scope is unknown."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How confident you are in this classification, from 0.0 to 1.0. "
            "Use >0.8 only when the alert is clear and unambiguous. "
            "Use 0.3–0.6 for noisy or incomplete alert data."
        ),
    )
    suspected_root_cause: Optional[str] = Field(
        default=None,
        description=(
            "If the alert clearly suggests a cause (recent deploy, DB timeout, "
            "memory leak), state it in one sentence. None if genuinely unclear."
        ),
    )
    suggested_urgency: Urgency = Field(description="Recommended response urgency")
    tags: list[str] = Field(default_factory=list, description="Classification tags")


def extract_incident(
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    api_key: str | None = None,
    max_retries: int = 2,
) -> LLMIncidentExtraction:
    """Call the configured LLM provider via Instructor to extract structured incident data.

    Provider is selected via SENTINEL_PROVIDER (defaults to "anthropic").

    Args:
        system_prompt: System prompt with triage instructions.
        user_prompt: Rendered user prompt with alert data.
        model: Model identifier (short alias or full model ID, provider-specific).
        temperature: Sampling temperature (0.0 for deterministic).
        max_tokens: Max response tokens.
        api_key: Override API key (optional — normally loaded from env).
        max_retries: Number of retry attempts on validation failure.

    Returns:
        Validated LLMIncidentExtraction with all LLM-determined fields.
    """
    config = GatewayConfig.from_env()
    if api_key:
        # Explicit key passed — override (for testing)
        config.auth_token = api_key

    return create_completion(
        config,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
        response_model=LLMIncidentExtraction,
    )
