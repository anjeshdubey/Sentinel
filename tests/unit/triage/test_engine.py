"""Tests for sentinel.triage.engine.triage_alert.

The LLM boundary (extract_incident) is monkeypatched throughout -- these
tests verify the orchestration: hashing/ID generation, retrieval wiring,
prompt assembly, collector events, and how the LLM's LLMIncidentExtraction
combines with computed fields into the final IncidentSummary.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentinel.config import ModelConfig, Settings
from sentinel.models.raw_alert import RawAlert
from sentinel.observability.trace import EventType, TraceCollector
from sentinel.retrieval.models import RetrievedChunk, RunbookChunk
from sentinel.retrieval.query_builder import _guess_service, build_retrieval_query
from sentinel.triage.engine import triage_alert
from sentinel.triage.extractor import LLMIncidentExtraction
from sentinel.utils.hashing import generate_incident_id, hash_payload
from tests.unit.triage.fakes import FakeRetriever, make_extraction


@pytest.fixture
def settings() -> Settings:
    return Settings(
        model=ModelConfig(
            provider="anthropic", default="claude-sonnet", temperature=0.0, max_tokens=1024
        )
    )


@pytest.fixture
def alert() -> RawAlert:
    return RawAlert(
        source="pagerduty",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        raw_payload={"message": "checkout returning 500s", "service": "checkout"},
        metadata={},
    )


def _patch_extract_incident(
    monkeypatch: pytest.MonkeyPatch,
    extraction: LLMIncidentExtraction | None = None,
    capture: dict | None = None,
) -> LLMIncidentExtraction:
    extraction = extraction or make_extraction()

    def _fake_extract_incident(**kwargs: object) -> LLMIncidentExtraction:
        if capture is not None:
            capture.update(kwargs)
        return extraction

    monkeypatch.setattr("sentinel.triage.engine.extract_incident", _fake_extract_incident)
    return extraction


class TestNoRetriever:
    def test_no_retrieval_call_when_retriever_is_none(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        captured: dict = {}
        _patch_extract_incident(monkeypatch, capture=captured)

        triage_alert(alert, settings, retriever=None)

        assert "(No matching runbooks found for this alert.)" in captured["user_prompt"]

    def test_result_combines_extraction_with_computed_fields(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        extraction = _patch_extract_incident(monkeypatch)

        incident = triage_alert(alert, settings, retriever=None)

        assert incident.title == extraction.title
        assert incident.severity == extraction.severity
        assert incident.service == extraction.service
        assert incident.environment == extraction.environment
        assert incident.symptom == extraction.symptom
        assert incident.blast_radius == extraction.blast_radius
        assert incident.confidence == extraction.confidence
        assert incident.suspected_root_cause == extraction.suspected_root_cause
        assert incident.suggested_urgency == extraction.suggested_urgency
        assert incident.tags == extraction.tags
        assert incident.timestamp == alert.timestamp
        assert incident.raw_alert_hash == hash_payload(alert.raw_payload)
        assert incident.incident_id == generate_incident_id(hash_payload(alert.raw_payload))

    def test_triage_timestamp_is_set_to_now(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        _patch_extract_incident(monkeypatch)

        before = datetime.now(UTC)
        incident = triage_alert(alert, settings, retriever=None)
        after = datetime.now(UTC)

        assert before <= incident.triage_timestamp <= after


class TestWithRetriever:
    def test_retriever_called_with_built_query_and_service_hint(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        _patch_extract_incident(monkeypatch)
        retriever = FakeRetriever(chunks=[])

        triage_alert(alert, settings, retriever=retriever)

        assert len(retriever.calls) == 1
        expected_text = build_retrieval_query(
            raw_payload=alert.raw_payload, metadata=alert.metadata
        )
        expected_service = _guess_service(alert.raw_payload, alert.metadata)
        assert retriever.calls[0]["text"] == expected_text
        assert retriever.calls[0]["service_filter"] == expected_service

    def test_retrieved_chunks_reach_the_prompt(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        captured: dict = {}
        _patch_extract_incident(monkeypatch, capture=captured)
        chunk = RetrievedChunk(
            chunk=RunbookChunk(
                runbook_id="rb-1",
                runbook_title="Checkout Runbook",
                chunk_index=0,
                chunk_total=1,
                text="UNIQUE_RUNBOOK_TEXT_MARKER",
                content_sha256="a" * 64,
            ),
            score=0.9,
        )
        retriever = FakeRetriever(chunks=[chunk])

        triage_alert(alert, settings, retriever=retriever)

        assert "UNIQUE_RUNBOOK_TEXT_MARKER" in captured["user_prompt"]

    def test_empty_chunk_list_falls_back_to_no_match_sentinel(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        captured: dict = {}
        _patch_extract_incident(monkeypatch, capture=captured)
        retriever = FakeRetriever(chunks=[])

        triage_alert(alert, settings, retriever=retriever)

        assert "(No matching runbooks found for this alert.)" in captured["user_prompt"]

    def test_collector_passed_through_to_retriever_query(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        _patch_extract_incident(monkeypatch)
        retriever = FakeRetriever(chunks=[])
        collector = TraceCollector()

        triage_alert(alert, settings, retriever=retriever, collector=collector)

        assert retriever.calls[0]["collector"] is collector


class TestEnrichmentBlock:
    def test_enrichment_block_reaches_the_prompt(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        captured: dict = {}
        _patch_extract_incident(monkeypatch, capture=captured)

        triage_alert(
            alert, settings, retriever=None, enrichment_block="Service owner: team-checkout"
        )

        assert "Service owner: team-checkout" in captured["user_prompt"]

    def test_no_enrichment_block_uses_no_data_sentinel(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        captured: dict = {}
        _patch_extract_incident(monkeypatch, capture=captured)

        triage_alert(alert, settings, retriever=None)

        assert "(No enrichment data available for this alert.)" in captured["user_prompt"]


class TestCollectorEvents:
    def test_events_emitted_in_order_with_expected_metadata(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        extraction = _patch_extract_incident(
            monkeypatch, extraction=make_extraction(title="x" * 100)
        )
        collector = TraceCollector()

        triage_alert(alert, settings, retriever=None, collector=collector)

        event_types = [e.event_type for e in collector.events]
        assert event_types == [
            EventType.LLM_CALL_STARTED,
            EventType.LLM_CALL_COMPLETED,
            EventType.EXTRACTION_COMPLETED,
        ]

        started, completed, extraction_completed = collector.events
        assert started.metadata["model"] == settings.model.default

        assert completed.metadata["model"] == settings.model.default
        assert completed.metadata["service"] == extraction.service
        assert completed.metadata["severity"] == extraction.severity.value

        assert extraction_completed.metadata["title"] == extraction.title[:80]
        assert len(extraction_completed.metadata["title"]) == 80
        assert extraction_completed.metadata["confidence"] == extraction.confidence

    def test_no_events_emitted_when_collector_is_none(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        _patch_extract_incident(monkeypatch)

        # Should not raise even though there's nowhere to record events.
        incident = triage_alert(alert, settings, retriever=None, collector=None)

        assert incident is not None


class TestDeterminism:
    def test_incident_id_and_hash_stable_across_calls_regardless_of_llm_output(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        _patch_extract_incident(monkeypatch, extraction=make_extraction(title="First title"))
        first = triage_alert(alert, settings, retriever=None)

        _patch_extract_incident(monkeypatch, extraction=make_extraction(title="Second title"))
        second = triage_alert(alert, settings, retriever=None)

        assert first.incident_id == second.incident_id
        assert first.raw_alert_hash == second.raw_alert_hash
        assert first.title != second.title

    def test_different_payloads_produce_different_incident_ids(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings, alert: RawAlert
    ) -> None:
        _patch_extract_incident(monkeypatch)
        first = triage_alert(alert, settings, retriever=None)

        other_alert = alert.model_copy(
            update={"raw_payload": {**alert.raw_payload, "message": "different symptom"}}
        )
        second = triage_alert(other_alert, settings, retriever=None)

        assert first.incident_id != second.incident_id
