"""Functional test: the full triage_alert() pipeline wired together.

Real components: sentinel.ingestion.loader (loads real fixture files),
sentinel.triage.engine.triage_alert, sentinel.triage.prompts, sentinel.config.
Faked external services: the LLM (extract_incident monkeypatched) and RAG
retrieval (FakeRetriever, no real Qdrant/embeddings). This is the
"multiple internal components wired together, external services faked"
functional tier from TEST_PLAN.md, one level up from the mocked-boundary
unit tests in tests/unit/triage/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.config import ModelConfig, Settings
from sentinel.ingestion.loader import load_from_file
from sentinel.retrieval.models import RetrievedChunk, RunbookChunk
from sentinel.triage.engine import triage_alert
from sentinel.triage.extractor import LLMIncidentExtraction
from tests.unit.triage.fakes import FakeRetriever, make_extraction

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "alerts"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        model=ModelConfig(
            provider="anthropic", default="claude-sonnet", temperature=0.0, max_tokens=1024
        )
    )


def _patch_extract_incident(
    monkeypatch: pytest.MonkeyPatch, extraction: LLMIncidentExtraction | None = None
) -> LLMIncidentExtraction:
    extraction = extraction or make_extraction()
    monkeypatch.setattr(
        "sentinel.triage.engine.extract_incident", lambda **kwargs: extraction
    )
    return extraction


class TestPipelineWithRealFixtureFiles:
    def test_checkout_alert_end_to_end_without_retrieval(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        extraction = _patch_extract_incident(
            monkeypatch, make_extraction(service="checkout", severity="critical")
        )
        alert = load_from_file(FIXTURES_DIR / "checkout-500s.json")

        incident = triage_alert(alert, settings, retriever=None)

        assert incident.service == "checkout"
        assert incident.service_recognized is True
        assert incident.severity == extraction.severity
        assert incident.raw_alert_hash
        assert incident.incident_id

    def test_vague_slack_alert_end_to_end_with_retrieval(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        _patch_extract_incident(
            monkeypatch, make_extraction(service="search", severity="low", confidence=0.3)
        )
        alert = load_from_file(FIXTURES_DIR / "vague-slack-report.json")
        chunk = RetrievedChunk(
            chunk=RunbookChunk(
                runbook_id="rb-search",
                runbook_title="Search Degradation Runbook",
                chunk_index=0,
                chunk_total=1,
                text="Check search cluster health first.",
                content_sha256="b" * 64,
            ),
            score=0.8,
        )
        retriever = FakeRetriever(chunks=[chunk])

        incident = triage_alert(alert, settings, retriever=retriever)

        assert incident.service == "search"
        assert incident.confidence == 0.3
        assert len(retriever.calls) == 1
        # The vague report's own text should have driven the retrieval query.
        assert "weird stuff" in retriever.calls[0]["text"] or "search" in retriever.calls[0]["text"]

    def test_two_different_fixture_alerts_produce_different_incident_ids(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        _patch_extract_incident(monkeypatch)
        checkout_alert = load_from_file(FIXTURES_DIR / "checkout-500s.json")
        slack_alert = load_from_file(FIXTURES_DIR / "vague-slack-report.json")

        checkout_incident = triage_alert(checkout_alert, settings, retriever=None)
        slack_incident = triage_alert(slack_alert, settings, retriever=None)

        assert checkout_incident.incident_id != slack_incident.incident_id
        assert checkout_incident.raw_alert_hash != slack_incident.raw_alert_hash

    def test_same_fixture_alert_is_deterministic_across_runs(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        _patch_extract_incident(monkeypatch)
        first_alert = load_from_file(FIXTURES_DIR / "checkout-500s.json")
        second_alert = load_from_file(FIXTURES_DIR / "checkout-500s.json")

        first = triage_alert(first_alert, settings, retriever=None)
        second = triage_alert(second_alert, settings, retriever=None)

        assert first.incident_id == second.incident_id
        assert first.raw_alert_hash == second.raw_alert_hash
