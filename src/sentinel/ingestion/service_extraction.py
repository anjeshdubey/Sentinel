"""Service name extraction from raw alerts.

Extracts the originating service name from alert payloads using multiple
heuristic sources in priority order. This is the first step in the enrichment
pipeline — downstream tool calls (ownership, deploys, dependencies) all key
on the extracted service name.

Examples:
    >>> from sentinel.models.raw_alert import RawAlert
    >>> alert = RawAlert(source="pagerduty", timestamp="2026-01-01T00:00:00Z",
    ...     raw_payload={"incident": {"service": {"name": "Checkout-API"}}},
    ...     metadata={})
    >>> extract_service_name(alert)
    'checkout-api'

    >>> alert2 = RawAlert(source="datadog", timestamp="2026-01-01T00:00:00Z",
    ...     raw_payload={"tags": ["service:inventory", "env:prod"]},
    ...     metadata={"service": "inventory-api"})
    >>> extract_service_name(alert2)
    'inventory-api'
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentinel.models.raw_alert import RawAlert
    from sentinel.observability.trace import TraceCollector

# Pattern for "service:xyz" in tag lists
_SERVICE_TAG_RE = re.compile(r"^service:(.+)$", re.IGNORECASE)

# Service names must be alphanumeric with hyphens/underscores, max 64 chars
_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")


def extract_service_name(
    alert: RawAlert, collector: TraceCollector | None = None
) -> str | None:
    """Extract the service name from a raw alert, checking multiple sources.

    Priority order:
      1. alert.metadata["service"] — explicit routing metadata
      2. PagerDuty: alert.raw_payload["incident"]["service"]["name"]
      3. Tags in metadata: alert.metadata["tags"] list, pattern "service:xyz"
      4. Tags in raw_payload: alert.raw_payload["tags"] list, pattern "service:xyz"

    Args:
        alert: Validated RawAlert input.
        collector: Optional TraceCollector for event emission.

    Returns:
        Lowercase service name, or None if no source yields a match.
    """
    # 1. Explicit metadata field
    service = alert.metadata.get("service")
    if service and isinstance(service, str):
        validated = _validate_service_name(service.strip().lower())
        if validated:
            if collector:
                from sentinel.observability.trace import EventType
                collector.emit(EventType.SERVICE_EXTRACTED, service_name=validated, source="metadata")
            return validated

    # 2. PagerDuty incident.service.name
    incident = alert.raw_payload.get("incident")
    if isinstance(incident, dict):
        pd_service = incident.get("service")
        if isinstance(pd_service, dict):
            name = pd_service.get("name")
            if name and isinstance(name, str):
                validated = _validate_service_name(name.strip().lower())
                if validated:
                    if collector:
                        from sentinel.observability.trace import EventType
                        collector.emit(EventType.SERVICE_EXTRACTED, service_name=validated, source="pagerduty")
                    return validated

    # 3. Tags in metadata (list of "service:xyz" strings)
    meta_tags = alert.metadata.get("tags")
    if isinstance(meta_tags, list):
        found = _find_service_in_tags(meta_tags)
        if found:
            if collector:
                from sentinel.observability.trace import EventType
                collector.emit(EventType.SERVICE_EXTRACTED, service_name=found, source="metadata_tags")
            return found

    # 4. Tags in raw_payload (Datadog convention)
    payload_tags = alert.raw_payload.get("tags")
    if isinstance(payload_tags, list):
        found = _find_service_in_tags(payload_tags)
        if found:
            if collector:
                from sentinel.observability.trace import EventType
                collector.emit(EventType.SERVICE_EXTRACTED, service_name=found, source="payload_tags")
            return found

    return None


def _validate_service_name(name: str) -> str | None:
    """Validate and sanitize a service name.

    Returns the name if valid, None if it looks malicious or malformed.
    """
    if not _SERVICE_NAME_RE.match(name):
        return None
    return name


def _find_service_in_tags(tags: list) -> str | None:
    """Scan a tag list for the first valid "service:xyz" pattern."""
    for tag in tags:
        if not isinstance(tag, str):
            continue
        match = _SERVICE_TAG_RE.match(tag)
        if match:
            candidate = match.group(1).strip().lower()
            validated = _validate_service_name(candidate)
            if validated:
                return validated
    return None
