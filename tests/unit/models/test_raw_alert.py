"""Tests for sentinel.models.raw_alert.RawAlert."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sentinel.models.raw_alert import RawAlert


def test_valid_payload_round_trips(valid_raw_alert_kwargs: dict) -> None:
    alert = RawAlert.model_validate(valid_raw_alert_kwargs)

    assert alert.source == "pagerduty"
    assert alert.timestamp == datetime(2026, 1, 1, tzinfo=UTC)
    assert alert.raw_payload == {"message": "checkout returning 500s"}
    assert alert.metadata == {}


def test_metadata_defaults_to_empty_dict(valid_raw_alert_kwargs: dict) -> None:
    valid_raw_alert_kwargs.pop("metadata")
    alert = RawAlert.model_validate(valid_raw_alert_kwargs)

    assert alert.metadata == {}


@pytest.mark.parametrize("missing_field", ["source", "timestamp", "raw_payload"])
def test_missing_required_field_rejected(
    valid_raw_alert_kwargs: dict, missing_field: str
) -> None:
    valid_raw_alert_kwargs.pop(missing_field)

    with pytest.raises(ValidationError):
        RawAlert.model_validate(valid_raw_alert_kwargs)


def test_unknown_alert_source_rejected(valid_raw_alert_kwargs: dict) -> None:
    valid_raw_alert_kwargs["source"] = "not-a-real-source"

    with pytest.raises(ValidationError):
        RawAlert.model_validate(valid_raw_alert_kwargs)


def test_arbitrary_raw_payload_shapes_accepted(valid_raw_alert_kwargs: dict) -> None:
    valid_raw_alert_kwargs["raw_payload"] = {
        "nested": {"deeply": {"list": [1, 2, {"a": "b"}]}},
        "tags": ["service:checkout", "env:prod"],
    }
    alert = RawAlert.model_validate(valid_raw_alert_kwargs)

    assert alert.raw_payload["nested"]["deeply"]["list"] == [1, 2, {"a": "b"}]


def test_arbitrary_metadata_shapes_accepted(valid_raw_alert_kwargs: dict) -> None:
    valid_raw_alert_kwargs["metadata"] = {"routing": {"team": "checkout"}, "flags": [1, 2]}
    alert = RawAlert.model_validate(valid_raw_alert_kwargs)

    assert alert.metadata["routing"] == {"team": "checkout"}
