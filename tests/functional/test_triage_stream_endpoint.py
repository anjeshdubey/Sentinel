"""Functional tests for POST /triage/stream (Week 5: graph-driven).

External services (LLM, retrieval, tool backends) are faked by monkeypatching
demo_endpoints' singletons + sentinel.triage.nodes.diagnose_incident -- these
verify the endpoint's own wiring (validation, budget/rate guardrails, caching,
graph-driven SSE translation, the interrupt frame), not the triage pipeline
itself (covered in tests/unit/triage/ and test_triage_engine_pipeline.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import backend.demo_endpoints as demo_endpoints
import backend.guardrails as guardrails
from sentinel.config import Settings
from sentinel.tools.errors import ToolBackendError
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


def _node_starts(frames: list[tuple[str, dict]]) -> list[str]:
    return [data["node"] for event, data in frames if event == "node_start"]


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(demo_endpoints, "CACHE_DIR", tmp_path)
    return tmp_path


def _wire_fake_graph(
    monkeypatch: pytest.MonkeyPatch,
    *,
    summary=None,
    tool_provider: FakeToolProvider | None = None,
    retriever=None,
    diagnose_raises: Exception | None = None,
) -> None:
    """Point the graph nodes at fakes: real Settings, a RAG-emitting retriever,
    a fake tool provider, and a stubbed diagnosis (canned summary or raiser)."""
    monkeypatch.setattr(
        demo_endpoints, "_get_settings", lambda: demo_endpoints.load_settings()
    )
    monkeypatch.setattr(
        demo_endpoints, "_get_retriever", lambda: retriever or FakeGraphRetriever()
    )
    monkeypatch.setattr(
        demo_endpoints,
        "_get_tool_provider",
        lambda: tool_provider or FakeToolProvider(),
    )

    if diagnose_raises is not None:

        def _raise(*args: object, **kwargs: object):
            raise diagnose_raises

        monkeypatch.setattr("sentinel.triage.nodes.diagnose_incident", _raise)
    else:
        canned = summary if summary is not None else make_summary()
        monkeypatch.setattr(
            "sentinel.triage.nodes.diagnose_incident", lambda *a, **k: canned
        )


class TestScenarioValidation:
    async def test_invalid_scenario_id_returns_400(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/triage/stream", json={"scenario_id": "not-a-scenario"}
        )

        assert response.status_code == 400
        assert (
            "checkout-deploy" in response.text
        )  # allowlist echoed in the error detail


class TestBudgetExhaustion:
    async def test_exhausted_budget_returns_503(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(guardrails.budget_tracker, "is_exhausted", lambda: True)

        response = await client.post(
            "/triage/stream", json={"scenario_id": "checkout-deploy"}
        )

        assert response.status_code == 503
        body = response.json()
        assert body["detail"]["error"] == "budget_exhausted"
        assert body["detail"]["cached_traces_available"] is True


class TestCacheHitPath:
    async def test_replays_cached_events_verbatim_without_running_graph(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        seeded_events = [
            {"event": "alert", "data": {"source": "pagerduty"}},
            {"event": "done", "data": {}},
        ]
        cache_path = demo_endpoints._cache_path("checkout-deploy")
        cache_path.write_text(json.dumps(seeded_events))

        async def _should_not_be_called(
            scenario_id: str, correlation_id: str
        ) -> list[dict]:
            raise AssertionError("cache hit path must not run the graph")

        monkeypatch.setattr(
            demo_endpoints, "_run_graph_and_collect_events", _should_not_be_called
        )

        response = await client.post(
            "/triage/stream", json={"scenario_id": "checkout-deploy"}
        )

        assert response.status_code == 200
        frames = _parse_sse(response.text)
        assert frames[0][0] == "start"
        assert frames[0][1]["cache_hit"] is True
        assert frames[1:] == [("alert", {"source": "pagerduty"}), ("done", {})]


class TestCacheMissAutoPath:
    async def test_high_confidence_run_emits_full_node_sequence(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        summary = make_summary(
            confidence=0.95, proposed_remediation="Roll back deploy #4821."
        )
        _wire_fake_graph(
            monkeypatch,
            summary=summary,
            tool_provider=FakeToolProvider(owner=FakeOwner(), deploys=[FakeDeploy()]),
        )

        response = await client.post(
            "/triage/stream", json={"scenario_id": "checkout-deploy"}
        )

        assert response.status_code == 200
        frames = _parse_sse(response.text)
        event_types = [f[0] for f in frames]

        assert event_types == [
            "start",
            "alert",
            "node_start",  # ingest
            "node_start",  # enrich
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
            "node_start",  # retrieve
            "rag_query",
            "rag_results",
            "node_start",  # diagnose
            "llm_call",
            "diagnosis",
            "node_start",  # finalize
            "finalized",
            "done",
        ]
        assert _node_starts(frames) == [
            "ingest",
            "enrich",
            "retrieve",
            "diagnose",
            "finalize",
        ]
        assert frames[0][1]["cache_hit"] is False

        diagnosis = dict(frames)["diagnosis"]
        assert diagnosis["severity"] == summary.severity.value
        assert diagnosis["service"] == summary.service
        assert diagnosis["confidence"] == summary.confidence
        assert diagnosis["assigned_team"] == "team-checkout"

        finalized = dict(frames)["finalized"]
        assert finalized["approval_status"] == "auto"
        assert finalized["requires_human_approval"] is False

    async def test_result_is_written_to_cache(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        _wire_fake_graph(monkeypatch, tool_provider=FakeToolProvider(owner=FakeOwner()))

        await client.post("/triage/stream", json={"scenario_id": "checkout-deploy"})

        cache_path = demo_endpoints._cache_path("checkout-deploy")
        assert cache_path.exists()
        cached = json.loads(cache_path.read_text())
        assert cached[-1]["event"] == "done"

    async def test_no_owner_no_deploys_skips_tool_calls(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        """slack-vague has no clean service hint, so gather_context short-circuits
        before any tool call — but the pipeline still streams the other steps."""
        _wire_fake_graph(
            monkeypatch, tool_provider=FakeToolProvider(owner=None, deploys=[])
        )

        response = await client.post(
            "/triage/stream", json={"scenario_id": "slack-vague"}
        )

        frames = _parse_sse(response.text)
        event_types = [f[0] for f in frames]
        assert "tool_call" not in event_types
        assert "diagnosis" in event_types


class TestCacheMissHumanGatePath:
    async def test_low_confidence_run_pauses_with_interrupt(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        summary = make_summary(
            confidence=0.4, proposed_remediation="Roll back deploy #4821."
        )
        _wire_fake_graph(
            monkeypatch,
            summary=summary,
            tool_provider=FakeToolProvider(owner=FakeOwner(), deploys=[FakeDeploy()]),
        )

        response = await client.post(
            "/triage/stream", json={"scenario_id": "checkout-deploy"}
        )

        assert response.status_code == 200
        frames = _parse_sse(response.text)
        event_types = [f[0] for f in frames]

        assert event_types[-1] == "interrupt"
        assert "finalized" not in event_types
        assert "done" not in event_types

        interrupt = dict(frames)["interrupt"]
        assert interrupt["correlation_id"].startswith("checkout-deploy:")
        assert interrupt["proposed_remediation"] == "Roll back deploy #4821."
        assert interrupt["assigned_team"] == "team-checkout"


class TestToolErrorsFailOpen:
    async def test_owner_tool_error_yields_none_result_and_no_assigned_team(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        _wire_fake_graph(
            monkeypatch,
            tool_provider=FakeToolProvider(
                deploys=[FakeDeploy()],
                owner_raises=ToolBackendError("ownership", "boom"),
            ),
        )

        response = await client.post(
            "/triage/stream", json={"scenario_id": "checkout-deploy"}
        )

        assert response.status_code == 200
        frames = dict(_parse_sse(response.text))
        assert frames["diagnosis"]["assigned_team"] is None

    async def test_deploys_tool_error_yields_empty_deploys_result(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        _wire_fake_graph(
            monkeypatch,
            tool_provider=FakeToolProvider(
                owner=FakeOwner(), deploys_raises=ToolBackendError("deploys", "boom")
            ),
        )

        response = await client.post(
            "/triage/stream", json={"scenario_id": "checkout-deploy"}
        )

        assert response.status_code == 200
        frames = _parse_sse(response.text)
        deploys_results = [
            data["result"]
            for event, data in frames
            if event == "tool_result" and data.get("tool") == "get_recent_deploys"
        ]
        assert deploys_results == [[]]


class TestExecutionFailure:
    async def test_graph_exception_returns_500_and_does_not_cache(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
    ) -> None:
        _wire_fake_graph(monkeypatch, diagnose_raises=RuntimeError("LLM call failed"))

        response = await client.post(
            "/triage/stream", json={"scenario_id": "checkout-deploy"}
        )

        assert response.status_code == 500
        body = response.json()
        assert body["detail"]["error"] == "execution_failed"
        assert "LLM call failed" in body["detail"]["message"]

        cache_path = demo_endpoints._cache_path("checkout-deploy")
        assert not cache_path.exists()


class TestBodySizeCap:
    async def test_oversized_body_returns_413(self, client: httpx.AsyncClient) -> None:
        oversized_payload = {"scenario_id": "checkout-deploy", "padding": "x" * 2000}

        response = await client.post("/triage/stream", json=oversized_payload)

        assert response.status_code == 413
        assert response.json()["error"] == "payload_too_large"


class TestRateLimiting:
    async def test_sixth_request_in_short_window_is_denied(
        self, client: httpx.AsyncClient
    ) -> None:
        for _ in range(5):
            response = await client.post(
                "/triage/stream", json={"scenario_id": "not-a-scenario"}
            )
            assert response.status_code == 400

        response = await client.post(
            "/triage/stream", json={"scenario_id": "not-a-scenario"}
        )

        assert response.status_code == 429
        body = response.json()
        assert body["error"] == "rate_limit_exceeded"
        assert "Retry-After" in response.headers


class TestCors:
    async def test_cors_header_present_on_response(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/health", headers={"Origin": "https://example.com"}
        )

        assert response.headers.get("access-control-allow-origin") == "*"


class TestSettingsSingleton:
    def test_get_settings_returns_real_settings_instance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(demo_endpoints, "_settings", None)

        settings = demo_endpoints._get_settings()

        assert isinstance(settings, Settings)
