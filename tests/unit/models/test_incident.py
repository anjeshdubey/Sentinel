"""Tests for sentinel.models.incident.IncidentSummary."""

import pytest
from pydantic import ValidationError

from sentinel.models.incident import KNOWN_SERVICES, IncidentSummary


def test_valid_kwargs_round_trip(valid_incident_kwargs: dict) -> None:
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.title == "Checkout returning 500s"
    assert incident.severity == "critical"
    assert incident.confidence == 0.9


@pytest.mark.parametrize("title", ["", "   ", "\t\n"])
def test_whitespace_only_title_rejected(valid_incident_kwargs: dict, title: str) -> None:
    valid_incident_kwargs["title"] = title

    with pytest.raises(ValidationError, match="Title cannot be empty"):
        IncidentSummary.model_validate(valid_incident_kwargs)


def test_title_is_stripped(valid_incident_kwargs: dict) -> None:
    valid_incident_kwargs["title"] = "  Checkout down  "
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.title == "Checkout down"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Checkout API", "checkout-api"),
        ("checkout_api", "checkout-api"),
        ("  Checkout  ", "checkout"),
        ("ALREADY-HYPHENATED", "already-hyphenated"),
    ],
)
def test_service_name_normalization(
    valid_incident_kwargs: dict, raw: str, expected: str
) -> None:
    valid_incident_kwargs["service"] = raw
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.service == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.123456, 0.12),
        (0.999, 1.0),
        (0.005, 0.01),
        (1.0, 1.0),
        (0.0, 0.0),
    ],
)
def test_confidence_rounded_to_two_decimals(
    valid_incident_kwargs: dict, raw: float, expected: float
) -> None:
    valid_incident_kwargs["confidence"] = raw
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.confidence == expected


@pytest.mark.parametrize("bad_confidence", [-0.01, 1.01, -1.0, 2.0])
def test_confidence_out_of_range_rejected(
    valid_incident_kwargs: dict, bad_confidence: float
) -> None:
    valid_incident_kwargs["confidence"] = bad_confidence

    with pytest.raises(ValidationError):
        IncidentSummary.model_validate(valid_incident_kwargs)


def test_tags_are_lowercased_and_stripped(valid_incident_kwargs: dict) -> None:
    valid_incident_kwargs["tags"] = ["  Database ", "OOM", "Latency"]
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.tags == ["database", "oom", "latency"]


def test_blank_tags_dropped(valid_incident_kwargs: dict) -> None:
    valid_incident_kwargs["tags"] = ["database", "  ", "", "oom"]
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.tags == ["database", "oom"]


def test_tags_default_to_empty_list(valid_incident_kwargs: dict) -> None:
    valid_incident_kwargs.pop("tags")
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.tags == []


def test_blast_radius_defaults_to_empty_list(valid_incident_kwargs: dict) -> None:
    valid_incident_kwargs.pop("blast_radius")
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.blast_radius == []


def test_suspected_root_cause_defaults_to_none(valid_incident_kwargs: dict) -> None:
    valid_incident_kwargs.pop("suspected_root_cause")
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.suspected_root_cause is None


@pytest.mark.parametrize("service", sorted(KNOWN_SERVICES))
def test_service_recognized_true_for_known_services(
    valid_incident_kwargs: dict, service: str
) -> None:
    valid_incident_kwargs["service"] = service
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.service_recognized is True


def test_service_recognized_true_for_literal_unknown(valid_incident_kwargs: dict) -> None:
    valid_incident_kwargs["service"] = "unknown"
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.service_recognized is True


def test_service_recognized_false_for_unrecognized_service(
    valid_incident_kwargs: dict,
) -> None:
    valid_incident_kwargs["service"] = "some-brand-new-service"
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.service_recognized is False


def test_service_recognized_input_value_is_overridden_by_post_init(
    valid_incident_kwargs: dict,
) -> None:
    """service_recognized is computed, not trusted from the input."""
    valid_incident_kwargs["service"] = "some-brand-new-service"
    valid_incident_kwargs["service_recognized"] = True
    incident = IncidentSummary.model_validate(valid_incident_kwargs)

    assert incident.service_recognized is False


def test_title_over_max_length_rejected(valid_incident_kwargs: dict) -> None:
    valid_incident_kwargs["title"] = "x" * 121

    with pytest.raises(ValidationError):
        IncidentSummary.model_validate(valid_incident_kwargs)


class TestWorkflowFields:
    """Week 5 human-in-the-loop fields — all defaulted for backward compat."""

    def test_defaults_when_absent(self, valid_incident_kwargs: dict) -> None:
        incident = IncidentSummary.model_validate(valid_incident_kwargs)

        assert incident.proposed_remediation is None
        assert incident.requires_human_approval is True
        assert incident.approval_status == "pending"

    def test_proposed_remediation_round_trips(self, valid_incident_kwargs: dict) -> None:
        valid_incident_kwargs["proposed_remediation"] = "Roll back deploy #4821 per runbook rb-1."
        incident = IncidentSummary.model_validate(valid_incident_kwargs)

        assert incident.proposed_remediation == "Roll back deploy #4821 per runbook rb-1."

    def test_approval_fields_round_trip(self, valid_incident_kwargs: dict) -> None:
        valid_incident_kwargs["requires_human_approval"] = False
        valid_incident_kwargs["approval_status"] = "auto"
        incident = IncidentSummary.model_validate(valid_incident_kwargs)

        assert incident.requires_human_approval is False
        assert incident.approval_status == "auto"
