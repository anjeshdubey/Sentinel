"""Tests for sentinel.tools.json_backend (fake CMDB providers).

Every test builds its own tmp_path data directory -- never touches the real
src/sentinel/data/cmdb/ fixtures.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentinel.tools.errors import ToolBackendError
from sentinel.tools.json_backend import (
    JsonDependenciesProvider,
    JsonDeploysProvider,
    JsonOwnershipProvider,
)


@pytest.fixture
def ownership_dir(tmp_path: Path) -> Path:
    (tmp_path / "ownership.json").write_text(
        json.dumps(
            [
                {
                    "service": "checkout",
                    "team": "team-a",
                    "tier": 1,
                    "escalation_channel": "#checkout-oncall",
                    "aliases": ["checkout-api", "Checkout Service"],
                }
            ]
        )
    )
    return tmp_path


@pytest.fixture
def deploys_dir(tmp_path: Path) -> Path:
    (tmp_path / "deploys.json").write_text(
        json.dumps(
            [
                {
                    "service": "checkout",
                    "version": "v1",
                    "deployed_at": "2026-01-01T00:00:00Z",
                    "deployed_by": "alice",
                },
                {
                    "service": "checkout",
                    "version": "v2",
                    "deployed_at": "2026-01-03T00:00:00Z",
                    "deployed_by": "bob",
                },
                {
                    "service": "checkout",
                    "version": "v0",
                    "deployed_at": "2025-12-01T00:00:00Z",
                    "deployed_by": "carol",
                },
            ]
        )
    )
    return tmp_path


@pytest.fixture
def dependencies_dir(tmp_path: Path) -> Path:
    (tmp_path / "dependencies.json").write_text(
        json.dumps(
            [{"service": "checkout", "upstream": ["auth"], "downstream": ["notification-service"]}]
        )
    )
    return tmp_path


class TestJsonOwnershipProvider:
    async def test_exact_match(self, ownership_dir: Path) -> None:
        provider = JsonOwnershipProvider(ownership_dir)

        owner = await provider.get_service_owner("checkout")

        assert owner is not None
        assert owner.team == "team-a"

    async def test_alias_resolution_is_case_insensitive(self, ownership_dir: Path) -> None:
        provider = JsonOwnershipProvider(ownership_dir)

        owner = await provider.get_service_owner("CHECKOUT SERVICE")

        assert owner is not None
        assert owner.service == "checkout"

    async def test_alias_exact_case(self, ownership_dir: Path) -> None:
        provider = JsonOwnershipProvider(ownership_dir)

        owner = await provider.get_service_owner("checkout-api")

        assert owner is not None
        assert owner.service == "checkout"

    async def test_unknown_service_returns_none(self, ownership_dir: Path) -> None:
        provider = JsonOwnershipProvider(ownership_dir)

        assert await provider.get_service_owner("unknown-svc") is None

    def test_malformed_json_raises_tool_backend_error(self, tmp_path: Path) -> None:
        (tmp_path / "ownership.json").write_text("not valid json")

        with pytest.raises(ToolBackendError) as exc_info:
            JsonOwnershipProvider(tmp_path)

        assert exc_info.value.tool == "ownership"

    def test_missing_file_raises_tool_backend_error(self, tmp_path: Path) -> None:
        with pytest.raises(ToolBackendError):
            JsonOwnershipProvider(tmp_path)


class TestJsonDeploysProvider:
    async def test_filters_by_since_and_orders_newest_first(self, deploys_dir: Path) -> None:
        provider = JsonDeploysProvider(deploys_dir)

        deploys = await provider.get_recent_deploys(
            "checkout", since=datetime(2025, 12, 15, tzinfo=UTC), limit=5
        )

        assert [d.version for d in deploys] == ["v2", "v1"]

    async def test_limit_truncates_results(self, deploys_dir: Path) -> None:
        provider = JsonDeploysProvider(deploys_dir)

        deploys = await provider.get_recent_deploys(
            "checkout", since=datetime(2025, 12, 15, tzinfo=UTC), limit=1
        )

        assert [d.version for d in deploys] == ["v2"]

    async def test_since_excludes_older_deploys(self, deploys_dir: Path) -> None:
        provider = JsonDeploysProvider(deploys_dir)

        deploys = await provider.get_recent_deploys(
            "checkout", since=datetime(2026, 1, 2, tzinfo=UTC), limit=5
        )

        assert [d.version for d in deploys] == ["v2"]

    async def test_since_is_inclusive_of_exact_deployed_at_timestamp(
        self, deploys_dir: Path
    ) -> None:
        """The filter is `deployed_at >= since` -- a deploy exactly at `since`
        must be included, not excluded."""
        provider = JsonDeploysProvider(deploys_dir)

        deploys = await provider.get_recent_deploys(
            "checkout", since=datetime(2026, 1, 1, tzinfo=UTC), limit=5
        )

        assert [d.version for d in deploys] == ["v2", "v1"]

    async def test_unknown_service_returns_empty_list(self, deploys_dir: Path) -> None:
        provider = JsonDeploysProvider(deploys_dir)

        deploys = await provider.get_recent_deploys(
            "unknown", since=datetime(2020, 1, 1, tzinfo=UTC)
        )

        assert deploys == []

    def test_malformed_json_raises_tool_backend_error(self, tmp_path: Path) -> None:
        (tmp_path / "deploys.json").write_text("not valid json")

        with pytest.raises(ToolBackendError) as exc_info:
            JsonDeploysProvider(tmp_path)

        assert exc_info.value.tool == "deploys"


class TestJsonDependenciesProvider:
    async def test_known_service_lookup(self, dependencies_dir: Path) -> None:
        provider = JsonDependenciesProvider(dependencies_dir)

        deps = await provider.get_service_dependencies("checkout")

        assert deps is not None
        assert deps.upstream == ["auth"]
        assert deps.downstream == ["notification-service"]

    async def test_unknown_service_returns_none(self, dependencies_dir: Path) -> None:
        provider = JsonDependenciesProvider(dependencies_dir)

        assert await provider.get_service_dependencies("unknown") is None

    def test_malformed_json_raises_tool_backend_error(self, tmp_path: Path) -> None:
        (tmp_path / "dependencies.json").write_text("not valid json")

        with pytest.raises(ToolBackendError) as exc_info:
            JsonDependenciesProvider(tmp_path)

        assert exc_info.value.tool == "dependencies"
