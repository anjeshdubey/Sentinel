"""Tests for sentinel.ingestion.service_extraction.extract_service_name.

Priority order under test (per the module docstring):
  1. metadata["service"]
  2. raw_payload["incident"]["service"]["name"]  (PagerDuty shape)
  3. metadata["tags"] entries matching "service:xyz"
  4. raw_payload["tags"] entries matching "service:xyz"
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentinel.ingestion.service_extraction import extract_service_name
from sentinel.models.raw_alert import RawAlert
from sentinel.observability.trace import EventType, TraceCollector


def _alert(raw_payload: dict, metadata: dict | None = None) -> RawAlert:
    return RawAlert(
        source="pagerduty",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        raw_payload=raw_payload,
        metadata=metadata or {},
    )


def test_metadata_service_has_highest_priority() -> None:
    alert = _alert(
        raw_payload={
            "incident": {"service": {"name": "payments"}},
            "tags": ["service:search"],
        },
        metadata={"service": "checkout", "tags": ["service:auth"]},
    )

    assert extract_service_name(alert) == "checkout"


def test_pagerduty_nested_service_wins_over_tags() -> None:
    alert = _alert(
        raw_payload={
            "incident": {"service": {"name": "Payments"}},
            "tags": ["service:search"],
        },
        metadata={"tags": ["service:auth"]},
    )

    assert extract_service_name(alert) == "payments"


def test_metadata_tags_win_over_payload_tags() -> None:
    alert = _alert(
        raw_payload={"tags": ["service:search"]},
        metadata={"tags": ["service:auth"]},
    )

    assert extract_service_name(alert) == "auth"


def test_payload_tags_used_as_last_resort() -> None:
    alert = _alert(raw_payload={"tags": ["service:search"]})

    assert extract_service_name(alert) == "search"


def test_no_source_matches_returns_none() -> None:
    alert = _alert(raw_payload={"message": "something happened"})

    assert extract_service_name(alert) is None


def test_invalid_metadata_service_falls_through_to_next_source() -> None:
    alert = _alert(
        raw_payload={"incident": {"service": {"name": "payments"}}},
        metadata={"service": "invalid service name"},
    )

    assert extract_service_name(alert) == "payments"


def test_overlength_pagerduty_service_name_falls_through() -> None:
    alert = _alert(
        raw_payload={"incident": {"service": {"name": "a" * 65}}},
        metadata={"tags": ["service:auth"]},
    )

    assert extract_service_name(alert) == "auth"


def test_tag_matching_is_case_insensitive() -> None:
    alert = _alert(raw_payload={"tags": ["SERVICE:Search"]})

    assert extract_service_name(alert) == "search"


def test_malformed_tag_entries_are_skipped() -> None:
    alert = _alert(raw_payload={"tags": [123, None, "not-a-service-tag", "service:search"]})

    assert extract_service_name(alert) == "search"


def test_path_traversal_like_service_name_rejected() -> None:
    alert = _alert(raw_payload={"incident": {"service": {"name": "../../etc/passwd"}}})

    assert extract_service_name(alert) is None


def test_non_dict_incident_service_ignored() -> None:
    alert = _alert(
        raw_payload={"incident": {"service": "not-a-dict"}, "tags": ["service:search"]},
    )

    assert extract_service_name(alert) == "search"


def test_tags_present_but_none_match_pattern_returns_none() -> None:
    alert = _alert(raw_payload={"tags": ["env:prod", "region:us-east-1"]})

    assert extract_service_name(alert) is None


@pytest.mark.parametrize(
    ("raw_payload", "metadata", "expected_source"),
    [
        ({}, {"service": "checkout"}, "metadata"),
        ({"incident": {"service": {"name": "payments"}}}, {}, "pagerduty"),
        ({}, {"tags": ["service:auth"]}, "metadata_tags"),
        ({"tags": ["service:search"]}, {}, "payload_tags"),
    ],
)
def test_collector_receives_service_extracted_event_with_correct_source(
    raw_payload: dict, metadata: dict, expected_source: str
) -> None:
    alert = _alert(raw_payload=raw_payload, metadata=metadata)
    collector = TraceCollector()

    extract_service_name(alert, collector=collector)

    assert len(collector.events) == 1
    event = collector.events[0]
    assert event.event_type == EventType.SERVICE_EXTRACTED
    assert event.metadata["source"] == expected_source


def test_no_collector_event_emitted_when_no_match() -> None:
    alert = _alert(raw_payload={"message": "nothing relevant"})
    collector = TraceCollector()

    extract_service_name(alert, collector=collector)

    assert collector.events == []
