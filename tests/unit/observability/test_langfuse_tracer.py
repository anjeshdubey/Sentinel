"""Unit tests for LangfuseTracer / build_tracer (Week 5 PR 6).

The real `langfuse.Langfuse` client is monkeypatched with a fake so these run
offline with no network calls and no credentials, mirroring how the rest of
the suite keeps the LLM boundary mocked. Live confirmation that spans actually
land in a Langfuse project is a separate, manual check once real credentials
are configured.
"""

from __future__ import annotations

from typing import Any

import pytest

from sentinel.config import ObservabilityConfig
from sentinel.observability.langfuse_tracer import LangfuseTracer, build_tracer


class _FakeSpan:
    def __init__(self, name: str, output: dict) -> None:
        self.name = name
        self.output = output
        self.ended = False

    def end(self) -> None:
        self.ended = True


class _FakeLangfuseClient:
    """Records calls the same shapes LangfuseTracer makes against the real
    `langfuse.Langfuse` client."""

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.spans: list[_FakeSpan] = []
        self.flushed = False

    def create_trace_id(self, *, seed: str) -> str:
        return f"trace-{seed}"

    def start_observation(self, *, trace_context, name, as_type, output):
        span = _FakeSpan(name, output)
        self.spans.append(span)
        return span

    def flush(self) -> None:
        self.flushed = True


def _enabled_config(**overrides: Any) -> ObservabilityConfig:
    base = {
        "langfuse_enabled": True,
        "langfuse_public_key": "pk_test",
        "langfuse_secret_key": "sk_test",
    }
    base.update(overrides)
    return ObservabilityConfig(**base)


@pytest.fixture(autouse=True)
def fake_langfuse_sdk(monkeypatch: pytest.MonkeyPatch) -> _FakeLangfuseClient:
    """Patch the lazily-imported `langfuse` module so LangfuseTracer never
    touches the real SDK/network."""
    import sys
    import types

    created: list[_FakeLangfuseClient] = []

    def _make_client(**kwargs: Any) -> _FakeLangfuseClient:
        client = _FakeLangfuseClient(**kwargs)
        created.append(client)
        return client

    fake_module = types.ModuleType("langfuse")
    fake_module.Langfuse = _make_client  # type: ignore[attr-defined]
    fake_types_module = types.ModuleType("langfuse.types")
    fake_types_module.TraceContext = dict  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    monkeypatch.setitem(sys.modules, "langfuse.types", fake_types_module)

    return created


class TestBuildTracer:
    def test_disabled_returns_none(self) -> None:
        config = ObservabilityConfig(langfuse_enabled=False)
        assert build_tracer(config, "corr-1") is None

    def test_enabled_without_keys_returns_none(self) -> None:
        config = ObservabilityConfig(
            langfuse_enabled=True, langfuse_public_key="", langfuse_secret_key=""
        )
        assert build_tracer(config, "corr-1") is None

    def test_enabled_with_keys_returns_tracer(self) -> None:
        tracer = build_tracer(_enabled_config(), "corr-1")
        assert isinstance(tracer, LangfuseTracer)

    def test_broken_client_construction_degrades_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import langfuse

        def _boom(**kwargs: Any) -> Any:
            raise RuntimeError("network unreachable")

        monkeypatch.setattr(langfuse, "Langfuse", _boom)

        assert build_tracer(_enabled_config(), "corr-1") is None


class TestLangfuseTracer:
    def test_log_creates_and_ends_a_span_on_the_seeded_trace(self) -> None:
        tracer = LangfuseTracer(_enabled_config(), "corr-42")

        tracer.log("node_start", payload={"node": "ingest"})

        client: _FakeLangfuseClient = tracer._client  # noqa: SLF001
        assert len(client.spans) == 1
        span = client.spans[0]
        assert span.name == "node_start"
        assert span.output == {"node": "ingest"}
        assert span.ended is True

    def test_same_correlation_id_seeds_the_same_trace(self) -> None:
        a = LangfuseTracer(_enabled_config(), "corr-shared")
        b = LangfuseTracer(_enabled_config(), "corr-shared")

        assert a._trace_context == b._trace_context  # noqa: SLF001

    def test_log_swallows_exceptions(self) -> None:
        tracer = LangfuseTracer(_enabled_config(), "corr-1")

        def _boom(**kwargs: Any) -> Any:
            raise RuntimeError("langfuse is down")

        tracer._client.start_observation = _boom  # noqa: SLF001

        tracer.log("node_start", payload={"node": "ingest"})  # must not raise

    def test_flush_delegates_to_client(self) -> None:
        tracer = LangfuseTracer(_enabled_config(), "corr-1")

        tracer.flush()

        assert tracer._client.flushed is True  # noqa: SLF001

    def test_flush_swallows_exceptions(self) -> None:
        tracer = LangfuseTracer(_enabled_config(), "corr-1")

        def _boom() -> Any:
            raise RuntimeError("langfuse is down")

        tracer._client.flush = _boom  # noqa: SLF001

        tracer.flush()  # must not raise
