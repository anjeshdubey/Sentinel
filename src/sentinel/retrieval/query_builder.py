"""Query builder for retrieval — constructs search queries from alert data."""

from __future__ import annotations

import json
import re
from typing import Any

from sentinel.models.incident import KNOWN_SERVICES

# Known injection prefixes to strip from query text
_INJECTION_PREFIXES = re.compile(
    r"(ignore\s+previous|disregard|system:|forget\s+all|override|you\s+are\s+now)",
    re.IGNORECASE,
)

# Characters to strip for sanitization
_UNSAFE_CHARS = re.compile(r'[<>"\'`]')


def _sanitize_query(text: str) -> str:
    """Strip unsafe characters and known injection prefixes from query text.

    This is NOT the primary injection defense (that's the score threshold +
    delimited block), but a first-pass filter to reduce noise.
    """
    # Strip unsafe characters
    text = _UNSAFE_CHARS.sub("", text)
    # Remove known injection prefixes
    text = _INJECTION_PREFIXES.sub("", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_symptom_heuristic(raw_payload: dict[str, Any]) -> str:
    """Extract a symptom string from raw_payload using heuristics.

    Looks for common alert payload fields that contain symptom info.
    Falls back to a flat string representation.
    """
    # Check common symptom-bearing fields
    symptom_fields = [
        "message",
        "description",
        "error",
        "summary",
        "title",
        "text",
        "alert_name",
        "alertname",
        "body",
        "details",
    ]

    for field in symptom_fields:
        if field in raw_payload:
            val = raw_payload[field]
            if isinstance(val, str) and val.strip():
                return val.strip()[:300]

    # Check nested structures (common in PagerDuty, Datadog)
    for key in ("event", "alert", "incident", "data"):
        if key in raw_payload and isinstance(raw_payload[key], dict):
            nested = raw_payload[key]
            for field in symptom_fields:
                if field in nested:
                    val = nested[field]
                    if isinstance(val, str) and val.strip():
                        return val.strip()[:300]

    # Fallback: flatten to string
    flat = json.dumps(raw_payload, default=str)
    return flat[:300]


def _guess_service(raw_payload: dict[str, Any], metadata: dict[str, Any] | None = None) -> str | None:
    """Guess the service name from alert payload and metadata.

    Strategy:
    1. Check metadata for explicit service field.
    2. Check raw_payload for service-related fields.
    3. Regex match against KNOWN_SERVICES in the payload text.
    """
    # Check metadata first
    if metadata:
        for key in ("service", "service_name", "source_service"):
            if key in metadata:
                val = str(metadata[key]).lower().strip().replace(" ", "-").replace("_", "-")
                if val:
                    return val

    # Check raw_payload fields
    for key in ("service", "service_name", "source", "component", "host_service"):
        if key in raw_payload:
            val = str(raw_payload[key]).lower().strip().replace(" ", "-").replace("_", "-")
            if val:
                return val

    # Regex match against known services in payload text
    payload_text = json.dumps(raw_payload, default=str).lower()
    for service in sorted(KNOWN_SERVICES, key=len, reverse=True):
        if service in payload_text:
            return service

    return None


def build_retrieval_query(
    raw_payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    title: str | None = None,
) -> str:
    """Build a compact query string from alert fields for vector retrieval.

    Fields used: title (if present), symptom extract (first 300 chars of
    relevant payload field), service hint (via _guess_service regex).

    Max length: 600 chars (approximately 150 tokens). Enforced via truncation.

    Args:
        raw_payload: The raw alert payload dict.
        metadata: Optional alert metadata dict.
        title: Optional alert title.

    Returns:
        Sanitized query string, max 600 characters.
    """
    parts: list[str] = []

    # Title (if available)
    if title:
        parts.append(title[:200])

    # Symptom extract from payload
    symptom = _extract_symptom_heuristic(raw_payload)
    if symptom:
        parts.append(symptom[:300])

    # Service hint
    service = _guess_service(raw_payload, metadata)
    if service:
        parts.append(service)

    query = " ".join(parts)
    return _sanitize_query(query)[:600]
