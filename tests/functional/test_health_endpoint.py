"""Functional tests for GET /health."""

from __future__ import annotations

import httpx
import pytest

import backend.guardrails as guardrails
from backend.demo_endpoints import SENTINEL_VERSION


async def test_health_returns_expected_shape(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["version"] == SENTINEL_VERSION
    assert "budget_used_pct" in body
    assert "cache_hit_rate_24h" in body


async def test_budget_used_pct_reflects_budget_tracker(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guardrails.budget_tracker, "used_pct", lambda: 42.5)

    response = await client.get("/health")

    assert response.json()["budget_used_pct"] == 42.5
