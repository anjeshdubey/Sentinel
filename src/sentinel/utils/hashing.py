"""Deterministic hashing for alert deduplication and ID generation."""

import hashlib
import json
import uuid
from typing import Any


def hash_payload(payload: dict[str, Any]) -> str:
    """Produce a stable SHA-256 hex digest of a dict.

    Keys are sorted to ensure determinism regardless of insertion order.
    """
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def generate_incident_id(alert_hash: str) -> str:
    """Generate a deterministic UUID v5 from the alert hash.

    Uses a fixed namespace so the same alert always produces the same incident ID.
    This is NOT the dedup key (that comes in Week 7 with embedding clusters).
    """
    namespace = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    return str(uuid.uuid5(namespace, alert_hash))
