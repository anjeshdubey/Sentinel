"""Functional tests for POST /triage/resume (Week 5 HITL).

A low-confidence /triage/stream run pauses at the human gate; the frontend POSTs
the correlation_id back here with a decision to finalize the run. These verify
resume validation, the approve/reject outcomes, cache replay, and the graceful
409 when the paused in-process thread is gone.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import backend.demo_endpoints as demo_endpoints
from tests.functional.fakes import (
    FakeDeploy,
    FakeGraphRetriever,
    FakeOwner,
    FakeToolProvider,
    make_summary,
)


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    frames = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        frames.append((event, data))
    return frames


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(demo_endpoints, "CACHE_DIR", tmp_path)
    return tmp_path


def _wire(monkeypatch: pytest.MonkeyPatch, summary) -> None:
    monkeypatch.setattr(
        demo_endpoints, "_get_settings", lambda: demo_endpoints.load_settings()
    )
    monkeypatch.setattr(demo_endpoints, "_get_retriever", lambda: FakeGraphRetriever())
    monkeypatch.setattr(
        demo_endpoints,
        "_get_tool_provider",
        lambda: FakeToolProvider(owner=FakeOwner(), deploys=[FakeDeploy()]),
    )
    monkeypatch.setattr(
        "sentinel.triage.nodes.diagnose_incident", lambda *a, **k: summary
    )


async def _stream_to_pause(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, remediation: str | None
) -> str:
    """Run a low-confidence scenario to the human gate; return its correlation_id."""
    _wire(monkeypatch, make_summary(confidence=0.4, proposed_remediation=remediation))
    response = await client.post(
        "/triage/stream", json={"scenario_id": "checkout-deploy"}
    )
    frames = dict(_parse_sse(response.text))
    return frames["interrupt"]["correlation_id"]


class TestResumeValidation:
    async def test_invalid_scenario_id_returns_400(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/triage/resume",
            json={"scenario_id": "nope", "correlation_id": "x", "decision": "approve"},
        )
        assert response.status_code == 400

    async def test_invalid_decision_returns_400(
        self, client: httpx.AsyncClient, cache_dir: Path
    ) -> None:
        response = await client.post(
            "/triage/resume",
            json={
                "scenario_id": "checkout-deploy",
                "correlation_id": "x",
                "decision": "maybe",
            },
        )
        assert response.status_code == 400
        assert "decision" in response.text.lower()


class TestResumeApprove:
    async def test_resume_approve_finalizes_and_keeps_remediation(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        correlation_id = await _stream_to_pause(
            client, monkeypatch, "Roll back deploy #4821."
        )

        response = await client.post(
            "/triage/resume",
            json={
                "scenario_id": "checkout-deploy",
                "correlation_id": correlation_id,
                "decision": "approve",
            },
        )

        assert response.status_code == 200
        frames = _parse_sse(response.text)
        event_types = [f[0] for f in frames]
        assert event_types[-1] == "done"
        assert "finalized" in event_types

        finalized = dict(frames)["finalized"]
        assert finalized["approval_status"] == "approved"
        assert finalized["requires_human_approval"] is True
        assert finalized["proposed_remediation"] == "Roll back deploy #4821."


class TestResumeReject:
    async def test_resume_reject_does_not_surface_remediation(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        correlation_id = await _stream_to_pause(
            client, monkeypatch, "Roll back deploy #4821."
        )

        response = await client.post(
            "/triage/resume",
            json={
                "scenario_id": "checkout-deploy",
                "correlation_id": correlation_id,
                "decision": "reject",
                "note": "not the right call",
            },
        )

        assert response.status_code == 200
        finalized = dict(_parse_sse(response.text))["finalized"]
        assert finalized["approval_status"] == "rejected"
        assert finalized["proposed_remediation"] is None


class TestResumeSessionExpired:
    async def test_missing_thread_returns_409(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        # Wire fakes so the graph builds cheaply, but never stream -> no live thread.
        _wire(monkeypatch, make_summary(confidence=0.4))

        response = await client.post(
            "/triage/resume",
            json={
                "scenario_id": "checkout-deploy",
                "correlation_id": "checkout-deploy:doesnotexist",
                "decision": "approve",
            },
        )

        assert response.status_code == 409
        body = response.json()
        assert body["detail"]["error"] == "session_expired"
        assert body["detail"]["correlation_id"] == "checkout-deploy:doesnotexist"


class TestResumeCacheHit:
    async def test_cached_resume_segment_replays_without_live_thread(
        self, client: httpx.AsyncClient, cache_dir: Path
    ) -> None:
        seeded = [
            {"event": "finalized", "data": {"approval_status": "approved"}},
            {"event": "done", "data": {}},
        ]
        cache_path = demo_endpoints._resume_cache_path("checkout-deploy", "approve")
        cache_path.write_text(json.dumps(seeded))

        response = await client.post(
            "/triage/resume",
            json={
                "scenario_id": "checkout-deploy",
                "correlation_id": "irrelevant-when-cached",
                "decision": "approve",
            },
        )

        assert response.status_code == 200
        frames = _parse_sse(response.text)
        assert frames[0][0] == "start"
        assert frames[0][1]["resumed"] is True
        assert ("finalized", {"approval_status": "approved"}) in frames
        assert frames[-1] == ("done", {})
