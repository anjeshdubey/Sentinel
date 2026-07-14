"""Functional tests for POST /triage/stream.

External services (LLM, Qdrant/retrieval, tool backends) are faked via
monkeypatched demo_endpoints singletons and triage_alert -- these tests
verify the endpoint's own wiring (validation, budget/rate guardrails,
caching, SSE event translation), not the triage pipeline itself (covered
in tests/unit/triage/ and tests/functional/test_triage_engine_pipeline.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import backend.demo_endpoints as demo_endpoints
import backend.guardrails as guardrails
from sentinel.config import Settings
from sentinel.models.incident import IncidentSummary
from sentinel.tools.errors import ToolBackendError
from tests.functional.fakes import FakeDeploy, FakeOwner, FakeToolProvider, make_fake_triage_alert


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


@pytest.fixture
def fake_incident(valid_incident_kwargs: dict) -> IncidentSummary:
    return IncidentSummary.model_validate(
        {**valid_incident_kwargs, "service": "checkout", "severity": "critical"}
    )


def _wire_fake_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    incident: IncidentSummary,
    tool_provider: FakeToolProvider,
    emit_rag_events: bool = False,
) -> None:
    monkeypatch.setattr(demo_endpoints, "_get_settings", lambda: demo_endpoints.load_settings())
    monkeypatch.setattr(demo_endpoints, "_get_retriever", lambda: object())
    monkeypatch.setattr(demo_endpoints, "_get_tool_provider", lambda: tool_provider)
    monkeypatch.setattr(
        demo_endpoints, "triage_alert", make_fake_triage_alert(incident, emit_rag_events)
    )


class TestScenarioValidation:
    async def test_invalid_scenario_id_returns_400(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/triage/stream", json={"scenario_id": "not-a-scenario"})

        assert response.status_code == 400
        assert "checkout-deploy" in response.text  # allowlist echoed in the error detail


class TestBudgetExhaustion:
    async def test_exhausted_budget_returns_503(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(guardrails.budget_tracker, "is_exhausted", lambda: True)

        response = await client.post("/triage/stream", json={"scenario_id": "checkout-deploy"})

        assert response.status_code == 503
        body = response.json()
        assert body["detail"]["error"] == "budget_exhausted"
        assert body["detail"]["cached_traces_available"] is True


class TestCacheHitPath:
    async def test_replays_cached_events_verbatim_without_running_pipeline(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, cache_dir: Path
    ) -> None:
        seeded_events = [
            {"event": "alert", "data": {"source": "pagerduty"}},
            {"event": "done", "data": {}},
        ]
        cache_path = demo_endpoints._cache_path("checkout-deploy")
        cache_path.write_text(json.dumps(seeded_events))

        async def _should_not_be_called(scenario_id: str) -> list[dict]:
            raise AssertionError("cache hit path must not run the real pipeline")

        monkeypatch.setattr(
            demo_endpoints, "_run_triage_and_collect_events", _should_not_be_called
        )

        response = await client.post("/triage/stream", json={"scenario_id": "checkout-deploy"})

        assert response.status_code == 200
        frames = _parse_sse(response.text)
        assert frames[0][0] == "start"
        assert frames[0][1]["cache_hit"] is True
        assert frames[1:] == [("alert", {"source": "pagerduty"}), ("done", {})]


class TestCacheMissPath:
    async def test_runs_pipeline_and_emits_full_event_sequence(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
        fake_incident: IncidentSummary,
    ) -> None:
        tool_provider = FakeToolProvider(owner=FakeOwner(), deploys=[FakeDeploy()])
        _wire_fake_pipeline(monkeypatch, fake_incident, tool_provider, emit_rag_events=True)

        response = await client.post("/triage/stream", json={"scenario_id": "checkout-deploy"})

        assert response.status_code == 200
        frames = _parse_sse(response.text)
        event_types = [f[0] for f in frames]

        assert event_types == [
            "start",
            "alert",
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
            "llm_call",
            "rag_query",
            "rag_results",
            "diagnosis",
            "done",
        ]
        assert frames[0][1]["cache_hit"] is False

        diagnosis = dict(frames)["diagnosis"]
        assert diagnosis["severity"] == fake_incident.severity.value
        assert diagnosis["service"] == fake_incident.service
        assert diagnosis["confidence"] == fake_incident.confidence

    async def test_result_is_written_to_cache(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
        fake_incident: IncidentSummary,
    ) -> None:
        tool_provider = FakeToolProvider(owner=FakeOwner(), deploys=[])
        _wire_fake_pipeline(monkeypatch, fake_incident, tool_provider)

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
        fake_incident: IncidentSummary,
    ) -> None:
        """The slack-vague fixture has no clean service hint in its payload/
        metadata for _guess_service's structured-field checks and no known
        service substring, so the tool_call branch should be skipped."""
        tool_provider = FakeToolProvider(owner=None, deploys=[])
        _wire_fake_pipeline(monkeypatch, fake_incident, tool_provider)

        response = await client.post("/triage/stream", json={"scenario_id": "slack-vague"})

        frames = _parse_sse(response.text)
        event_types = [f[0] for f in frames]
        assert "tool_call" not in event_types


class TestToolErrorsFailOpen:
    """The endpoint catches ToolError at each tool call site and degrades
    gracefully (result=None / owner_team=None) rather than 500ing --
    enrichment is best-effort, never a hard dependency."""

    async def test_owner_tool_error_yields_none_result_and_no_assigned_team(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
        fake_incident: IncidentSummary,
    ) -> None:
        tool_provider = FakeToolProvider(
            deploys=[FakeDeploy()], owner_raises=ToolBackendError("ownership", "boom")
        )
        _wire_fake_pipeline(monkeypatch, fake_incident, tool_provider)

        response = await client.post("/triage/stream", json={"scenario_id": "checkout-deploy"})

        assert response.status_code == 200
        frames = dict(_parse_sse(response.text))
        assert frames["diagnosis"]["assigned_team"] is None

    async def test_deploys_tool_error_yields_empty_deploys_result(
        self,
        client: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        cache_dir: Path,
        fake_incident: IncidentSummary,
    ) -> None:
        tool_provider = FakeToolProvider(
            owner=FakeOwner(), deploys_raises=ToolBackendError("deploys", "boom")
        )
        _wire_fake_pipeline(monkeypatch, fake_incident, tool_provider)

        response = await client.post("/triage/stream", json={"scenario_id": "checkout-deploy"})

        assert response.status_code == 200
        frames = _parse_sse(response.text)
        deploys_results = [
            data["result"]
            for event, data in frames
            if event == "tool_result" and data.get("tool") == "get_recent_deploys"
        ]
        assert deploys_results == [[]]


class TestExecutionFailure:
    async def test_pipeline_exception_returns_500_and_does_not_cache(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, cache_dir: Path
    ) -> None:
        tool_provider = FakeToolProvider()
        monkeypatch.setattr(demo_endpoints, "_get_settings", lambda: demo_endpoints.load_settings())
        monkeypatch.setattr(demo_endpoints, "_get_retriever", lambda: object())
        monkeypatch.setattr(demo_endpoints, "_get_tool_provider", lambda: tool_provider)

        def _raising_triage_alert(*args: object, **kwargs: object) -> None:
            raise RuntimeError("LLM call failed")

        monkeypatch.setattr(demo_endpoints, "triage_alert", _raising_triage_alert)

        response = await client.post("/triage/stream", json={"scenario_id": "checkout-deploy"})

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
        """Rate limiting runs in middleware before route validation, so an
        invalid scenario_id still counts against the quota -- exercising it
        doesn't require mocking the pipeline."""
        for _ in range(5):
            response = await client.post(
                "/triage/stream", json={"scenario_id": "not-a-scenario"}
            )
            assert response.status_code == 400

        response = await client.post("/triage/stream", json={"scenario_id": "not-a-scenario"})

        assert response.status_code == 429
        body = response.json()
        assert body["error"] == "rate_limit_exceeded"
        assert "Retry-After" in response.headers


class TestCors:
    async def test_cors_header_present_on_response(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health", headers={"Origin": "https://example.com"})

        assert response.headers.get("access-control-allow-origin") == "*"


class TestSettingsSingleton:
    def test_get_settings_returns_real_settings_instance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(demo_endpoints, "_settings", None)

        settings = demo_endpoints._get_settings()

        assert isinstance(settings, Settings)
