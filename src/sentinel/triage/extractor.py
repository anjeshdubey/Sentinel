"""Structured extraction via Instructor (multi-provider)."""

from pydantic import BaseModel, Field

from sentinel.gateway import (
    GatewayConfig,
    create_completion_with_fallback,
    resolve_model,
)
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
    environment: str = Field(
        description="Environment: prod, staging, dev, sandbox, unknown"
    )
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
    suspected_root_cause: str | None = Field(
        default=None,
        description=(
            "If the alert clearly suggests a cause (recent deploy, DB timeout, "
            "memory leak), state it in one sentence. None if genuinely unclear."
        ),
    )
    suggested_urgency: Urgency = Field(description="Recommended response urgency")
    tags: list[str] = Field(default_factory=list, description="Classification tags")
    proposed_remediation: str | None = Field(
        default=None,
        description=(
            "A concrete remediation step for this incident, grounded in the "
            "retrieved runbook excerpts provided in <retrieved_context>. Cite the "
            "runbook's guidance rather than inventing steps. Leave null if the "
            "runbooks do not clearly support a remediation for this symptom."
        ),
    )


def extract_incident(
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    provider: str | None = None,
    api_key: str | None = None,
    max_retries: int = 2,
    use_cache: bool = True,
) -> LLMIncidentExtraction:
    """Call the LLM provider chain via Instructor to extract structured incident data.

    Builds a priority-ordered provider chain (typically Together AI -> Groq ->
    Gemini -> Anthropic, whichever have API keys configured) via
    GatewayConfig.chain_from_env(), with `provider` (typically
    settings.model.provider from sentinel.yaml) pinned first when given. If
    the primary provider's call fails for any reason, the next provider in
    the chain is tried automatically -- triage should never hard-fail just
    because one provider is down. Successful primary-provider responses are
    cached (Upstash, if configured) so repeated identical alerts don't
    re-hit the LLM.

    Args:
        system_prompt: System prompt with triage instructions.
        user_prompt: Rendered user prompt with alert data.
        model: Model identifier (short alias or full model ID) for the
            primary provider only -- fallback providers use their own
            default model, since a model name from one provider's alias
            table is meaningless to another provider's API.
        temperature: Sampling temperature (0.0 for deterministic).
        max_tokens: Max response tokens.
        provider: Provider name ("together" | "groq" | "gemini" | "anthropic").
            Optional — normally sourced from settings.model.provider. Pinned
            as the primary/first entry in the fallback chain.
        api_key: Override API key for the primary provider (optional —
            normally loaded from env; used for testing).
        max_retries: Number of retry attempts on validation failure, per provider.
        use_cache: Whether to check/populate the Upstash response cache.

    Returns:
        Validated LLMIncidentExtraction with all LLM-determined fields.
    """
    chain = GatewayConfig.chain_from_env(primary_provider=provider)

    primary = chain[0]
    chain[0] = GatewayConfig(
        provider=primary.provider,
        auth_token=api_key or primary.auth_token,
        model=resolve_model(primary.provider, model),
        base_url=primary.base_url,
    )

    return create_completion_with_fallback(
        chain,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
        response_model=LLMIncidentExtraction,
        use_cache=use_cache,
    )
