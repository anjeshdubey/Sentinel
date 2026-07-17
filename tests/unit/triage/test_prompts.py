"""Tests for sentinel.triage.prompts.build_user_prompt / _render_runbook_excerpts."""

from __future__ import annotations

from sentinel.retrieval.models import RetrievedChunk, RunbookChunk
from sentinel.triage.prompts import SYSTEM_PROMPT, _render_runbook_excerpts, build_user_prompt


def _chunk(text: str, index: int = 0, total: int = 1, runbook_id: str = "rb-1") -> RetrievedChunk:
    return RetrievedChunk(
        chunk=RunbookChunk(
            runbook_id=runbook_id,
            runbook_title=f"{runbook_id} title",
            chunk_index=index,
            chunk_total=total,
            text=text,
            content_sha256="a" * 64,
        ),
        score=0.9,
    )


class TestRenderRunbookExcerpts:
    def test_no_chunks_returns_no_match_sentinel(self) -> None:
        assert _render_runbook_excerpts(None) == "(No matching runbooks found for this alert.)"

    def test_empty_list_returns_no_match_sentinel(self) -> None:
        assert _render_runbook_excerpts([]) == "(No matching runbooks found for this alert.)"

    def test_single_chunk_rendered_as_excerpt(self) -> None:
        result = _render_runbook_excerpts([_chunk("UNIQUE_TEXT")])

        assert "UNIQUE_TEXT" in result
        assert 'id="1"' in result
        assert "<excerpt" in result and "</excerpt>" in result

    def test_multiple_chunks_separated_by_blank_line(self) -> None:
        result = _render_runbook_excerpts([_chunk("FIRST"), _chunk("SECOND")])

        assert "FIRST" in result
        assert "SECOND" in result
        # A blank line separates the first </excerpt> from the second <excerpt>.
        assert "</excerpt>\n\n<excerpt" in result


class TestBuildUserPrompt:
    def test_includes_source_timestamp_and_payloads(self) -> None:
        prompt = build_user_prompt(
            source="pagerduty",
            timestamp="2026-01-01T00:00:00Z",
            raw_payload='{"message": "down"}',
            metadata="{}",
        )

        assert "pagerduty" in prompt
        assert "2026-01-01T00:00:00Z" in prompt
        assert '{"message": "down"}' in prompt

    def test_no_runbook_context_uses_sentinel(self) -> None:
        prompt = build_user_prompt(
            source="pagerduty", timestamp="t", raw_payload="{}", metadata="{}"
        )

        assert "(No matching runbooks found for this alert.)" in prompt

    def test_runbook_context_included_when_provided(self) -> None:
        prompt = build_user_prompt(
            source="pagerduty",
            timestamp="t",
            raw_payload="{}",
            metadata="{}",
            runbook_context=[_chunk("RUNBOOK_MARKER")],
        )

        assert "RUNBOOK_MARKER" in prompt

    def test_no_enrichment_block_uses_sentinel(self) -> None:
        prompt = build_user_prompt(
            source="pagerduty", timestamp="t", raw_payload="{}", metadata="{}"
        )

        assert "(No enrichment data available for this alert.)" in prompt

    def test_enrichment_block_included_when_provided(self) -> None:
        prompt = build_user_prompt(
            source="pagerduty",
            timestamp="t",
            raw_payload="{}",
            metadata="{}",
            enrichment_block="ENRICHMENT_MARKER",
        )

        assert "ENRICHMENT_MARKER" in prompt

    def test_user_prompt_grounds_remediation_in_runbooks(self) -> None:
        prompt = build_user_prompt(
            source="pagerduty", timestamp="t", raw_payload="{}", metadata="{}"
        )

        assert "proposed_remediation" in prompt
        assert "runbook" in prompt.lower()


class TestSystemPromptRemediation:
    def test_system_prompt_instructs_grounded_remediation(self) -> None:
        assert "proposed_remediation" in SYSTEM_PROMPT
        # Must tell the model to fall back to null when runbooks don't support one.
        assert "null" in SYSTEM_PROMPT.lower()
