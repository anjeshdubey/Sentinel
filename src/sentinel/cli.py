"""Sentinel CLI — Typer application entry point."""

import logging
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from sentinel.config import load_settings
from sentinel.ingestion.loader import load_from_file, load_from_stdin
from sentinel.ingestion.service_extraction import extract_service_name
from sentinel.observability.trace import EventType, TraceCollector
from sentinel.output.console import print_incident, print_incident_json, print_incident_minimal
from sentinel.output.jsonl import append_incident
from sentinel.retrieval.bootstrap import build_retriever
from sentinel.tools.bootstrap import build_sync_facade, build_tool_provider
from sentinel.tools.formatting import format_context_block
from sentinel.tools.models import EnrichmentContext
from sentinel.triage.engine import triage_alert

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="sentinel",
    help="LLM-powered SRE triage agent — structured understanding from raw alerts.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def triage(
    alert: Annotated[
        Optional[Path],
        typer.Option("--alert", "-a", help="Path to alert JSON file. Omit to read from stdin."),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: rich, json, minimal"),
    ] = "rich",
    no_save: Annotated[
        bool,
        typer.Option("--no-save", help="Skip writing to incidents.jsonl"),
    ] = False,
    model: Annotated[
        Optional[str],
        typer.Option("--model", "-m", help="Override the default model"),
    ] = None,
    no_retrieval: Annotated[
        bool,
        typer.Option("--no-retrieval", help="Disable RAG runbook retrieval"),
    ] = False,
    no_tools: Annotated[
        bool,
        typer.Option("--no-tools", help="Disable tool enrichment (ownership, deploys, deps)"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable trace event logging"),
    ] = False,
) -> None:
    """Triage a raw alert and produce a structured IncidentSummary."""
    settings = load_settings()
    logging.basicConfig(level="DEBUG" if verbose else settings.log_level)

    collector = TraceCollector(verbose=verbose)

    # Attach Langfuse tracer if enabled (forwards trace events as spans)
    if settings.observability.langfuse_enabled:
        from sentinel.observability.tracer import get_tracer, set_trace_context

        tracer = get_tracer()
        set_trace_context(collector.trace_id)
        collector.attach_tracer(tracer)

    # Override model if specified
    if model:
        settings.model.default = model

    # Load alert
    if alert:
        if not alert.exists():
            console.print(f"[red]Error:[/red] File not found: {alert}")
            raise typer.Exit(code=1)
        raw_alert = load_from_file(alert)
    else:
        console.print("[dim]Reading alert from stdin (paste JSON, then Ctrl+D)...[/dim]")
        raw_alert = load_from_stdin()

    # Tool enrichment: extract service → call ownership/deploys/deps → format
    enrichment_block: str | None = None
    if not no_tools:
        service_name = extract_service_name(raw_alert, collector=collector)
        if service_name:
            try:
                tool_provider = build_sync_facade(build_tool_provider(settings))
            except Exception as e:
                logger.warning(f"Tool provider init failed: {e}")
                console.print(f"[yellow]Tool provider unavailable ({e}); continuing without.[/yellow]")
                tool_provider = None

            if tool_provider:
                unavailable_tools: list[str] = []
                owner = None
                deploys: tuple = ()
                deps = None
                resolved_service = service_name

                collector.emit(EventType.TOOL_CALL_STARTED, tool="ownership", service=service_name)
                try:
                    owner = tool_provider.get_service_owner(service_name)
                    if owner:
                        resolved_service = owner.service
                        collector.emit(EventType.TOOL_CALL_COMPLETED, tool="ownership", resolved_service=resolved_service)
                    else:
                        unavailable_tools.append("ownership")
                        collector.emit(EventType.TOOL_CALL_COMPLETED, tool="ownership", result="not_found")
                except Exception as e:
                    logger.warning(f"Ownership lookup failed: {e}")
                    unavailable_tools.append("ownership")
                    collector.emit(EventType.TOOL_CALL_COMPLETED, tool="ownership", error=str(e))

                collector.emit(EventType.TOOL_CALL_STARTED, tool="deploys", service=resolved_service)
                try:
                    deploys = tool_provider.get_recent_deploys(
                        resolved_service,
                        since=raw_alert.timestamp - timedelta(hours=2),
                        limit=5,
                    )
                    collector.emit(EventType.TOOL_CALL_COMPLETED, tool="deploys", count=len(deploys))
                except Exception as e:
                    logger.warning(f"Deploys lookup failed: {e}")
                    unavailable_tools.append("deploys")
                    collector.emit(EventType.TOOL_CALL_COMPLETED, tool="deploys", error=str(e))

                collector.emit(EventType.TOOL_CALL_STARTED, tool="dependencies", service=resolved_service)
                try:
                    deps = tool_provider.get_service_dependencies(resolved_service)
                    collector.emit(EventType.TOOL_CALL_COMPLETED, tool="dependencies", has_deps=deps is not None)
                except Exception as e:
                    logger.warning(f"Dependencies lookup failed: {e}")
                    unavailable_tools.append("dependencies")
                    collector.emit(EventType.TOOL_CALL_COMPLETED, tool="dependencies", error=str(e))

                enrichment = EnrichmentContext(
                    service_owner=owner,
                    recent_deploys=list(deploys),
                    service_dependencies=deps,
                    unavailable_tools=unavailable_tools,
                )
                enrichment_block = format_context_block(enrichment)
                logger.info(f"Tool enrichment: service={service_name} (resolved={resolved_service})")
                console.print(f"[dim]Tool enrichment: {resolved_service}[/dim]")
        else:
            logger.info("No service name extracted; skipping tool enrichment")

    # Initialize retriever unless disabled
    retriever = None
    if not no_retrieval:
        try:
            retriever = build_retriever(settings)
            logger.info("RAG retrieval enabled")
            console.print("[dim]RAG retrieval enabled[/dim]")
        except Exception as e:
            logger.warning(f"Failed to initialize retriever: {e}")
            logger.warning("Continuing without RAG retrieval")
            console.print(f"[yellow]Retrieval unavailable ({e}); continuing without RAG.[/yellow]")

    # Triage
    with console.status("[bold green]Triaging alert...[/bold green]"):
        incident = triage_alert(
            raw_alert, settings, retriever=retriever,
            enrichment_block=enrichment_block, collector=collector,
        )

    # Output
    match output_format:
        case "json":
            print_incident_json(incident)
        case "minimal":
            print_incident_minimal(incident)
        case _:
            print_incident(incident)

    # Trace summary
    if verbose:
        summary = collector.summary()
        console.print(f"\n[dim]Trace [{collector.trace_id[:8]}]: {summary['event_count']} events, {summary['total_ms']}ms total[/dim]")
        for event in collector.events:
            console.print(f"  [dim][{event.span_id[:8]}] {event.event_type.value}: {event.metadata}[/dim]")

    # Persist
    if not no_save:
        output_path = settings.output.jsonl_path
        append_incident(incident, output_path)
        console.print(f"\n[dim]Saved to {output_path}[/dim]")


@app.command()
def version() -> None:
    """Print Sentinel version."""
    from sentinel import __version__

    console.print(f"sentinel v{__version__}")


if __name__ == "__main__":
    app()
