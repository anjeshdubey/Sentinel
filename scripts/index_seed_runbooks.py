#!/usr/bin/env python3
"""Index seed runbooks into the Qdrant vector store.

Usage:
    python scripts/index_seed_runbooks.py [--runbook-dir data/runbooks] [--qdrant-path ./data/qdrant]

This script:
1. Initializes the BgeSmallEmbedder (downloads model on first run)
2. Creates/connects to the embedded Qdrant store
3. Indexes all .md files in the runbook directory
4. Reports indexing statistics
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sentinel.config import load_settings
from sentinel.retrieval.embedder import BgeSmallEmbedder
from sentinel.retrieval.indexer import IncidentIndexer, RunbookIndexer
from sentinel.retrieval.store import QdrantStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Index seed runbooks into Qdrant")
    parser.add_argument(
        "--runbook-dir",
        type=Path,
        default=Path("data/runbooks"),
        help="Directory containing runbook .md files",
    )
    parser.add_argument(
        "--qdrant-path",
        type=str,
        default=None,
        help="Path for embedded Qdrant storage (overrides config)",
    )
    args = parser.parse_args()

    # Load settings
    settings = load_settings()

    # Resolve paths
    runbook_dir = args.runbook_dir.resolve()
    qdrant_path = args.qdrant_path or settings.qdrant.path

    if not runbook_dir.exists():
        logger.error("Runbook directory does not exist: %s", runbook_dir)
        sys.exit(1)

    logger.info("Initializing BGE-small embedder...")
    embedder = BgeSmallEmbedder(model_name=settings.retrieval.embedding_model)
    logger.info("Embedder ready (dim=%d)", embedder.dim)

    logger.info("Connecting to Qdrant (mode=%s, path=%s)...", settings.qdrant.mode, qdrant_path)
    store = QdrantStore(
        mode=settings.qdrant.mode,
        path=qdrant_path,
        url=settings.qdrant.url,
        embedding_model=settings.retrieval.embedding_model,
        embedding_dim=settings.retrieval.embedding_dim,
    )

    # Index runbooks
    logger.info("Indexing runbooks from: %s", runbook_dir)
    indexer = RunbookIndexer(
        store=store,
        embedder=embedder,
        collection_name=settings.qdrant.runbook_collection,
        chunk_size_tokens=settings.retrieval.chunk_size_tokens,
        chunk_overlap_tokens=settings.retrieval.chunk_overlap_tokens,
    )
    num_indexed = indexer.index(runbook_dir)
    logger.info("Runbook indexing complete: %d new chunks", num_indexed)

    # Create incidents collection (empty, ready for future writes)
    logger.info("Ensuring incidents collection exists...")
    incident_indexer = IncidentIndexer(
        store=store,
        embedder=embedder,
        collection_name=settings.qdrant.incident_collection,
    )
    incident_indexer.ensure_collection()

    # Seed a few sample incidents for testing
    sample_incidents = [
        {
            "incident_id": "INC-2026-001",
            "title": "Checkout API 503 due to connection pool exhaustion",
            "service": "checkout",
            "severity": "critical",
            "symptom": "HTTP 503 errors on /charge endpoint at 87% error rate",
            "environment": "prod",
            "resolved_summary": "Increased connection pool max from 50 to 100, restarted pods",
            "occurred_at": "2026-06-01T14:30:00Z",
        },
        {
            "incident_id": "INC-2026-002",
            "title": "Auth service timeout causing cascading failures",
            "service": "auth",
            "severity": "high",
            "symptom": "Token validation timing out after 5s, all auth-dependent services failing",
            "environment": "prod",
            "resolved_summary": "Redis session store was OOM, restarted with increased memory",
            "occurred_at": "2026-06-10T09:15:00Z",
        },
        {
            "incident_id": "INC-2026-003",
            "title": "Order processing queue backup after schema change",
            "service": "order-service",
            "severity": "high",
            "symptom": "Consumer lag growing >100k messages, orders stuck in processing",
            "environment": "prod",
            "resolved_summary": "Rolled back producer to previous schema version, replayed DLQ",
            "occurred_at": "2026-06-15T16:45:00Z",
        },
        {
            "incident_id": "INC-2026-004",
            "title": "Search index corruption after failed reindex",
            "service": "search",
            "severity": "medium",
            "symptom": "Search returning empty results for 30% of queries",
            "environment": "prod",
            "resolved_summary": "Switched alias back to previous good index, re-triggered reindex",
            "occurred_at": "2026-06-20T11:00:00Z",
        },
        {
            "incident_id": "INC-2026-005",
            "title": "Inventory overselling due to cache invalidation failure",
            "service": "inventory",
            "severity": "high",
            "symptom": "Products shown in stock but warehouse reports zero quantity",
            "environment": "prod",
            "resolved_summary": "Flushed Redis cache, fixed pub/sub subscription for invalidation events",
            "occurred_at": "2026-06-25T08:30:00Z",
        },
    ]

    logger.info("Seeding %d sample incidents...", len(sample_incidents))
    incident_indexer.reindex(sample_incidents)

    # Report final state
    runbook_count = store.count(settings.qdrant.runbook_collection)
    incident_count = store.count(settings.qdrant.incident_collection)
    logger.info("Final state: %d runbook chunks, %d incidents indexed", runbook_count, incident_count)
    logger.info("Done. Qdrant data at: %s", qdrant_path)


if __name__ == "__main__":
    main()
