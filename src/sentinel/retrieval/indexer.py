"""Runbook and incident indexers — idempotent upsert to Qdrant collections."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from sentinel.retrieval.chunking import split_runbook_file
from sentinel.retrieval.embedder import Embedder
from sentinel.retrieval.models import RunbookChunk
from sentinel.retrieval.store import QdrantStore

logger = logging.getLogger(__name__)


class RunbookIndexer:
    """Index runbook markdown files into the runbooks_v1 Qdrant collection.

    Idempotent: chunks with matching content_sha256 already in the
    collection are skipped. Creates payload indexes on service_tags
    and environment unconditionally.
    """

    def __init__(
        self,
        store: QdrantStore,
        embedder: Embedder,
        collection_name: str = "runbooks_v1",
        chunk_size_tokens: int = 512,
        chunk_overlap_tokens: int = 64,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._collection_name = collection_name
        self._chunk_size_tokens = chunk_size_tokens
        self._chunk_overlap_tokens = chunk_overlap_tokens

    def index(self, runbook_dir: Path) -> int:
        """Index all .md files in the given directory.

        Args:
            runbook_dir: Directory containing runbook markdown files.

        Returns:
            Number of new chunks indexed (skipping duplicates).
        """
        # Ensure collection exists with proper indexes
        self._store.ensure_collection(self._collection_name, dim=self._embedder.dim)
        logger.info(
            "Payload indexes on 'service_tags' and 'environment' registered for '%s'",
            self._collection_name,
        )

        # Find all markdown files
        md_files = sorted(runbook_dir.glob("*.md"))
        if not md_files:
            logger.warning("No .md files found in %s", runbook_dir)
            return 0

        logger.info("Found %d runbook files to index", len(md_files))

        # Get existing chunk hashes for idempotent upsert
        existing_hashes = self._get_existing_hashes()

        # Process each file
        total_indexed = 0
        for file_path in md_files:
            chunks = split_runbook_file(
                file_path,
                chunk_size_tokens=self._chunk_size_tokens,
                chunk_overlap_tokens=self._chunk_overlap_tokens,
            )

            # Filter out already-indexed chunks
            new_chunks = [c for c in chunks if c.content_sha256 not in existing_hashes]

            if not new_chunks:
                logger.debug("Skipping %s — all chunks already indexed", file_path.name)
                continue

            # Embed and upsert
            texts = [c.text for c in new_chunks]
            vectors = self._embedder.embed(texts)

            # Generate point IDs based on content hash (deterministic across processes)
            ids = [
                int.from_bytes(
                    hashlib.sha256(c.content_sha256.encode()).digest()[:8], "big"
                )
                for c in new_chunks
            ]

            payloads = [c.model_dump() for c in new_chunks]

            self._store.upsert_points(
                collection_name=self._collection_name,
                ids=ids,
                vectors=vectors,
                payloads=payloads,
            )

            total_indexed += len(new_chunks)
            logger.info(
                "Indexed %d chunks from %s (%d skipped as duplicates)",
                len(new_chunks),
                file_path.name,
                len(chunks) - len(new_chunks),
            )

        logger.info(
            "Indexing complete: %d new chunks added to '%s'",
            total_indexed,
            self._collection_name,
        )
        return total_indexed

    def _get_existing_hashes(self) -> set[str]:
        """Retrieve content_sha256 values of already-indexed chunks."""
        try:
            points = self._store.scroll_all(self._collection_name, limit=10000)
            return {
                p.payload.get("content_sha256", "")
                for p in points
                if p.payload.get("content_sha256")
            }
        except Exception:
            return set()


class IncidentIndexer:
    """Index incident summaries into the incidents_v1 Qdrant collection.

    Owns ALL writes to the incidents_v1 collection per design section 2.6.
    S2's PastIncidentsProvider is read-only against this collection.
    """

    def __init__(
        self,
        store: QdrantStore,
        embedder: Embedder,
        collection_name: str = "incidents_v1",
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._collection_name = collection_name

    def ensure_collection(self) -> None:
        """Create the incidents_v1 collection if it doesn't exist."""
        self._store.ensure_collection(self._collection_name, dim=self._embedder.dim)
        logger.info(
            "Incidents collection '%s' ready with payload indexes",
            self._collection_name,
        )

    def index_incident(
        self,
        incident_id: str,
        title: str,
        service: str,
        severity: str,
        symptom: str,
        environment: str = "prod",
        resolved_summary: str = "",
        occurred_at: str = "",
    ) -> None:
        """Index a single incident into the collection.

        Args:
            incident_id: Unique incident ID.
            title: Incident title.
            service: Affected service.
            severity: Incident severity.
            symptom: Observable symptom description.
            environment: Environment (prod, staging, dev, any).
            resolved_summary: How the incident was resolved.
            occurred_at: ISO timestamp of when the incident occurred.
        """
        # Build embedding text from title + symptom
        embed_text = f"{title} {symptom} {service}"
        vector = self._embedder.embed([embed_text])[0]

        # Generate deterministic ID from incident_id (stable across processes)
        point_id = int.from_bytes(
            hashlib.sha256(incident_id.encode()).digest()[:8], "big"
        )

        payload = {
            "incident_id": incident_id,
            "title": title,
            "service": service,
            "severity": severity,
            "symptom": symptom,
            "environment": environment,
            "resolved_summary": resolved_summary,
            "occurred_at": occurred_at,
            "service_tags": [service],
        }

        self._store.upsert_points(
            collection_name=self._collection_name,
            ids=[point_id],
            vectors=vector.reshape(1, -1),
            payloads=[payload],
        )
        logger.debug("Indexed incident '%s' into '%s'", incident_id, self._collection_name)

    def reindex(self, incidents: list[dict]) -> int:
        """Reindex a batch of incidents.

        Args:
            incidents: List of incident dicts with keys matching index_incident params.

        Returns:
            Number of incidents indexed.
        """
        self.ensure_collection()

        for incident in incidents:
            self.index_incident(**incident)

        logger.info(
            "Reindexed %d incidents into '%s'",
            len(incidents),
            self._collection_name,
        )
        return len(incidents)
