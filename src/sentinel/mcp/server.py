"""
Sentinel MCP Server — exposes alert triage as a tool via Model Context Protocol.

This is a trivial MCP server that lets any MCP client (Claude Desktop, Claude Code,
or any compatible agent) discover and invoke Sentinel's triage capability.

Run:
    python -m sentinel.mcp.server          # stdio transport (for Claude Desktop/Code)
    python -m sentinel.mcp.server --sse    # SSE transport (for HTTP clients)

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "sentinel": {
          "command": "/Users/anjesh.dubey/AIProjects/sentinel/.venv13/bin/python",
          "args": ["-m", "sentinel.mcp.server"]
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from sentinel.config import load_settings
from sentinel.ingestion.service_extraction import extract_service_name
from sentinel.retrieval.bootstrap import build_retriever
from sentinel.tools.bootstrap import build_sync_facade, build_tool_provider
from sentinel.tools.formatting import format_context_block
from sentinel.tools.models import EnrichmentContext

logger = logging.getLogger(__name__)

# Initialize the MCP server
mcp = FastMCP(
    "Sentinel SRE Triage",
    instructions="LLM-powered SRE alert triage — structured understanding from raw alerts. Use triage_alert to classify any monitoring alert.",
)

# Module-level settings + retriever + tool provider — built once at server
# startup so heavy models/caches aren't reloaded per request.
_settings = load_settings()
try:
    _retriever = build_retriever(_settings)
    logger.info("MCP: RAG retrieval initialized")
except Exception as e:
    logger.warning(f"MCP: Failed to initialize retriever: {e}")
    _retriever = None

try:
    _tool_provider = build_sync_facade(build_tool_provider(_settings))
    logger.info("MCP: Tool provider initialized")
except Exception as e:
    logger.warning(f"MCP: Failed to initialize tool provider: {e}")
    _tool_provider = None


@mcp.tool()
def triage_alert(
    source: str,
    raw_payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Triage a raw monitoring alert and produce a structured incident summary.

    Takes a raw alert from any monitoring system (PagerDuty, Datadog, Grafana, Slack)
    and uses Claude to extract severity, blast radius, root cause hypothesis, and
    recommended urgency.

    Args:
        source: Origin monitoring system. One of: pagerduty, datadog, grafana, slack, manual.
        raw_payload: The raw alert body as a JSON object (whatever the monitoring system sent).
        metadata: Optional additional context (routing info, tags, environment hints).
        timestamp: When the alert fired (ISO 8601). Defaults to now if omitted.

    Returns:
        Structured incident summary with severity, service, symptom, blast radius,
        confidence, root cause, urgency, and tags.
    """
    from sentinel.models.raw_alert import RawAlert
    from sentinel.triage.engine import triage_alert as _triage

    # Parse timestamp
    if timestamp:
        ts = datetime.fromisoformat(timestamp)
    else:
        ts = datetime.now(UTC)

    # Build RawAlert
    alert = RawAlert(
        source=source,
        timestamp=ts,
        raw_payload=raw_payload,
        metadata=metadata or {},
    )

    # Tool enrichment: extract service → call ownership/deploys/deps
    enrichment_block: str | None = None
    if _tool_provider:
        service_name = extract_service_name(alert)
        if service_name:
            unavailable_tools: list[str] = []
            owner = None
            deploys: tuple = ()
            deps = None
            resolved_service = service_name

            try:
                owner = _tool_provider.get_service_owner(service_name)
                if owner:
                    resolved_service = owner.service
                else:
                    unavailable_tools.append("ownership")
            except Exception as e:
                logger.warning(f"MCP: Ownership lookup failed: {e}")
                unavailable_tools.append("ownership")

            try:
                deploys = _tool_provider.get_recent_deploys(
                    resolved_service,
                    since=ts - timedelta(hours=2),
                    limit=5,
                )
            except Exception as e:
                logger.warning(f"MCP: Deploys lookup failed: {e}")
                unavailable_tools.append("deploys")

            try:
                deps = _tool_provider.get_service_dependencies(resolved_service)
            except Exception as e:
                logger.warning(f"MCP: Dependencies lookup failed: {e}")
                unavailable_tools.append("dependencies")

            enrichment = EnrichmentContext(
                service_owner=owner,
                recent_deploys=list(deploys),
                service_dependencies=deps,
                unavailable_tools=unavailable_tools,
            )
            enrichment_block = format_context_block(enrichment)
            logger.info(f"MCP: Tool enrichment for service={resolved_service}")

    # Run triage with module-level retriever (None if init failed)
    incident = _triage(alert, _settings, retriever=_retriever, enrichment_block=enrichment_block)

    # Return as serializable dict
    return incident.model_dump(mode="json")


@mcp.tool()
def list_sources() -> list[str]:
    """List supported alert source formats.

    Returns the monitoring systems Sentinel can ingest alerts from.
    """
    from sentinel.models.enums import AlertSource

    return [s.value for s in AlertSource]


@mcp.tool()
def get_severity_scale() -> dict[str, str]:
    """Get Sentinel's severity classification scale.

    Returns the severity levels and their definitions, useful for understanding
    how triage decisions are made.
    """
    return {
        "critical": "Full outage or >50% error rate. Revenue/data loss imminent.",
        "high": "Partial outage or >10% error rate. Team-wide response needed.",
        "medium": "Degraded performance. Needs attention within hours.",
        "low": "Warning threshold breach. Informational or next-day.",
    }


def main() -> None:
    """Run the MCP server."""
    transport = "stdio"
    if "--sse" in sys.argv:
        transport = "sse"

    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
