"""Real-API integration tests for the triage graph (Week 5 PR 6).

Deliberately the *only* tests in the suite that call a real LLM — everything
else (unit + functional) runs mocked/stubbed and offline. Two runs total,
against the two fixtures already verified live during PR 6 scoping:

- `checkout-deploy` -> auto-approve path (confidence 0.90 as of 2026-07-22)
- `slack-vague`     -> human-gate path (confidence 0.40 as of 2026-07-22),
  then resumed with an approve decision (resume itself makes no further LLM
  call, so this stays within the two-call budget)

Requires TOGETHER_API_KEY (the configured provider in sentinel.yaml). Skipped
automatically when the key isn't set, so the rest of the suite stays offline
and free by default. Run explicitly with: pytest -m integration
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sentinel.config import load_settings
from sentinel.ingestion.loader import load_from_file
from sentinel.retrieval.bootstrap import build_retriever
from sentinel.tools.bootstrap import build_tool_provider
from sentinel.triage.engine import resume_triage_graph, run_triage_graph
from sentinel.triage.nodes import TriageDeps

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TOGETHER_API_KEY"),
        reason="TOGETHER_API_KEY not set — skipping real-API integration tests",
    ),
]

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "backend" / "fixtures"


@pytest.fixture(scope="module")
def deps() -> TriageDeps:
    # Module-scoped: the local embedded Qdrant store holds an exclusive file
    # lock on its storage path, so only one QdrantClient (hence one retriever)
    # may be open per process — build it once and share it across both tests.
    settings = load_settings()
    retriever = build_retriever(settings)
    tool_provider = build_tool_provider(
        settings,
        retrieval_client=retriever._store,  # noqa: SLF001 — reuse embedded Qdrant store
        embedder=retriever._embedder,  # noqa: SLF001
    )
    return TriageDeps(
        settings=settings, retriever=retriever, tool_provider=tool_provider
    )


class TestAutoApproveRealAPI:
    async def test_checkout_deploy_auto_approves(self, deps: TriageDeps) -> None:
        alert = load_from_file(FIXTURES_DIR / "checkout-deploy.json")

        _graph, result = await run_triage_graph(
            alert, deps, correlation_id="integration-auto"
        )

        assert result.interrupted is False
        assert result.final_summary is not None
        assert result.approval_status == "auto"
        assert result.final_summary.confidence >= 0.80
        assert result.final_summary.proposed_remediation is not None
        assert result.final_summary.requires_human_approval is False


class TestHumanGateRealAPI:
    async def test_slack_vague_pauses_then_resume_approves(
        self, deps: TriageDeps
    ) -> None:
        alert = load_from_file(FIXTURES_DIR / "slack-vague.json")

        graph, result = await run_triage_graph(
            alert, deps, correlation_id="integration-human"
        )

        assert result.interrupted is True
        assert result.final_summary is None
        assert result.summary is not None
        assert result.summary.confidence < 0.80

        resumed = await resume_triage_graph(
            graph,
            correlation_id="integration-human",
            human_decision="approve",
            human_note="verified in PR 6 integration test",
        )

        assert resumed.interrupted is False
        assert resumed.approval_status == "approved"
        assert resumed.final_summary is not None
        assert resumed.final_summary.requires_human_approval is True
