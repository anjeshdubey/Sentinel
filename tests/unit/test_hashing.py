"""Tests for sentinel.utils.hashing."""

import uuid

from sentinel.utils.hashing import generate_incident_id, hash_payload


def test_hash_payload_stable_regardless_of_key_order() -> None:
    a = hash_payload({"service": "checkout", "severity": "critical"})
    b = hash_payload({"severity": "critical", "service": "checkout"})

    assert a == b


def test_hash_payload_differs_for_different_values() -> None:
    a = hash_payload({"service": "checkout"})
    b = hash_payload({"service": "payments"})

    assert a != b


def test_hash_payload_is_sha256_hex() -> None:
    digest = hash_payload({"a": 1})

    assert len(digest) == 64
    int(digest, 16)  # raises ValueError if not valid hex


def test_hash_payload_handles_non_json_native_values_via_default_str() -> None:
    # A plain hashing call shouldn't blow up on non-JSON-native values
    # (e.g. datetimes that slipped into a raw_payload dict).
    from datetime import UTC, datetime

    digest = hash_payload({"timestamp": datetime(2026, 1, 1, tzinfo=UTC)})

    assert len(digest) == 64


def test_generate_incident_id_is_deterministic() -> None:
    alert_hash = hash_payload({"service": "checkout"})

    id_a = generate_incident_id(alert_hash)
    id_b = generate_incident_id(alert_hash)

    assert id_a == id_b


def test_generate_incident_id_differs_for_different_hashes() -> None:
    id_a = generate_incident_id(hash_payload({"service": "checkout"}))
    id_b = generate_incident_id(hash_payload({"service": "payments"}))

    assert id_a != id_b


def test_generate_incident_id_is_valid_uuid5() -> None:
    alert_hash = hash_payload({"service": "checkout"})
    incident_id = generate_incident_id(alert_hash)

    parsed = uuid.UUID(incident_id)
    assert parsed.version == 5
