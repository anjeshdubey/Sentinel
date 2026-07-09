"""Raw alert input model."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sentinel.models.enums import AlertSource


class RawAlert(BaseModel):
    """Validated representation of an incoming alert payload.

    This is the input contract — any monitoring source must be
    normalized into this shape before triage.
    """

    source: AlertSource = Field(description="Origin monitoring system")
    timestamp: datetime = Field(description="When the alert fired (from source)")
    raw_payload: dict[str, Any] = Field(description="Original alert body, unmodified")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context (e.g., routing info, ingestion tags)",
    )
