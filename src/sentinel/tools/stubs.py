"""Month-5 stub backends — raise ToolNotImplemented per protocols.py contract.

These exist so build_tool_provider() can wire a complete ToolProvider today
(all six protocol slots filled) even though resolution recording and
escalation advice are not implemented until Month 5.
"""

from __future__ import annotations

from sentinel.models.incident import IncidentSummary
from sentinel.tools.errors import ToolNotImplemented
from sentinel.tools.models import EscalationAdvice, ResolutionRecord


class StubResolutionRecorder:
    """Stub ResolutionRecorder — always raises ToolNotImplemented."""

    async def submit_resolution(self, record: ResolutionRecord) -> None:
        raise ToolNotImplemented(
            "submit_resolution", "Resolution recording available in Month 5"
        )


class StubEscalationAdvisor:
    """Stub EscalationAdvisor — always raises ToolNotImplemented."""

    async def get_escalation_advice(self, incident: IncidentSummary) -> EscalationAdvice:
        raise ToolNotImplemented(
            "get_escalation_advice", "Escalation advice available in Month 5"
        )
