"""Shared fixtures for the Sentinel test suite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest


@pytest.fixture
def valid_incident_kwargs() -> dict[str, Any]:
    """Minimal set of valid kwargs for constructing an IncidentSummary.

    Tests should override individual keys to exercise specific validators.
    """
    return {
        "incident_id": "11111111-1111-1111-1111-111111111111",
        "title": "Checkout returning 500s",
        "severity": "critical",
        "service": "checkout",
        "environment": "prod",
        "symptom": "Checkout returning HTTP 500 at 90% error rate.",
        "blast_radius": ["us-east-1"],
        "confidence": 0.9,
        "suspected_root_cause": "Recent deploy",
        "raw_alert_hash": "a" * 64,
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "triage_timestamp": datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        "suggested_urgency": "immediate",
        "tags": ["database"],
    }


@pytest.fixture
def valid_raw_alert_kwargs() -> dict[str, Any]:
    """Minimal set of valid kwargs for constructing a RawAlert."""
    return {
        "source": "pagerduty",
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "raw_payload": {"message": "checkout returning 500s"},
        "metadata": {},
    }
