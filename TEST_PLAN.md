# Sentinel Test Plan

Status: **no tests exist yet** (`tests/` is not created). `pyproject.toml` already
declares the dev toolchain (`pytest`, `pytest-asyncio`, `pytest-cov`, `respx`) and
two markers (`integration`, `slow`), so the test-runner contract is decided —
this plan fills in what goes under `tests/`.

## 1. Strategy

Three tiers, most volume at the bottom:

| Tier | What it exercises | External deps | Speed | `-m` selection |
|---|---|---|---|---|
| **Unit** | Single function/class in isolation | None (all faked/mocked) | ms | default |
| **Functional** | Multiple internal components wired together (e.g. FastAPI endpoint → triage engine → tool provider) with *external* services (LLM API, Qdrant/embeddings) faked | None real | ms–low s | default |
| **Integration** | Real Anthropic/Gemini/Groq API calls, real embedded Qdrant + real sentence-transformers model | Yes (API keys, model download) | s–min | `-m integration`, `-m slow` |

Default CI/dev run: `pytest -m "not integration and not slow"` — fast, deterministic,
no API keys needed, no model downloads. Integration tier runs manually or on a
separate (nightly) job.

### Directory layout

```
tests/
  conftest.py                     # shared fixtures (RawAlert factory, Settings factory, frozen time)
  unit/
    models/
      test_incident.py
      test_raw_alert.py
      test_enums.py
    test_config.py
    test_gateway.py
    test_hashing.py
    ingestion/
      test_loader.py
      test_service_extraction.py
    retrieval/
      test_query_builder.py
      test_chunking.py
    tools/
      test_cache.py
      test_provider.py
      test_json_backend.py
    triage/
      test_extractor.py
      test_engine.py
    observability/
      test_trace.py
    backend/
      test_guardrails.py
  functional/
    test_scenarios_endpoint.py
    test_health_endpoint.py
    test_triage_stream_endpoint.py
    test_triage_engine_pipeline.py   # engine + fake retriever + fake LLM, no FastAPI
  integration/
    test_gateway_live.py             # real Anthropic call, skipped if no ANTHROPIC_API_KEY
    test_retrieval_live.py           # real Qdrant embedded + real BGE embedder
    test_triage_end_to_end.py        # full pipeline, real LLM + real retrieval
  fixtures/
    alerts/                          # small RawAlert JSON payloads for reuse
    cmdb/                            # tiny ownership/deploys/dependencies JSON for tools tests
```

## 2. Tooling & mocking strategy

- **LLM calls**: never hit a real provider in unit/functional tests. Two options,
  pick per-test based on what's being verified:
  - Monkeypatch `sentinel.triage.extractor.create_completion` (or
    `sentinel.gateway.create_completion`) to return a canned `LLMIncidentExtraction`
    — use this when testing `engine.triage_alert` orchestration, since we don't
    care about Instructor/HTTP internals there.
  - Use `respx` to intercept the actual Anthropic HTTP call — use this in
    `test_gateway.py` when verifying `create_completion` builds the right
    request shape (system prompt placement, provider-specific message format).
- **Qdrant/embeddings**: `retriever.py` / `store.py` / `embedder.py` need real
  `numpy`/`qdrant-client`/`sentence-transformers`. For unit tests, fake the
  `Embedder` protocol (return fixed vectors) and use an embedded `QdrantStore`
  pointed at a `tmp_path` — no network, no model download, still real Qdrant
  logic. Only the real BGE model download goes in `integration`/`slow`.
- **Time**: `IncidentSummary.triage_timestamp` and `RateLimiter`/`BudgetTracker`
  are time-sensitive. Use `freezegun` (add to `dev` deps) or monkeypatch
  `time.monotonic`/`datetime.now` directly rather than sleeping in tests.
- **Filesystem**: `JsonOwnershipProvider` etc. and `BudgetTracker` read/write
  files — use `tmp_path` fixtures, never touch `src/sentinel/data/` or
  `backend/.cache/` from tests.
- **FastAPI**: `httpx.AsyncClient` with `ASGITransport` (or `TestClient`) against
  `backend.demo_app.app`. Override the lazily-built singletons
  (`_get_settings`/`_get_retriever`/`_get_tool_provider` in `demo_endpoints.py`)
  via monkeypatch so functional tests never build a real retriever.
- **Async**: `asyncio_mode = "auto"` is already set — `async def test_...` just works.

## 3. Unit tests by module

### `models/` — pure validation logic, highest ROI, target ~100%
- `IncidentSummary`: title whitespace-only rejected; service normalization
  (spaces/underscores → hyphens, lowercased); confidence rounding to 2dp;
  confidence out-of-range (`<0`, `>1`) rejected; tags lowercased + blank
  tags dropped; `service_recognized` true for `KNOWN_SERVICES` members and for
  literal `"unknown"`, false otherwise (via `model_post_init`).
- `RawAlert`: valid payload round-trips; missing required field rejected;
  arbitrary `raw_payload`/`metadata` dict shapes accepted.
- `enums.py`: `StrEnum` membership/values are stable strings (contract for
  JSON serialization — a rename here is a breaking API change).

### `config.py`
- `load_settings()` with no `sentinel.yaml` present → defaults.
- `load_settings()` merges yaml under env vars (env wins) — test both a yaml-only
  value and an env-var override of the same key (`SENTINEL_MODEL__DEFAULT`).
- `logging.level` in yaml gets hoisted to `log_level`.
- Malformed/empty yaml file → defaults, no crash.

### `gateway.py`
- `GatewayConfig.from_env`: explicit `provider` arg wins over `SENTINEL_PROVIDER`
  env wins over default `"anthropic"`.
- Unknown provider → `GatewayConfigError`.
- Missing API key for selected provider → `GatewayConfigError` with the right
  env var name in the message.
- `resolve_model`: known alias resolved per provider; unknown name passed through
  unchanged; alias table is provider-scoped (an anthropic alias doesn't leak
  into gemini resolution).
- `ANTHROPIC_BASE_URL` only applied for the anthropic provider.
- `create_completion`: for each provider, assert the right Instructor factory
  is used and the message shape differs correctly (anthropic: `system=` kwarg;
  gemini/groq: system role in `messages`) — mock `instructor.from_anthropic` /
  `from_genai` / `from_groq` and assert call args, or use `respx` for the
  anthropic HTTP path specifically.

### `utils/hashing.py`
- `hash_payload`: same dict, different key insertion order → identical hash.
- `hash_payload`: differing values → different hash (no trivial collisions).
- `generate_incident_id`: same hash → same UUID every call (determinism);
  different hashes → different UUIDs; output is valid UUID5 syntax.

### `ingestion/loader.py`
- `load_from_file`: valid JSON → `RawAlert`; malformed JSON → `json.JSONDecodeError`;
  schema-invalid JSON (missing `source`) → pydantic `ValidationError`.
- `load_from_dict`: same validation contract without the file I/O.
- `load_from_stdin`: monkeypatch `sys.stdin` with `io.StringIO`.

### `ingestion/service_extraction.py`
- Priority order across all 4 sources — construct alerts that satisfy multiple
  sources at once and assert the higher-priority one wins (metadata >
  PagerDuty nested > metadata tags > payload tags).
- Invalid/malicious service names (path traversal chars, overlength, leading
  digit-only edge cases) rejected by `_validate_service_name` and the function
  falls through to the next source instead of returning garbage.
- No matching source anywhere → `None`.
- Case-insensitivity and tag pattern matching (`"service:XYZ"`, mixed case).

### `retrieval/query_builder.py`
- `_sanitize_query`: strips `< > " ' \`` and known injection prefixes
  (`"ignore previous instructions"`, `"system:"`, `"you are now"`, etc.),
  collapses whitespace — this is a security-relevant surface, test each
  injection-prefix pattern individually plus a combined adversarial string.
- `_guess_service`: metadata takes priority over payload; payload field
  priority order; regex fallback against `KNOWN_SERVICES` in flattened JSON
  text; returns `None` when nothing matches.
- `build_retrieval_query`: assembles title+symptom+service, truncates to
  600 chars total (test with an oversized symptom field to confirm the cap
  is enforced post-assembly, not per-field only).
- `_extract_symptom_heuristic`: field priority order (`message` before
  `description` before nested `event.message`, etc.), 300-char truncation,
  fallback to flattened JSON when no known field matches.

### `retrieval/chunking.py`
- `_parse_frontmatter`: valid YAML frontmatter parsed correctly; content with
  no frontmatter returns empty dict + full body unchanged; malformed
  frontmatter (missing closing `---`) falls back gracefully; list-valued
  frontmatter field (`[checkout, payments]`) parsed into a list. Cover both
  the `python-frontmatter` path and the manual-fallback path (monkeypatch the
  import to fail, or test with input that trips the library differently than
  manual parsing — at minimum test the manual parser directly if separable).
- `_extract_service_tags`: frontmatter `service_tags`/`services`/`service`
  keys all recognized; body-text regex fallback against `KNOWN_SERVICES`;
  union of both sources, sorted+deduped.
- `_split_into_paragraphs`: code blocks (fenced ```) kept as single units even
  when they contain blank lines; tables (pipe-delimited, ≥2 rows) kept intact;
  normal prose split on blank-line boundaries.
- `_estimate_tokens` / `_sha256`: deterministic, matches expected values for
  known input.
- End-to-end `split_runbook_file` (if that's the public entrypoint — verify
  name): chunk size stays near `chunk_size_tokens` with configured overlap;
  a chunk larger than `MAX_INDIVISIBLE_TOKENS` (e.g. one giant code block) is
  force-split rather than left oversized.

### `tools/cache.py` — concurrency-sensitive, needs careful async tests
- TTL hit within window returns cached value without calling `fn` again
  (assert call count via a `Mock`/counter).
- TTL expiry (freeze/advance clock) triggers a fresh call.
- `ttl=0` (single-flight only): value is never reused across sequential calls
  even though single-flight dedupes concurrent ones.
- Single-flight dedup: fire N concurrent `get_or_call` with the same key via
  `asyncio.gather`, assert the underlying `fn` was awaited exactly once and
  all N callers got the same result.
- Single-flight + exception: if `fn` raises, all concurrent waiters see the
  same exception (not just the first caller).
- LRU eviction: fill past `max_entries`, assert oldest key evicted first and
  `size` stays capped.
- `invalidate` / `clear` behave as documented.

### `tools/provider.py`
- Protocol version major mismatch at construction → `RuntimeError`; minor
  mismatch allowed.
- Per-tool timeout: a slow fake backend (`asyncio.sleep` beyond the
  configured `timeouts_ms`) raises `ToolTimeout` for that tool specifically.
- List results frozen to `tuple` on the way out (mutate the fake backend's
  returned list after the call, assert the caller's copy is unaffected).
- Cache delegation: with a `ToolCache` configured, repeated calls hit the
  cache (spy on the fake backend's call count); without one, every call hits
  the backend directly.
- Each of the six methods (`get_service_owner`, `get_recent_deploys`,
  `get_service_dependencies`, `get_similar_past_incidents`,
  `submit_resolution`, `get_escalation_advice`) routes to its own protocol
  member with the right cache key shape.
- `SyncToolProvider`: wraps async methods correctly via `asyncio.run` when
  called from sync code with no running loop.

### `tools/json_backend.py`
- `JsonOwnershipProvider`: exact match; case-insensitive alias resolution;
  unknown service → `None`; malformed/missing JSON file → `ToolBackendError`
  at construction (use `tmp_path` fixture data, not the real `data/cmdb/`).
- `JsonDeploysProvider`: `since` filter excludes older deploys; `limit`
  truncates; results are newest-first even if source JSON is unordered;
  unknown service → `[]`.
- `JsonDependenciesProvider`: known/unknown service lookups.

### `triage/extractor.py`
- `extract_incident` builds `GatewayConfig` via `provider` arg and forwards to
  `create_completion` with the right `response_model` — mock both
  `GatewayConfig.from_env` and `create_completion`, assert call args.
- `api_key` override replaces `config.auth_token` before the call.

### `triage/engine.py` — the core orchestration, mock the LLM boundary
- `triage_alert` with `retriever=None`: no retrieval call happens, prompt has
  no runbook context, and the returned `IncidentSummary` combines the mocked
  `LLMIncidentExtraction` fields with computed fields (`incident_id` derived
  from `hash_payload(alert.raw_payload)`, `raw_alert_hash` matches, `timestamp`
  passed through, `triage_timestamp` is "now").
- `triage_alert` with a fake `retriever`: `build_retrieval_query` and
  `_guess_service` are called with the alert's payload/metadata, and the
  chunks returned by `retriever.query(...)` reach `build_user_prompt` as
  `runbook_context`. Empty chunk list → `None` passed through, not `[]`.
- `enrichment_block` passthrough into the prompt when provided.
- `collector` (fake `TraceCollector`) receives `LLM_CALL_STARTED`,
  `LLM_CALL_COMPLETED`, `EXTRACTION_COMPLETED` events in order, with the
  expected metadata (model name, service, severity, truncated title,
  confidence) — and *no* events emitted when `collector=None` (shouldn't crash).
- Determinism: same `alert.raw_payload` across two calls (different mocked
  LLM output) still yields the same `incident_id`/`raw_alert_hash`.

### `observability/trace.py`
- `TraceCollector.emit` appends typed events with correct `event_type` and
  metadata; `verbose` flag behavior (whatever it currently gates — check the
  source, likely console printing) is exercised without asserting on stdout
  content beyond "doesn't crash" unless it's meaningfully testable.
- Confirm no Langfuse import/network call happens when
  `ObservabilityConfig.langfuse_enabled=False` (the documented zero-overhead
  path) — this is a regression guard for a documented invariant.

### `backend/guardrails.py`
- `RateLimiter.check`: under short-window limit → allowed; at the 6th request
  within 10 min → denied with `retry_after_seconds > 0` and the short-window
  reason string; long-window (20/24h) boundary similarly, independent of the
  short window; `exempt_ips` always allowed regardless of history. Drive time
  via monkeypatched `time.monotonic` rather than real sleeps.
- `is_valid_scenario_id`: allowlist membership, case sensitivity, unknown ID
  rejected.
- `BudgetTracker`: `record_call` cost math matches the documented per-1K
  pricing; cumulative cost persists across instances sharing a `tmp_path`
  file; month rollover resets `cost_usd` to 0; corrupted/missing budget file
  degrades to zero cost rather than crashing; `is_exhausted` at/above 100%;
  `FORCE_ENABLE=true` env var overrides exhaustion; `is_warning` at the 80%
  threshold.

## 4. Functional tests

These wire real internal code together but fake every external service
(LLM API, Qdrant, embeddings) — the goal is to catch integration bugs between
Sentinel's own modules without paying for API calls or model downloads.

### `test_triage_engine_pipeline.py`
Full `triage_alert()` call using a **fake `RunbookRetriever`** (returns fixed
chunks) and a **monkeypatched `extract_incident`** (returns a fixed
`LLMIncidentExtraction`), driven by real fixture `RawAlert` JSON files under
`tests/fixtures/alerts/`. Verifies the whole pipeline shape end-to-end without
network.

### FastAPI endpoints (`backend/demo_app.py` + `demo_endpoints.py`)
Use `httpx.ASGITransport` against `app` directly, monkeypatching
`demo_endpoints._get_settings/_get_retriever/_get_tool_provider` so nothing
real gets built.

- `GET /scenarios` → the 4 fixed scenario dicts, matching `FIXTURE_FILES` keys
  exactly (a regression guard — these two structures currently have to stay
  in sync by hand).
- `GET /health` → shape check; `budget_used_pct` reflects a monkeypatched
  `budget_tracker`.
- `POST /triage/stream`:
  - Invalid `scenario_id` → 400 with the allowlist in the error detail.
  - `budget_tracker.is_exhausted()` monkeypatched `True` → 503 with
    `cached_traces_available: true`.
  - Cache-hit path: pre-seed `backend/.cache/traces/<scenario>-<today>.json`
    in a monkeypatched `CACHE_DIR` (tmp_path), assert the SSE stream replays
    it verbatim and `_run_triage_and_collect_events` is never called.
  - Cache-miss path: monkeypatch `triage_alert` and the tool provider methods
    to fixed fakes, assert the SSE event sequence
    (`start → alert → tool_call/tool_result* → llm_call → rag_query? →
    rag_results? → diagnosis → done`) and that the response gets cached to disk.
  - Execution failure inside `_run_triage_and_collect_events` → 500 with
    `execution_failed` and no cache file written.
  - Body over `MAX_BODY_BYTES` (1KB) → 413 from the middleware, before the
    handler runs.
  - Rate limiting: 6 sequential requests from the same test client IP within
    the short window → 6th returns 429 with `Retry-After` header set.
- CORS: `ALLOWED_ORIGIN` env var reflected in response headers for a
  cross-origin preflight-style request (only worth one test — it's a one-line
  config passthrough).

## 5. Integration tests (`-m integration`, `-m slow`)

Not run by default; require real credentials/models. Each test should
`pytest.skip` cleanly if its precondition isn't met (missing API key, `rag`
extras not installed) rather than failing CI.

- **`test_gateway_live.py`**: one real completion per configured provider
  (skip providers without an API key in env) against a trivial
  `LLMIncidentExtraction`-shaped prompt — confirms Instructor + the live SDK
  version still agree on request/response shape. Cheap, cap `max_tokens` low.
- **`test_retrieval_live.py`** (`slow` — downloads BGE model): build a real
  `QdrantStore` (embedded, `tmp_path`) + real `BgeSmallEmbedder`, index the
  actual runbooks under `src/sentinel/data/runbooks/`, run a handful of
  representative queries, and assert `score_threshold` filtering / the
  soft-boost-then-fallback filter strategy behave as documented.
  Deliberately not run against `data/qdrant/` (the checked-in prod index).
- **`test_triage_end_to_end.py`** (`integration` + `slow`): the 4 real backend
  fixtures (`backend/fixtures/*.json`) through the actual pipeline with a real
  LLM and real retrieval, asserting only broad shape/sanity (valid severity
  enum, non-empty title, confidence in range) — not exact LLM output, which
  isn't a stable assertion target. This is a smoke test, run manually or on a
  scheduled job, not per-PR.
- Optionally, a light `modal_app.py` import test gated on the `modal` package
  being installed, just to catch a broken decorator/import at deploy time.

## 6. Coverage targets

| Area | Target | Rationale |
|---|---|---|
| `models/`, `utils/hashing.py`, `retrieval/query_builder.py`, `tools/cache.py`, `backend/guardrails.py` | ~95–100% | Pure logic, security-relevant (injection sanitization, rate limiting), cheap to fully cover |
| `config.py`, `gateway.py`, `ingestion/`, `tools/provider.py`, `tools/json_backend.py`, `triage/` | ~85–90% | Mockable boundaries, a few provider/error branches acceptable to leave uncovered if genuinely hard to trigger |
| `retrieval/chunking.py` | ~85% | Pure logic but many branches (frontmatter fallback, code/table detection) |
| `retrieval/{store,embedder,retriever,indexer}.py`, `observability/trace.py` (Langfuse path) | Best-effort, mostly via functional fakes + a thin integration smoke layer | Real coverage requires real Qdrant/model; don't chase % here at the cost of slow suites |
| `backend/demo_app.py`, `backend/demo_endpoints.py` | ~80%+ via functional tests | External services faked, internal wiring fully exercised |
| `output/console.py` | Low priority | Rich-formatted console output, not on any critical path (unused by the FastAPI backend) |

Run `pytest -m "not integration and not slow" --cov=src/sentinel --cov=backend --cov-report=term-missing` as the standard local/CI command; `--cov-fail-under` threshold to be set once Phase 1–3 below land (recommend starting at 75% and ratcheting up).

## 7. Suggested build order (phasing)

1. **Phase 1 — pure logic**: `models/`, `utils/hashing.py`, `config.py`,
   `retrieval/query_builder.py`, `ingestion/`. No mocking infra needed beyond
   `tmp_path`/`monkeypatch`. Fastest path to a non-zero coverage number.
2. **Phase 2 — tools layer**: `tools/cache.py` (concurrency tests),
   `tools/provider.py`, `tools/json_backend.py`, `backend/guardrails.py`.
   Establishes the async-test patterns reused everywhere else.
3. **Phase 3 — triage core**: `gateway.py`, `triage/extractor.py`,
   `triage/engine.py` with the LLM boundary mocked. Establishes the
   LLM-mocking pattern.
4. **Phase 4 — functional**: FastAPI endpoint tests + the full-pipeline
   functional test, reusing fakes from Phases 2–3.
5. **Phase 5 — chunking + retrieval unit tests** with a faked `Embedder`.
6. **Phase 6 — integration tier**, marked and excluded from default runs; add
   last since it needs real credentials and is the least stable to maintain.

No CI workflow exists yet (`README.md` confirms: "No CI/CD is configured"). Once
Phase 1–4 land, a `.github/workflows/tests.yml` running
`pytest -m "not integration and not slow"` on PRs is a natural follow-up — not
included in this plan since it's an infra decision, not a test-design one.
