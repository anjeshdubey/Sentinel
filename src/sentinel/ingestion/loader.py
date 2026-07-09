"""Load raw alerts from various input sources."""

import json
import sys
from pathlib import Path
from typing import Any

from sentinel.models.raw_alert import RawAlert


def load_from_file(path: Path) -> RawAlert:
    """Load a RawAlert from a JSON file.

    Expected JSON structure matches the RawAlert schema:
    {
        "source": "pagerduty",
        "timestamp": "2026-05-25T03:14:00Z",
        "raw_payload": { ... },
        "metadata": { ... }
    }
    """
    content = path.read_text(encoding="utf-8")
    data = json.loads(content)
    return RawAlert.model_validate(data)


def load_from_dict(data: dict[str, Any]) -> RawAlert:
    """Load a RawAlert from a Python dict (for programmatic use)."""
    return RawAlert.model_validate(data)


def load_from_stdin() -> RawAlert:
    """Load a RawAlert from stdin (piped JSON)."""
    content = sys.stdin.read()
    data = json.loads(content)
    return RawAlert.model_validate(data)
