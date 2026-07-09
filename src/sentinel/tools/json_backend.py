"""JSON backend providers for fake CMDB — Month 2 local data.

All providers load data once at construction (in-memory dict after load)
and serve reads from memory. The async signatures exist so these backends
are protocol-compatible with future HTTP-backed real backends without any
caller change.

Data files live under src/sentinel/data/cmdb/:
- ownership.json: service ownership records with aliases
- deploys.json: deploy events
- dependencies.json: service dependency graphs
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sentinel.tools.errors import ToolBackendError
from sentinel.tools.models import Deploy, ServiceDependencies, ServiceOwner


class JsonOwnershipProvider:
    """Loads ownership.json once; all reads are in-memory dict lookups.

    Supports alias resolution: exact match first, then alias scan.
    """

    def __init__(self, data_dir: Path) -> None:
        path = data_dir / "ownership.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ToolBackendError("ownership", f"failed to load {path}: {e}") from e

        self._by_service: dict[str, ServiceOwner] = {}
        self._alias_map: dict[str, str] = {}  # alias -> canonical service name

        for row in raw:
            owner = ServiceOwner(**row)
            self._by_service[owner.service] = owner
            for alias in owner.aliases:
                self._alias_map[alias.lower()] = owner.service

    async def get_service_owner(self, service: str) -> ServiceOwner | None:
        """Resolve service name and return owner.

        Resolution order:
        1. Exact match against canonical service name
        2. Case-insensitive alias scan
        """
        # Exact match first
        owner = self._by_service.get(service)
        if owner is not None:
            return owner

        # Alias resolution (case-insensitive)
        canonical = self._alias_map.get(service.lower())
        if canonical is not None:
            return self._by_service.get(canonical)

        return None


class JsonDeploysProvider:
    """Loads deploys.json once; serves filtered queries from memory.

    Returns deploys ordered newest-first, filtered by `since` datetime.
    """

    def __init__(self, data_dir: Path) -> None:
        path = data_dir / "deploys.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ToolBackendError("deploys", f"failed to load {path}: {e}") from e

        self._by_service: dict[str, list[Deploy]] = {}
        for row in raw:
            d = Deploy(**row)
            self._by_service.setdefault(d.service, []).append(d)

        # Pre-sort each service's deploys newest-first
        for deploys in self._by_service.values():
            deploys.sort(key=lambda d: d.deployed_at, reverse=True)

    async def get_recent_deploys(
        self, service: str, since: datetime, limit: int = 5
    ) -> list[Deploy]:
        """Return deploys for `service` deployed at or after `since`, newest-first.

        Returns empty list if service is unknown or has no deploys in window.
        """
        deploys = self._by_service.get(service, [])
        # Filter by since datetime and apply limit
        return [d for d in deploys if d.deployed_at >= since][:limit]


class JsonDependenciesProvider:
    """Loads dependencies.json once; serves lookups from memory."""

    def __init__(self, data_dir: Path) -> None:
        path = data_dir / "dependencies.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ToolBackendError(
                "dependencies", f"failed to load {path}: {e}"
            ) from e

        self._by_service: dict[str, ServiceDependencies] = {}
        for row in raw:
            deps = ServiceDependencies(**row)
            self._by_service[deps.service] = deps

    async def get_service_dependencies(
        self, service: str
    ) -> ServiceDependencies | None:
        """Return dependency graph for service, or None if unknown."""
        return self._by_service.get(service)
