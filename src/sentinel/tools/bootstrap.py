"""Tool bootstrap composition root — builds the full ToolProvider.

This is the single place where backend implementations are selected and
wired together. Engine code (triage_alert) is unchanged across backend
switches. The entire "real backend swap" story:
- One new file (real_backend.py)
- One new elif here
- Zero touches to callers

Usage:
    from sentinel.tools.bootstrap import build_tool_provider, build_sync_facade

    provider = build_tool_provider(settings)
    sync_provider = build_sync_facade(provider)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sentinel.tools.cache import ToolCache
from sentinel.tools.json_backend import (
    JsonDependenciesProvider,
    JsonDeploysProvider,
    JsonOwnershipProvider,
)
from sentinel.tools.protocols import PROTOCOL_VERSION
from sentinel.tools.provider import SyncToolProvider, ToolProvider
from sentinel.tools.stubs import StubEscalationAdvisor, StubResolutionRecorder
from sentinel.tools.vector_backend import AsyncQdrantPastIncidentsProvider


def build_tool_provider(
    settings: Any,
    retrieval_client: Any = None,
    embedder: Any = None,
) -> ToolProvider:
    """Construct a fully wired ToolProvider based on settings.

    Args:
        settings: Sentinel Settings object (must have .tools config).
        retrieval_client: Optional Qdrant client for past-incidents queries.
        embedder: Optional embedding function for vector search.

    Returns:
        Configured ToolProvider with all six backends wired.
    """
    tools_config = settings.tools

    # Build cache
    cache = ToolCache(
        ttl_by_tool=tools_config.cache.ttl_seconds,
        max_entries=tools_config.cache.max_entries,
        single_flight=tools_config.cache.single_flight,
    )

    # Resolve data directory
    data_dir = Path(tools_config.data_dir)

    if tools_config.backend == "json":
        return ToolProvider(
            ownership=JsonOwnershipProvider(data_dir),
            deploys=JsonDeploysProvider(data_dir),
            dependencies=JsonDependenciesProvider(data_dir),
            past_incidents=AsyncQdrantPastIncidentsProvider(
                client=retrieval_client,
                collection_name="incidents_v1",
                embedder=embedder,
            ),
            resolution=StubResolutionRecorder(),
            escalation=StubEscalationAdvisor(),
            timeouts_ms=tools_config.timeouts_ms,
            cache=cache,
            backend_protocol_version=PROTOCOL_VERSION,
        )
    else:
        # Future: "real" backend for Month 5
        raise ValueError(
            f"Unknown tools backend: {tools_config.backend!r}. "
            f"Supported: 'json'. Real backends available in Month 5."
        )


def build_sync_facade(provider: ToolProvider) -> SyncToolProvider:
    """Wrap an async ToolProvider in a synchronous facade for CLI usage.

    Args:
        provider: Async ToolProvider instance.

    Returns:
        SyncToolProvider wrapping each method in asyncio.run().
    """
    return SyncToolProvider(provider)
