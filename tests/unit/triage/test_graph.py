"""Tests for the compiled triage graph (Week 5 PR 3).

Runs the real graph end-to-end with a monkeypatched diagnosis so routing,
interrupt, and resume are deterministic and offline (no LLM, no retriever, no
tool provider). Covers the PRD acceptance criteria for the headless graph.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentinel.config import ModelConfig, Settings
from sentinel.models.incident import IncidentSummary
from sentinel.models.raw_alert import RawAlert
from sentinel.triage import nodes
from sentinel.triage.engine import resume_triage_graph, run_triage_graph
from sentinel.triage.graph import build_graph, route_on_confidence
from sentinel.triage.nodes import TriageDeps

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


def make_alert() -> RawAlert:
    return RawAlert(
        source="pagerduty",
        timestamp=TS,
        raw_payload={"service": "checkout", "message": "500s"},
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


def _patch_diagnosis(monkeypatch: pytest.MonkeyPatch, summary: IncidentSummary) -> None:
    monkeypatch.setattr(nodes, "diagnose_incident", lambda *a, **k: summary)


def _deps() -> TriageDeps:
    # No retriever / tool provider: retrieve + enrich become no-ops, so only the
    # (patched) diagnosis drives routing.
    return TriageDeps(make_settings(), retriever=None, tool_provider=None)


class TestCompile:
    def test_compiles_with_checkpointer_and_interrupt_before_approve(self) -> None:
        graph = build_graph(_deps())
        assert graph.checkpointer is not None
        assert list(graph.interrupt_before_nodes) == ["approve"]


class TestRouteOnConfidence:
    def test_high_confidence_with_remediation_routes_to_finalize(self) -> None:
        state = {"summary": make_summary(confidence=0.95, proposed_remediation="x")}
        assert route_on_confidence(state) == "finalize"

    def test_low_confidence_routes_to_approve(self) -> None:
        state = {"summary": make_summary(confidence=0.4, proposed_remediation="x")}
        assert route_on_confidence(state) == "approve"

    def test_high_confidence_without_remediation_routes_to_approve(self) -> None:
        state = {"summary": make_summary(confidence=0.99, proposed_remediation=None)}
        assert route_on_confidence(state) == "approve"


class TestAutoApprovePath:
    async def test_high_confidence_finalizes_without_interrupt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_diagnosis(
            monkeypatch,
            make_summary(confidence=0.95, proposed_remediation="Roll back."),
        )

        _graph, result = await run_triage_graph(
            make_alert(), _deps(), correlation_id="auto-1"
        )

        assert result.interrupted is False
        assert result.final_summary is not None
        assert result.approval_status == "auto"
        assert result.final_summary.requires_human_approval is False


class TestHumanGatePath:
    async def test_low_confidence_pauses_before_approve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_diagnosis(monkeypatch, make_summary(confidence=0.4))

        graph, result = await run_triage_graph(
            make_alert(), _deps(), correlation_id="hg-1"
        )

        assert result.interrupted is True
        assert result.final_summary is None  # finalize hasn't run
        assert result.summary is not None  # diagnosis is retrievable
        assert result.approval_status == "pending"

    async def test_paused_state_retrievable_by_thread_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_diagnosis(monkeypatch, make_summary(confidence=0.4))

        graph, _result = await run_triage_graph(
            make_alert(), _deps(), correlation_id="hg-2"
        )

        snapshot = graph.get_state({"configurable": {"thread_id": "hg-2"}})
        assert snapshot.next == ("approve",)
        assert snapshot.values["summary"].confidence == 0.4
        assert "final_summary" not in snapshot.values

    async def test_high_confidence_without_remediation_forces_human(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_diagnosis(
            monkeypatch, make_summary(confidence=0.99, proposed_remediation=None)
        )

        _graph, result = await run_triage_graph(
            make_alert(), _deps(), correlation_id="hg-3"
        )

        assert result.interrupted is True
        assert result.final_summary is None


class TestResume:
    async def test_resume_approve_finalizes_approved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_diagnosis(
            monkeypatch,
            make_summary(confidence=0.4, proposed_remediation="Roll back #4821."),
        )
        graph, run = await run_triage_graph(
            make_alert(), _deps(), correlation_id="res-appr"
        )
        assert run.interrupted

        resumed = await resume_triage_graph(
            graph, correlation_id="res-appr", human_decision="approve"
        )

        assert resumed.interrupted is False
        assert resumed.approval_status == "approved"
        assert resumed.final_summary.proposed_remediation == "Roll back #4821."
        assert resumed.final_summary.requires_human_approval is True

    async def test_resume_reject_does_not_surface_remediation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_diagnosis(
            monkeypatch,
            make_summary(confidence=0.4, proposed_remediation="Roll back #4821."),
        )
        graph, run = await run_triage_graph(
            make_alert(), _deps(), correlation_id="res-rej"
        )
        assert run.interrupted

        resumed = await resume_triage_graph(
            graph,
            correlation_id="res-rej",
            human_decision="reject",
            human_note="not now",
        )

        assert resumed.approval_status == "rejected"
        assert resumed.final_summary.proposed_remediation is None

    async def test_prebuilt_graph_is_reused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passing a graph into run_triage_graph returns that same instance so a
        caller can hold one graph across run + resume (PR 4's singleton)."""
        _patch_diagnosis(monkeypatch, make_summary(confidence=0.4))
        graph = build_graph(_deps())

        returned, result = await run_triage_graph(
            make_alert(), _deps(), correlation_id="res-reuse", graph=graph
        )

        assert returned is graph
        assert result.interrupted is True
