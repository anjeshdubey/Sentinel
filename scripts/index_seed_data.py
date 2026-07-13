"""One-off script: index runbooks + past incidents into the embedded Qdrant store.

Run once (or whenever seed data changes) to populate ./data/qdrant:

    python scripts/index_seed_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from sentinel.config import load_settings  # noqa: E402
from sentinel.retrieval.embedder import BgeSmallEmbedder  # noqa: E402
from sentinel.retrieval.indexer import IncidentIndexer, RunbookIndexer  # noqa: E402
from sentinel.retrieval.store import QdrantStore  # noqa: E402

# Past-incident severities in the jsonl use SRE-style labels; map to the
# Severity enum used by PastIncident (critical/high/medium/low).
_SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "warning": "medium",
    "medium": "medium",
    "low": "low",
}


def main() -> None:
    settings = load_settings()

    store = QdrantStore(
        mode=settings.qdrant.mode,
        path=settings.qdrant.path,
        url=settings.qdrant.url,
        embedding_model=settings.retrieval.embedding_model,
        embedding_dim=settings.retrieval.embedding_dim,
    )
    embedder = BgeSmallEmbedder(model_name=settings.retrieval.embedding_model)

    # --- Runbooks -> runbooks_v1 ---
    runbook_dir = REPO_ROOT / "src" / "sentinel" / "data" / "runbooks"
    runbook_indexer = RunbookIndexer(
        store=store,
        embedder=embedder,
        collection_name=settings.qdrant.runbook_collection,
    )
    n_chunks = runbook_indexer.index(runbook_dir)
    print(f"Indexed {n_chunks} new runbook chunks into '{settings.qdrant.runbook_collection}'")

    # --- Past incidents -> incidents_v1 ---
    incidents_path = REPO_ROOT / "src" / "sentinel" / "data" / "past_incidents.jsonl"
    incidents: list[dict] = []
    with incidents_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            incidents.append(
                {
                    "incident_id": row["incident_id"],
                    "title": row["suspected_root_cause"][:120],
                    "service": row["service"],
                    "severity": _SEVERITY_MAP.get(row["severity"], "medium"),
                    "symptom": row["suspected_root_cause"],
                    "environment": "prod",
                    "resolved_summary": row["resolution"],
                    "occurred_at": row["timestamp"],
                }
            )

    incident_indexer = IncidentIndexer(
        store=store,
        embedder=embedder,
        collection_name=settings.qdrant.incident_collection,
    )
    n_incidents = incident_indexer.reindex(incidents)
    print(f"Indexed {n_incidents} past incidents into '{settings.qdrant.incident_collection}'")


if __name__ == "__main__":
    main()
