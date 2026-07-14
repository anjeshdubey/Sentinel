"""Enums are a serialization contract — renaming a value is a breaking API change."""

from sentinel.models.enums import AlertSource, Severity, Urgency


def test_alert_source_values() -> None:
    assert AlertSource.PAGERDUTY == "pagerduty"
    assert AlertSource.GRAFANA == "grafana"
    assert AlertSource.DATADOG == "datadog"
    assert AlertSource.SLACK == "slack"
    assert AlertSource.MANUAL == "manual"


def test_severity_values() -> None:
    assert Severity.CRITICAL == "critical"
    assert Severity.HIGH == "high"
    assert Severity.MEDIUM == "medium"
    assert Severity.LOW == "low"


def test_urgency_values() -> None:
    assert Urgency.IMMEDIATE == "immediate"
    assert Urgency.NEXT_HOUR == "next_hour"
    assert Urgency.NEXT_DAY == "next_day"
    assert Urgency.MONITOR == "monitor"


def test_enums_constructible_from_their_string_value() -> None:
    assert AlertSource("datadog") is AlertSource.DATADOG
    assert Severity("high") is Severity.HIGH
    assert Urgency("monitor") is Urgency.MONITOR
