"""Shared enumerations for Sentinel models."""

from enum import StrEnum


class AlertSource(StrEnum):
    """Origin system of the raw alert."""

    PAGERDUTY = "pagerduty"
    GRAFANA = "grafana"
    DATADOG = "datadog"
    SLACK = "slack"
    MANUAL = "manual"


class Severity(StrEnum):
    """Incident severity levels.

    - critical: Full outage or >50% error rate. Revenue/data loss imminent.
    - high: Partial outage or >10% error rate. Team-wide response needed.
    - medium: Degraded performance. Needs attention within hours.
    - low: Warning threshold breach. Informational, monitor or next-day.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Urgency(StrEnum):
    """Suggested response urgency."""

    IMMEDIATE = "immediate"
    NEXT_HOUR = "next_hour"
    NEXT_DAY = "next_day"
    MONITOR = "monitor"
