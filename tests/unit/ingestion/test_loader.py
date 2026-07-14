"""Tests for sentinel.ingestion.loader."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sentinel.ingestion.loader import load_from_dict, load_from_file, load_from_stdin
from sentinel.models.raw_alert import RawAlert


def test_load_from_file_valid_json(tmp_path: Path) -> None:
    payload = {
        "source": "pagerduty",
        "timestamp": "2026-05-25T03:14:00Z",
        "raw_payload": {"message": "checkout down"},
        "metadata": {"team": "checkout"},
    }
    path = tmp_path / "alert.json"
    path.write_text(json.dumps(payload))

    alert = load_from_file(path)

    assert isinstance(alert, RawAlert)
    assert alert.source == "pagerduty"
    assert alert.raw_payload == {"message": "checkout down"}


def test_load_from_file_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "alert.json"
    path.write_text("{not valid json")

    with pytest.raises(json.JSONDecodeError):
        load_from_file(path)


def test_load_from_file_schema_invalid_raises(tmp_path: Path) -> None:
    path = tmp_path / "alert.json"
    path.write_text(json.dumps({"timestamp": "2026-05-25T03:14:00Z", "raw_payload": {}}))

    with pytest.raises(ValidationError):
        load_from_file(path)


def test_load_from_dict_valid() -> None:
    alert = load_from_dict(
        {
            "source": "datadog",
            "timestamp": "2026-05-25T03:14:00Z",
            "raw_payload": {"tags": ["service:checkout"]},
        }
    )

    assert alert.source == "datadog"


def test_load_from_dict_schema_invalid_raises() -> None:
    with pytest.raises(ValidationError):
        load_from_dict({"source": "datadog"})


def test_load_from_stdin_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "source": "manual",
        "timestamp": "2026-05-25T03:14:00Z",
        "raw_payload": {"message": "manual report"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    alert = load_from_stdin()

    assert alert.source == "manual"
    assert alert.raw_payload == {"message": "manual report"}


def test_load_from_stdin_malformed_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))

    with pytest.raises(json.JSONDecodeError):
        load_from_stdin()
