# Testing Strategy

```bash
pip install -r requirements.txt  # includes pytest, pytest-asyncio
pytest -m "not integration and not slow"
```

## Tiers

| Tier | Location | What it covers | Status |
|---|---|---|---|
| Unit | `tests/unit/` | Isolated modules — config merge precedence, gateway provider resolution, models, ingestion, retrieval query building, tools, guardrails, the triage pipeline's individual stages. | Implemented (251 tests) |
| Functional | `tests/functional/` | Real internal code wired together, external services (LLM, Qdrant, embeddings) faked — full `triage_alert()` pipeline, real FastAPI endpoints via `httpx.ASGITransport`. | Implemented (26 tests) |
| Integration | `-m integration` / `-m slow` | Real LLM API calls, real Qdrant, real model downloads. Not run by default; each test should `pytest.skip` cleanly if its precondition (API key, `rag` extras) isn't met. | Markers defined in `pyproject.toml`; no test files yet |

Not yet covered by unit tests: `retrieval/chunking.py`, `observability/trace.py`.

## Source of truth

This page is intentionally a summary, not a copy. The full strategy —
per-module test breakdown, mocking approach, coverage targets, suggested build
order — lives in
[`TEST_PLAN.md`](https://github.com/anjeshdubey/sentinel/blob/main/TEST_PLAN.md)
at the repo root. Duplicating that ~23KB document into a second location here
is exactly the kind of drift this docs project exists to prevent — an earlier
pass through this repo found `README.md` and `TEST_PLAN.md` had already
silently disagreed about which tiers were implemented, precisely because two
places described the same thing. Update `TEST_PLAN.md`; this page only needs
touching when the tier-level status in the table above changes.
