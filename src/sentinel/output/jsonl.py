"""JSONL file writer for incident persistence."""

from pathlib import Path

from sentinel.models.incident import IncidentSummary


def append_incident(incident: IncidentSummary, path: Path) -> None:
    """Append a single incident as one JSON line to the JSONL file.

    Creates parent directories and the file if they don't exist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(incident.model_dump_json() + "\n")
