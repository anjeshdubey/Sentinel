"""Unit tests for the LangGraph triage nodes (Week 5 PR 3).

Each node is exercised in isolation against a plain state dict + fake deps; the
LLM boundary (diagnose_incident) is monkeypatched. No graph is built here — that
is test_graph.py's job.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentinel.config import ModelConfig, Settings
from sentinel.models.incident import IncidentSummary
from sentinel.models.raw_alert import RawAlert
from sentinel.tools.models import Deploy, ServiceOwner
from sentinel.triage import nodes
from sentinel.triage.nodes import (
    CONFIDENCE_THRESHOLD,
    TriageDeps,
    build_initial_state,
    diagnose,
    enrich,
    finalize,
    human_gate,
    ingest,
    needs_human_review,
    retrieve,
)
from tests.unit.triage.fakes import FakeRetriever

TS = datetime(2026, 7, 13, 10, 0, 0, tzinfo=UTC)


def make_settings() -> Settings:
    return Settings(
        model=ModelConfig(
            provider="anthropic",
            default="claude-sonnet",
            temperature=0.0,
            max_tokens=1024,
        )
    )


def make_alert(payload: dict | None = None) -> RawAlert:
    return RawAlert(
        source="pagerduty",
        timestamp=TS,
        raw_payload=(
            payload
            if payload is not None
            else {"service": "checkout", "message": "500s"}
        ),
        metadata={},
    )


def make_summary(**overrides: object) -> IncidentSummary:
    base: dict = {
        "incident_id": "INC-1",
        "title": "Checkout returning 500s",
        "severity": "critical",
        "service": "checkout",
        "environment": "prod",
        "symptom": "Checkout returning HTTP 500 at 90% error rate.",
        "confidence": 0.9,
        "raw_alert_hash": "h" * 64,
        "timestamp": TS,
        "triage_timestamp": TS,
        "suggested_urgency": "immediate",
        "proposed_remediation": "Roll back deploy #4821 per runbook rb-1.",
    }
    base.update(overrides)
    return IncidentSummary(**base)


class _FakeProvider:
    def __init__(
        self, owner: ServiceOwner | None = None, deploys: list[Deploy] | None = None
    ) -> None:
        self._owner = owner
        self._deploys = list(deploys or [])

    async def get_service_owner(self, service: str) -> ServiceOwner | None:
        return self._owner

    async def get_recent_deploys(
        self, service: str, since, limit: int = 5
    ) -> list[Deploy]:
        return list(self._deploys)


class TestIngest:
    def test_builds_rawalert_from_dict(self) -> None:
        state = {
            "raw_alert": {
                "source": "pagerduty",
                "timestamp": TS,
                "raw_payload": {"a": 1},
            }
        }
        delta = ingest(state, TriageDeps(make_settings()))
        assert isinstance(delta["alert"], RawAlert)
        assert delta["alert"].raw_payload == {"a": 1}

    def test_passes_through_existing_rawalert(self) -> None:
        alert = make_alert()
        delta = ingest({"raw_alert": alert}, TriageDeps(make_settings()))
        assert delta["alert"] is alert


class TestRetrieve:
    def test_none_retriever_yields_no_chunks(self) -> None:
        delta = retrieve(
            {"alert": make_alert()}, TriageDeps(make_settings(), retriever=None)
        )
        assert delta["runbook_chunks"] is None

    def test_passes_chunks_from_retriever(self) -> None:
        retriever = FakeRetriever(chunks=[])  # empty -> engine returns None
        delta = retrieve(
            {"alert": make_alert()}, TriageDeps(make_settings(), retriever=retriever)
        )
        assert delta["runbook_chunks"] is None
        assert len(retriever.calls) == 1


class TestEnrich:
    async def test_no_tool_provider_skips_enrichment(self) -> None:
        delta = await enrich(
            {"alert": make_alert()}, TriageDeps(make_settings(), tool_provider=None)
        )
        assert delta == {"enrichment_block": None, "owner_team": None}

    async def test_resolves_owner_and_block(self) -> None:
        owner = ServiceOwner(
            service="checkout-api",
            team="payments",
            tier=0,
            escalation_channel="#payments-oncall",
            aliases=["checkout"],
        )
        deploy = Deploy(
            service="checkout-api",
            version="v2.15.0",
            deployed_at=datetime(2026, 7, 13, 9, 0, 0, tzinfo=UTC),
            deployed_by="bob",
            change_summary="Increase pool timeout",
        )
        deps = TriageDeps(
            make_settings(), tool_provider=_FakeProvider(owner=owner, deploys=[deploy])
        )

        delta = await enrich({"alert": make_alert()}, deps)

        assert delta["owner_team"] == "payments"
        assert "Service owner: payments (tier 0)" in delta["enrichment_block"]
        assert "v2.15.0" in delta["enrichment_block"]


class TestDiagnose:
    def test_auto_route_for_high_confidence_with_remediation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        summary = make_summary(confidence=0.95, proposed_remediation="Roll back.")
        monkeypatch.setattr(nodes, "diagnose_incident", lambda *a, **k: summary)

        delta = diagnose({"alert": make_alert()}, TriageDeps(make_settings()))

        assert delta["summary"] is summary
        assert delta["requires_human_approval"] is False
        assert delta["route"] == "auto"

    def test_needs_human_for_low_confidence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        summary = make_summary(confidence=0.4, proposed_remediation="Roll back.")
        monkeypatch.setattr(nodes, "diagnose_incident", lambda *a, **k: summary)

        delta = diagnose({"alert": make_alert()}, TriageDeps(make_settings()))

        assert delta["requires_human_approval"] is True
        assert delta["route"] == "needs_human"

    def test_needs_human_for_high_confidence_without_remediation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        summary = make_summary(confidence=0.99, proposed_remediation=None)
        monkeypatch.setattr(nodes, "diagnose_incident", lambda *a, **k: summary)

        delta = diagnose({"alert": make_alert()}, TriageDeps(make_settings()))

        assert delta["requires_human_approval"] is True
        assert delta["route"] == "needs_human"


class TestNeedsHumanReview:
    def test_threshold_value(self) -> None:
        assert CONFIDENCE_THRESHOLD == 0.80

    def test_at_threshold_with_remediation_is_auto(self) -> None:
        assert (
            needs_human_review(make_summary(confidence=0.80, proposed_remediation="x"))
            is False
        )

    def test_just_below_threshold_needs_human(self) -> None:
        assert (
            needs_human_review(make_summary(confidence=0.79, proposed_remediation="x"))
            is True
        )

    def test_no_remediation_always_needs_human(self) -> None:
        assert (
            needs_human_review(make_summary(confidence=1.0, proposed_remediation=None))
            is True
        )


class TestHumanGate:
    @pytest.mark.parametrize("decision", ["approve", "approved", "APPROVE"])
    def test_approve_variants(self, decision: str) -> None:
        delta = human_gate({"human_decision": decision}, TriageDeps(make_settings()))
        assert delta["approval_status"] == "approved"

    @pytest.mark.parametrize("decision", ["reject", "rejected", "Reject"])
    def test_reject_variants(self, decision: str) -> None:
        delta = human_gate({"human_decision": decision}, TriageDeps(make_settings()))
        assert delta["approval_status"] == "rejected"

    def test_missing_decision_stays_pending(self) -> None:
        delta = human_gate({}, TriageDeps(make_settings()))
        assert delta["approval_status"] == "pending"


class TestFinalize:
    def test_auto_path_sets_auto_and_keeps_remediation(self) -> None:
        summary = make_summary(proposed_remediation="Roll back deploy #4821.")
        state = {"summary": summary, "route": "auto", "approval_status": "pending"}

        delta = finalize(state, TriageDeps(make_settings()))

        final = delta["final_summary"]
        assert final.approval_status == "auto"
        assert final.requires_human_approval is False
        assert final.proposed_remediation == "Roll back deploy #4821."

    def test_approved_keeps_remediation_actionable(self) -> None:
        summary = make_summary(proposed_remediation="Roll back deploy #4821.")
        state = {
            "summary": summary,
            "route": "needs_human",
            "approval_status": "approved",
        }

        final = finalize(state, TriageDeps(make_settings()))["final_summary"]

        assert final.approval_status == "approved"
        assert final.requires_human_approval is True
        assert final.proposed_remediation == "Roll back deploy #4821."

    def test_rejected_clears_remediation(self) -> None:
        summary = make_summary(proposed_remediation="Roll back deploy #4821.")
        state = {
            "summary": summary,
            "route": "needs_human",
            "approval_status": "rejected",
        }

        final = finalize(state, TriageDeps(make_settings()))["final_summary"]

        assert final.approval_status == "rejected"
        assert final.proposed_remediation is None

    def test_gated_without_decision_never_auto_approves(self) -> None:
        """route needs_human + still-pending status must not become 'auto'."""
        state = {
            "summary": make_summary(),
            "route": "needs_human",
            "approval_status": "pending",
        }

        final = finalize(state, TriageDeps(make_settings()))["final_summary"]

        assert final.approval_status == "pending"
        assert final.requires_human_approval is True


class TestBuildInitialState:
    def test_seeds_pending_approval(self) -> None:
        alert = make_alert()
        state = build_initial_state(alert, "corr-1")
        assert state["raw_alert"] is alert
        assert state["correlation_id"] == "corr-1"
        assert state["approval_status"] == "pending"
        assert state["requires_human_approval"] is True
