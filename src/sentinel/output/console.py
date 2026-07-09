"""Rich console output for IncidentSummary."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from sentinel.models.incident import IncidentSummary

console = Console()

SEVERITY_COLORS = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "dim blue",
}


def print_incident(incident: IncidentSummary) -> None:
    """Print a formatted incident summary to the console."""
    severity_style = SEVERITY_COLORS.get(incident.severity.value, "white")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan", width=18)
    table.add_column("Value")

    table.add_row("Incident ID", incident.incident_id)
    table.add_row("Severity", f"[{severity_style}]{incident.severity.value.upper()}[/]")
    service_display = incident.service
    if not incident.service_recognized:
        service_display += " [dim](unrecognized)[/dim]"
    table.add_row("Service", service_display)
    table.add_row("Environment", incident.environment)
    table.add_row("Symptom", incident.symptom)
    table.add_row("Blast Radius", ", ".join(incident.blast_radius) if incident.blast_radius else "—")
    table.add_row("Urgency", incident.suggested_urgency.value)
    table.add_row("Confidence", f"{incident.confidence:.0%}")
    if incident.suspected_root_cause:
        table.add_row("Root Cause", incident.suspected_root_cause)
    table.add_row("Tags", ", ".join(incident.tags) if incident.tags else "—")
    table.add_row("Alert Time", incident.timestamp.isoformat())
    table.add_row("Triaged At", incident.triage_timestamp.isoformat())

    panel = Panel(
        table,
        title=f"[bold]{incident.title}[/bold]",
        border_style=severity_style,
        padding=(1, 2),
    )
    console.print(panel)


def print_incident_json(incident: IncidentSummary) -> None:
    """Print incident as formatted JSON."""
    console.print_json(incident.model_dump_json(indent=2))


def print_incident_minimal(incident: IncidentSummary) -> None:
    """Print a single-line summary."""
    console.print(
        f"[{SEVERITY_COLORS.get(incident.severity.value, 'white')}]"
        f"{incident.severity.value.upper()}[/] "
        f"| {incident.service} | {incident.title} "
        f"| confidence={incident.confidence:.0%}"
    )
