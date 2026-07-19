"""Shared fixtures for FastAPI functional tests against backend.demo_app.app.

Importing backend.demo_app pulls in backend.guardrails' module-level
singletons (rate_limiter, budget_tracker), which are shared mutable state
across the whole test process. Every test gets a clean rate limiter so
request counts from one test never leak into another; budget_tracker's
methods are monkeypatched per-test rather than trusted to read a real
on-disk file.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import AsyncIterator

import httpx
import pytest

import backend.demo_endpoints as demo_endpoints
import backend.guardrails as guardrails
from backend.demo_app import app


@pytest.fixture(autouse=True)
def _reset_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guardrails.rate_limiter, "_requests", defaultdict(deque))


@pytest.fixture(autouse=True)
def _reset_graph_checkpointer() -> None:
    """Fresh in-process MemorySaver per test so paused threads never leak across
    tests. Cheap to rebuild (unlike the settings/retriever singletons, which are
    intentionally left intact)."""
    demo_endpoints._checkpointer = None
    yield
    demo_endpoints._checkpointer = None


@pytest.fixture(autouse=True)
def _budget_not_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to a healthy budget; tests that want the
    exhausted/warning path override these explicitly."""
    monkeypatch.setattr(guardrails.budget_tracker, "is_exhausted", lambda: False)
    monkeypatch.setattr(guardrails.budget_tracker, "used_pct", lambda: 0.0)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as ac:
        yield ac
