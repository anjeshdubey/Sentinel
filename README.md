# Sentinel — AI SRE Triage Agent (Demo)

An LLM-powered agent that reads incident alerts, retrieves relevant runbooks (RAG),
calls tools to gather context (ownership, deploys, dependencies, past incidents),
and produces a structured diagnosis — streamed to a browser as a live trace.

Built directly on provider SDKs + [Instructor](https://github.com/instructor-ai/instructor)
(no agent framework), with pluggable LLM providers (Anthropic, Gemini, Groq).

**Live Demo:** <https://anjeshdubey.github.io/sentinel/>

## Architecture

```
frontend/ (+ docs/ mirror) ──HTTP/SSE──▶ backend/ (FastAPI) ──▶ src/sentinel/ (core engine)
                                                                  ├─ triage/    (extraction pipeline)
                                                                  ├─ retrieval/ (RAG over runbooks, Qdrant)
                                                                  ├─ tools/     (CMDB enrichment)
                                                                  └─ gateway.py (multi-provider LLM client)
```

- **Frontend**: static HTML/JS demo UI, no build step.
- **Backend**: plain FastAPI app, deployable locally (`uvicorn`) or serverless (Modal).
- **Core engine** (`src/sentinel`): installable package with the triage pipeline, RAG retrieval, and tool-calling layer, independent of the FastAPI/Modal wrapper around it.

## Repository layout

### `src/sentinel/` — core engine (installable package)

| Path | What it does |
|---|---|
| `config.py` | `Settings` (pydantic-settings): model/output/retrieval/tools/observability config. `load_settings()` reads `sentinel.yaml` and merges it with env vars — for a field set in both, **yaml wins** (pydantic-settings gives constructor kwargs priority over env sources). |
| `gateway.py` | Multi-provider LLM client factory. `GatewayConfig.from_env(provider=...)` resolves provider + API key + model alias; `create_completion()` runs a structured (Instructor) completion against Anthropic, Gemini, or Groq behind one interface. |
| `triage/engine.py` | `triage_alert()` — the pipeline entry point: hash alert → deterministic ID, retrieve runbook context, build prompt, call the LLM extractor, emit trace events, assemble the final `IncidentSummary`. |
| `triage/extractor.py` | `extract_incident()` — calls `gateway.create_completion` against the `LLMIncidentExtraction` schema (title, severity, service, blast radius, confidence, etc.), which Instructor validates/fills. |
| `triage/prompts.py` | System prompt (triage rubric + prompt-injection defenses) and user-prompt template (marks retrieved context as untrusted). |
| `retrieval/` | Qdrant-backed RAG: `chunking.py` (frontmatter-aware runbook splitting), `embedder.py` (BGE-small, 384-dim), `store.py` (hybrid filtered search), `retriever.py` (`RunbookRetriever`, async + sync), `indexer.py` (idempotent runbook/incident indexing), `query_builder.py` (builds + sanitizes retrieval queries), `bootstrap.py` (composition root). |
| `tools/` | Tool-provider abstraction for CMDB enrichment: `protocols.py` (versioned interfaces for ownership/deploys/dependencies/past-incidents), `provider.py` (`ToolProvider`, timeouts + caching), `json_backend.py` (reads `data/cmdb/*.json`), `vector_backend.py` (past-incident similarity via Qdrant), `cache.py` (TTL/LRU/single-flight), `bootstrap.py` (only implements the `json` backend today — `real` backend raises, reserved for a future live-API integration). |
| `ingestion/` | `loader.py` loads alerts (`load_from_file`/`_dict`/`_stdin`) into `RawAlert`. `service_extraction.py` has a separate service-name heuristic that is **not currently wired into the triage pipeline** (the pipeline uses `retrieval/query_builder._guess_service()` instead) — likely leftover from an earlier design. |
| `models/` | Pydantic domain models: `RawAlert`, `IncidentSummary` (with `KNOWN_SERVICES` and field validators), `enums.py` (`AlertSource`, `Severity`, `Urgency`). |
| `observability/trace.py` | `TraceCollector` — emits typed `TraceEvent`s (SSE-serializable) for each pipeline stage. Has a Langfuse-forwarding hook (`attach_tracer`), but Langfuse itself isn't wired up anywhere yet — `ObservabilityConfig.langfuse_enabled` defaults to `False` and nothing instantiates a tracer. |
| `output/console.py` | Rich-formatted console output (`print_incident*`) — used for local/REPL testing, not by the FastAPI backend (which streams SSE instead). |
| `data/` | Seed data: `cmdb/*.json` (fake ownership/deploys/dependencies), `runbooks/*.md` (8 runbooks, indexed into Qdrant), `past_incidents.jsonl` (seed incidents for similarity search). |
| `utils/hashing.py` | Deterministic incident-ID hashing. |

### `backend/` — FastAPI demo API

| Path | What it does |
|---|---|
| `demo_app.py` | Plain FastAPI app (`app`), CORS via `ALLOWED_ORIGIN` env var, and `guardrails_middleware` (1KB request body cap, rate limiting on `/triage*`). Runnable directly with `uvicorn backend.demo_app:app --reload`. |
| `demo_endpoints.py` | The actual API routes (see below). Builds lazily-cached singletons for settings/retriever/tool provider. For the 4 demo scenarios, it fetches enrichment (ownership, deploys) itself and calls `triage_alert()`, translating `TraceEvent`s into SSE frames, with responses cached to `backend/.cache/traces/`. |
| `modal_app.py` | Wraps `demo_app.app` in `@modal.asgi_app()` for serverless deployment; declares Modal secrets for the three provider API keys + `sentinel-provider`. Same router/endpoints as local — no separate API surface. |
| `guardrails.py` | `RateLimiter` (in-memory, per-process sliding window — noted as needing Redis for multi-instance), `BudgetTracker` (file-based monthly cost cap — defined but **`record_call()` is never invoked**, so cost tracking and the `/health` budget figure are currently inert), `ALLOWED_SCENARIO_IDS` allowlist. |
| `fixtures/*.json` | The 4 sample alert payloads backing the demo scenarios. |

**Endpoints** (same set served locally and on Modal):
- `GET /scenarios` — the 4 demo scenario definitions.
- `GET /health` — status/version (budget and cache-hit-rate figures are placeholders, not live-tracked).
- `POST /triage/stream` — `{scenario_id}` → SSE stream of the triage run (from cache if available, else live).

### `frontend/` + `docs/` — static demo UI

Plain HTML/JS, no build step. `docs/` is a **mirror of `frontend/`** used as the GitHub Pages source (repo Pages config points at `/docs` on `main`) — the two must be kept in sync manually; `frontend/` is the working copy.

`app.js` auto-detects environment: if the page is served from `localhost`/`127.0.0.1`, it calls a local backend at `http://localhost:8000`; otherwise it calls the deployed Modal backend.

### Root files

| Path | What it does |
|---|---|
| `sentinel.yaml` | Active LLM provider/model config (see below), output/logging settings. Alternate provider blocks are kept commented out for quick swapping. |
| `pyproject.toml` / `requirements.txt` | Package + dependency definitions. |
| `scripts/index_seed_data.py` | One-off script that indexes `src/sentinel/data/runbooks/` and `past_incidents.jsonl` into the local Qdrant store (`data/qdrant/`). Already run — that directory is checked in with populated collections. |
| `STATUS.md` | A stale snapshot from an earlier repo-trimming pass (references an old repo name/remote) — historical only, not current status. |
| `TEST_PLAN.md` | The full intended test strategy (unit/functional/integration tiers). Unit and functional tiers are implemented; integration is not — see Testing below. |

No CI/CD is configured (no `.github/workflows`) — deployment to Modal and GitHub Pages is manual.

## Testing

```bash
pip install -r requirements.txt  # includes pytest, pytest-asyncio
pytest tests/
```

277 tests currently pass: 251 unit + 26 functional (`pytest -m "not integration and not slow"`
runs both tiers; nothing is skipped by default since the integration tier below doesn't
exist as files yet, only as markers).

**Unit** (`tests/unit/`) covers `config.py` (yaml/env merge precedence — for a field set
in both sentinel.yaml and the environment, **yaml wins**), `gateway.py` (provider/model/
API-key resolution incl. the provider-arg-over-env-var fix above, and `create_completion()`'s
per-provider Instructor call shape), `utils/hashing.py`, `models/` (`RawAlert`,
`IncidentSummary`, enums), `ingestion/` (loader + service extraction),
`retrieval/query_builder.py` (sanitization, service guessing), `tools/` (cache
TTL/LRU/single-flight, JSON backends, `ToolProvider` routing/timeouts/caching),
`backend/guardrails.py` (rate limiting, incl. sliding-window boundary cases), and
`triage/engine.py`, `triage/extractor.py`, `triage/prompts.py`.

**Functional** (`tests/functional/`) wires real internal code together with every external
service (LLM API, Qdrant, embeddings) faked: `test_triage_engine_pipeline.py` runs the full
`triage_alert()` pipeline against fixture alerts; `test_health_endpoint.py`,
`test_scenarios_endpoint.py`, `test_triage_stream_endpoint.py` exercise the real FastAPI
app via `httpx.ASGITransport`.

Not yet covered: `retrieval/chunking.py`, `observability/trace.py`, and the
integration tier (`-m integration`/`-m slow` — real LLM/Qdrant calls; markers are
defined in `pyproject.toml` but no test files exist yet). See `TEST_PLAN.md` for the
full strategy and coverage targets.

## Quick Start (Backend Development)

```bash
# Install dependencies
pip install -r requirements.txt

# Run the backend locally
uvicorn backend.demo_app:app --reload --port 8000

# Or test the triage function directly
python
>>> from sentinel.triage.engine import triage_alert
>>> from sentinel.ingestion.loader import load_from_file
>>> from sentinel.config import load_settings
>>> from sentinel.observability import TraceCollector
>>>
>>> settings = load_settings()
>>> alert = load_from_file('backend/fixtures/checkout-deploy.json')
>>> collector = TraceCollector(verbose=True)
>>> incident = triage_alert(alert, settings, collector=collector)
```

For frontend testing, serve `frontend/` locally (e.g. `python -m http.server` from that
directory) and open it via `localhost` — it will auto-target your local backend.

**Note:** CLI and MCP server have been removed to minimize dependencies for demo deployment.

### Switching LLM provider/model

There's exactly one file to edit for this, whether you're running locally or on Modal:
**`sentinel.yaml`** — set `model.provider` (`anthropic` | `gemini` | `groq`) and
`model.default` (short alias, e.g. `claude-sonnet` / `gemini-flash` / `groq-llama`,
or a full provider model ID). Other provider blocks are kept commented out in the
file for quick swapping.

`sentinel.yaml`'s `model.provider` is passed explicitly through
`triage_alert()` → `extract_incident()` → `GatewayConfig.from_env(provider=...)`
(`src/sentinel/gateway.py`), so it takes priority over any `SENTINEL_PROVIDER`
env var. That env var (and the Modal `sentinel-provider` secret that sets it,
see below) is now only a fallback used if `sentinel.yaml` doesn't specify a
provider at all — editing the yaml is the supported way to switch.

**Local:**
1. Edit `model.provider`/`model.default` in `sentinel.yaml` and save.
2. Make sure the matching API key is in the backend process's actual shell
   environment (see Secrets management below — **`backend/.env` is not
   auto-loaded**, you must export it yourself). The running dev server
   (`uvicorn --reload`) re-reads `sentinel.yaml` per request via
   `load_settings()`, so the yaml edit needs no restart; if you just added a
   new env var to your shell, restart `uvicorn` so it inherits it.

**Modal (deployed):**
1. Edit `model.provider`/`model.default` in `sentinel.yaml` and commit.
2. Make sure the matching provider's Modal secret exists (see Secrets
   management below — all three are typically created once, up front).
3. Redeploy: `modal deploy backend/modal_app.py` (from repo root). The new
   `sentinel.yaml` is baked into the image via `add_local_dir`, so a redeploy
   is required — there's no live-reload on Modal.

### Secrets management

Two completely separate stores, one per environment — there is no shared
secrets file between them.

**Local**: `backend/.env` (git-ignored, never committed) holds the keys as a
reference file:
```
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
GROQ_API_KEY=...
```
**This file is not automatically loaded** — nothing in the codebase calls
`load_dotenv()` or configures pydantic-settings' `env_file`, so simply having
`backend/.env` populated does *not* put these into `os.environ`. You need to
export them into the shell that runs `uvicorn` yourself, e.g.
`export $(grep -v '^#' backend/.env | xargs)` before starting the server, or
source them via your shell profile/direnv. (Worth fixing properly — adding a
`load_dotenv()` call to `demo_app.py` — since `python-dotenv` is already an
installed transitive dependency of `pydantic-settings`; not yet done.)

Only the key matching the active `sentinel.yaml` provider is strictly required
once it *is* in the environment; `GatewayConfig.from_env()` only reads the one
key for whichever provider is selected (`src/sentinel/gateway.py`'s
`API_KEY_ENV_VARS` map), so the other two keys being absent doesn't break
anything.

**Modal** (deployed backend): Modal has its own encrypted secret store, entirely
separate from `.env` and the git repo — nothing provider-related is baked into
the Docker image or checked into source. Created once via the Modal CLI:
```bash
modal secret create anthropic-api-key ANTHROPIC_API_KEY=sk-ant-...
modal secret create gemini-api-key GEMINI_API_KEY=...
modal secret create groq-api-key GROQ_API_KEY=...
modal secret create sentinel-provider SENTINEL_PROVIDER=anthropic  # fallback only, see above
```
`backend/modal_app.py`'s `@app.function(secrets=[...])` declares all four by
name; Modal injects each one's key/value pairs as environment variables into
the container at runtime, so `os.environ["ANTHROPIC_API_KEY"]` etc. resolve
inside Modal exactly like they do locally — `gateway.py` doesn't know or care
which environment it's running in. All three provider secrets are typically
created once up front (so any provider can be selected via `sentinel.yaml` +
redeploy without re-touching Modal secrets); only `sentinel-provider` would
ever need `--force` recreating, and only if you were relying on the fallback
path rather than `sentinel.yaml`.

## Demo Deployment

See `backend/README.md` and `frontend/README.md` for deployment instructions
(Modal for the backend, GitHub Pages via `docs/` for the frontend).

## License

MIT
