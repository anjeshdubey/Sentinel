"""Functional tests for GET /scenarios."""

from __future__ import annotations

import httpx
import pytest

from backend.demo_endpoints import FIXTURE_FILES, SCENARIOS


async def test_returns_all_four_scenarios(client: httpx.AsyncClient) -> None:
    response = await client.get("/scenarios")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 4
    assert body == SCENARIOS


async def test_scenario_ids_match_fixture_files_exactly(client: httpx.AsyncClient) -> None:
    """SCENARIOS and FIXTURE_FILES are two separately hand-maintained
    structures in demo_endpoints.py -- this pins that they stay in sync."""
    response = await client.get("/scenarios")

    scenario_ids = {s["id"] for s in response.json()}
    assert scenario_ids == set(FIXTURE_FILES.keys())


@pytest.mark.parametrize(
    "field", ["id", "title", "severity_hint", "description", "why_interesting"]
)
async def test_every_scenario_has_required_fields(client: httpx.AsyncClient, field: str) -> None:
    response = await client.get("/scenarios")

    for scenario in response.json():
        assert field in scenario
        assert scenario[field]
