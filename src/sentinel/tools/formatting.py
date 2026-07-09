"""Enrichment context formatter — renders tool results for prompt injection.

Renders an EnrichmentContext into a compact markdown block suitable for
inclusion in the LLM prompt's <enrichment> section.

Field ordering (rendered top-to-bottom):
1. service_owner (team, tier, escalation channel)
2. recent_deploys (up to 5, newest first)
3. past_incidents (up to 3, ranked by relevance)
4. service_dependencies (upstream/downstream lists)

Truncation priority when block exceeds budget_tokens:
- Drop dependencies FIRST (lowest priority)
- Then past_incidents (keep top 1 if trimming needed)
- Then recent_deploys (keep top 2 if trimming needed)
- Owner is NEVER dropped (highest priority)

Token estimation: ~4 chars per token for English prose.
"""

from __future__ import annotations

import html

from sentinel.tools.models import EnrichmentContext

# Approximate chars-per-token for budget estimation
_CHARS_PER_TOKEN = 4

# Hard character limit to prevent context exhaustion
_MAX_BLOCK_CHARS = 2000


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return len(text) // _CHARS_PER_TOKEN + 1


def format_context_block(
    enrichment: EnrichmentContext, budget_tokens: int = 400
) -> str:
    """Render enrichment context as a compact markdown block for prompt injection.

    Args:
        enrichment: Aggregated enrichment data from tool calls.
        budget_tokens: Maximum token budget for the rendered block.

    Returns:
        Markdown string fitting within budget_tokens.
        Returns '(No enrichment data available for this alert.)' when all
        fields are None/empty and no tools were unavailable.
    """
    sections: list[str] = []
    estimated_tokens = 0
    budget_chars = budget_tokens * _CHARS_PER_TOKEN

    # Helper to check if adding text would bust the budget
    def _fits(text: str) -> bool:
        return (estimated_tokens + _estimate_tokens(text)) <= budget_tokens

    # 1. Owner (NEVER dropped)
    if enrichment.service_owner:
        owner = enrichment.service_owner
        owner_text = (
            f"**Service Owner:** {html.escape(owner.team)} (tier {owner.tier}), "
            f"escalate to {html.escape(owner.escalation_channel)}"
        )
        if owner.manager:
            owner_text += f", manager: {html.escape(owner.manager)}"
        sections.append(owner_text)
        estimated_tokens += _estimate_tokens(owner_text)
    elif "ownership" in enrichment.unavailable_tools:
        text = "**Service Owner:** (unavailable)"
        sections.append(text)
        estimated_tokens += _estimate_tokens(text)

    # 2. Recent deploys (truncate to top 2 if over budget)
    if enrichment.recent_deploys:
        # Determine how many deploys to show
        deploys_to_show = list(enrichment.recent_deploys[:5])

        # Pre-render to check budget
        deploy_lines = [
            f"- {html.escape(d.version)} by {html.escape(d.deployed_by)} at {d.deployed_at.strftime('%Y-%m-%d %H:%M')}"
            + (f" (rollback of {html.escape(d.rollback_of)})" if d.rollback_of else "")
            + (f": {html.escape(d.change_summary)}" if d.change_summary else "")
            for d in deploys_to_show
        ]
        full_text = "**Recent Deploys:**\n" + "\n".join(deploy_lines)

        if not _fits(full_text):
            # Truncate to top 2
            deploys_to_show = deploys_to_show[:2]
            deploy_lines = [
                f"- {html.escape(d.version)} by {html.escape(d.deployed_by)} at {d.deployed_at.strftime('%Y-%m-%d %H:%M')}"
                + (f": {html.escape(d.change_summary)}" if d.change_summary else "")
                for d in deploys_to_show
            ]
            full_text = "**Recent Deploys:**\n" + "\n".join(deploy_lines)

        sections.append(full_text)
        estimated_tokens += _estimate_tokens(full_text)
    elif "deploys" in enrichment.unavailable_tools:
        text = "**Recent Deploys:** (unavailable)"
        sections.append(text)
        estimated_tokens += _estimate_tokens(text)

    # 3. Past incidents (truncate to top 1 if over budget)
    if enrichment.past_incidents:
        incidents_to_show = list(enrichment.past_incidents[:3])

        incident_lines = [
            f"- [{p.relevance}] {html.escape(p.title)} ({p.severity.value}, {p.occurred_at.strftime('%Y-%m-%d')})"
            for p in incidents_to_show
        ]
        full_text = "**Similar Past Incidents:**\n" + "\n".join(incident_lines)

        if not _fits(full_text):
            # Truncate to top 1
            incidents_to_show = incidents_to_show[:1]
            incident_lines = [
                f"- [{p.relevance}] {html.escape(p.title)} ({p.severity.value})"
                for p in incidents_to_show
            ]
            full_text = "**Similar Past Incidents:**\n" + "\n".join(incident_lines)

        sections.append(full_text)
        estimated_tokens += _estimate_tokens(full_text)
    elif "past_incidents" in enrichment.unavailable_tools:
        text = "**Similar Past Incidents:** (unavailable)"
        sections.append(text)
        estimated_tokens += _estimate_tokens(text)

    # 4. Dependencies (drop entirely if over budget)
    if enrichment.service_dependencies:
        deps = enrichment.service_dependencies
        upstream_str = ", ".join(html.escape(s) for s in deps.upstream[:5]) if deps.upstream else "none"
        downstream_str = ", ".join(html.escape(s) for s in deps.downstream[:5]) if deps.downstream else "none"
        deps_text = (
            f"**Dependencies:** upstream: [{upstream_str}]; "
            f"downstream: [{downstream_str}]"
        )

        if _fits(deps_text):
            sections.append(deps_text)
            estimated_tokens += _estimate_tokens(deps_text)
        # If over budget, dependencies are simply dropped (lowest priority)
    elif "dependencies" in enrichment.unavailable_tools:
        text = "**Dependencies:** (unavailable)"
        # Only add if we have budget
        if _fits(text):
            sections.append(text)
            estimated_tokens += _estimate_tokens(text)

    # Empty-context sentinel
    if not sections:
        return "(No enrichment data available for this alert.)"

    result = "\n\n".join(sections)

    # Hard cap to prevent context exhaustion regardless of budget_tokens
    if len(result) > _MAX_BLOCK_CHARS:
        result = result[:_MAX_BLOCK_CHARS] + "\n(truncated)"

    return result
