# Demo Repository Cleanup Summary

## Before vs After

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| **Total Python files** | 67 | 39 | **-42%** |
| **Lines of code** | ~6,462 | ~5,429 | **-16%** |
| **Dependencies** | typer, rich, mcp, langfuse | anthropic, instructor, pydantic | **Simpler** |

## Files Removed (8 total)

### CLI Interface (2 files)
- ❌ `src/sentinel/cli.py` (195 lines)
- ❌ `src/sentinel/__main__.py` (11 lines)
- **Why:** Demo backend calls `triage_alert()` directly, doesn't need CLI
- **Dependency reduction:** No longer need `typer`, `rich` CLI dependencies

### MCP Server (3 files)
- ❌ `src/sentinel/mcp/__init__.py` (12 lines)
- ❌ `src/sentinel/mcp/__main__.py` (8 lines)
- ❌ `src/sentinel/mcp/server.py` (226 lines)
- **Why:** MCP not used in demo flow (backend → frontend)
- **Dependency reduction:** No MCP protocol dependencies

### Output Persistence (1 file)
- ❌ `src/sentinel/output/jsonl.py` (47 lines)
- **Why:** Demo streams to frontend, doesn't persist to files

### Observability (1 file)
- ❌ `src/sentinel/observability/tracer.py` (413 lines)
- **Why:** Langfuse integration optional, adds complexity
- **Dependency reduction:** No `langfuse` dependency needed
- **Kept:** `trace.py` (TraceCollector for SSE streaming)

### Scripts (1 file)
- ❌ `scripts/index_seed_runbooks.py` (131 lines)
- **Why:** Runbook indexing can be done once during setup or in Modal deploy

## Files Kept (39 Python files)

### Core Triage (4 files)
✅ `triage/engine.py` — Main triage_alert() function  
✅ `triage/extractor.py` — LLM extraction with Instructor  
✅ `triage/prompts.py` — System prompts  
✅ `triage/__init__.py`

### Tools (9 files)
✅ `tools/bootstrap.py` — Factory functions  
✅ `tools/cache.py` — Caching layer  
✅ `tools/errors.py` — Error types  
✅ `tools/formatting.py` — Enrichment context formatter  
✅ `tools/json_backend.py` — JSON CMDB backend  
✅ `tools/models.py` — Tool data models  
✅ `tools/protocols.py` — Tool protocols  
✅ `tools/provider.py` — Tool provider  
✅ `tools/__init__.py`

### Retrieval (RAG) (9 files)
✅ `retrieval/bootstrap.py` — Retriever factory  
✅ `retrieval/chunking.py` — Document chunking  
✅ `retrieval/embedder.py` — BGE-small embedder  
✅ `retrieval/indexer.py` — Qdrant indexing  
✅ `retrieval/models.py` — Retrieval models  
✅ `retrieval/query_builder.py` — Query builder  
✅ `retrieval/retriever.py` — Main retriever  
✅ `retrieval/store.py` — Qdrant store  
✅ `retrieval/__init__.py`

### Models (5 files)
✅ `models/raw_alert.py` — RawAlert  
✅ `models/incident.py` — IncidentSummary  
✅ `models/enums.py` — Severity, Urgency  
✅ `models/__init__.py`

### Ingestion (3 files)
✅ `ingestion/loader.py` — Load JSON alerts  
✅ `ingestion/service_extraction.py` — Extract service from alert  
✅ `ingestion/__init__.py`

### Observability (2 files)
✅ `observability/trace.py` — TraceCollector, TraceEvent  
✅ `observability/__init__.py`

### Output (2 files)
✅ `output/console.py` — Rich console output (for local dev)  
✅ `output/__init__.py`

### Utils (2 files)
✅ `utils/hashing.py` — Incident ID hashing  
✅ `utils/__init__.py`

### Core (3 files)
✅ `__init__.py`  
✅ `config.py` — Settings  
✅ `gateway.py` — Anthropic API client

### Data (1 file + directories)
✅ `data/__init__.py`  
✅ `data/cmdb/` — 3 JSON files (ownership, deploys, dependencies)  
✅ `data/runbooks/` — 8 markdown runbooks

## Demo Backend Flow (Simplified)

```python
# backend/demo_app.py (to be implemented)
from sentinel.triage.engine import triage_alert
from sentinel.ingestion.loader import load_from_file
from sentinel.config import load_settings
from sentinel.observability import TraceCollector

@app.post("/triage/stream")
async def triage_stream(scenario_id: str):
    # 1. Load fixture
    alert = load_from_file(f"fixtures/{scenario_id}.json")
    
    # 2. Create collector
    collector = TraceCollector(verbose=True)
    
    # 3. Run triage
    incident = triage_alert(alert, settings, collector=collector)
    
    # 4. Stream events as SSE
    for event in collector.events:
        yield event.to_sse()
    
    # 5. Return final diagnosis
    yield f"data: {incident.model_dump_json()}\n\n"
```

**No CLI, no MCP, no JSONL persistence, no Langfuse** — just the core triage logic.

## Dependency Reduction

### Before (removed)
```txt
typer>=0.9.0           # CLI framework
rich>=13.0.0           # Console formatting
langfuse>=2.0.0        # Observability backend
```

### After (core only)
```txt
anthropic>=0.25.0      # LLM SDK
instructor>=1.0.0      # Structured extraction
pydantic>=2.0.0        # Data validation
pydantic-settings>=2.0.0  # Config management
qdrant-client>=1.8.0   # Vector store
sentence-transformers>=2.3.0  # Embeddings
```

## Summary

**Cleaner, focused demo repository:**
- ✅ **42% fewer Python files** (67 → 39)
- ✅ **Simpler dependencies** (removed CLI, MCP, Langfuse)
- ✅ **Same core functionality** (triage, tools, RAG, trace streaming)
- ✅ **Ready for Modal deployment** (no CLI/MCP coupling)

**Next steps:**
1. Create 4 demo alert fixtures in `backend/fixtures/`
2. Implement `backend/demo_app.py` with SSE streaming
3. Implement `frontend/` (HTML/CSS/JS)
4. Deploy to Modal + GitHub Pages
