# Sentinel Demo Repository — Status

**Repository:** https://github.com/anjeshdubey/sentinel  
**Branch:** main  
**Status:** ✅ **READY FOR BACKEND/FRONTEND IMPLEMENTATION**

---

## ✅ Completed

### 1. Clean Repository Structure
- **Location:** `/Users/anjeshdubey/projects/sentinel-demo`
- **Remote:** https://github.com/anjeshdubey/sentinel

### 2. File Optimization
| Metric | Result |
|--------|--------|
| Python files | **39** (down from 67, -42%) |
| Total files | **77** (down from 85, -9%) |
| Lines of code | **~5,429** (down from ~6,462, -16%) |
| Dependencies | **5 core** (down from 8, -38%) |

### 3. Files Included

**Core Implementation (39 Python files):**
- ✅ `triage/` — Engine, extractor, prompts (4 files)
- ✅ `tools/` — Ownership, deploys, dependencies (9 files)
- ✅ `retrieval/` — RAG with Qdrant + BGE embedder (9 files)
- ✅ `models/` — RawAlert, IncidentSummary, enums (5 files)
- ✅ `ingestion/` — Loader, service extraction (3 files)
- ✅ `observability/` — TraceCollector for SSE (2 files)
- ✅ `output/` — Console output for dev (2 files)
- ✅ `utils/` — Hashing utilities (2 files)
- ✅ Core: config, gateway (3 files)

**Data Files:**
- ✅ 8 markdown runbooks (SRE troubleshooting scenarios)
- ✅ 3 CMDB JSON files (ownership, deploys, dependencies)

**Configuration:**
- ✅ `sentinel.yaml`
- ✅ `pyproject.toml`
- ✅ `requirements.txt`

**Documentation:**
- ✅ `README.md` — Getting started
- ✅ `DEMO_STRUCTURE.md` — Repository layout
- ✅ `CLEANUP_SUMMARY.md` — Cleanup details
- ✅ `STATUS.md` — This file

**Infrastructure:**
- ✅ `backend/` — Stubs for Modal deployment
- ✅ `frontend/` — Stubs for GitHub Pages
- ✅ `.gitignore`

### 4. Files Removed (Not Needed for Demo)

**From initial copy (93 files excluded):**
- ❌ All test files (56 integration + unit tests)
- ❌ Evaluation scripts (21 test alert fixtures)
- ❌ Internal documentation (12 planning docs)
- ❌ Development workflows

**Additional cleanup (8 files removed):**
- ❌ CLI interface (`cli.py`, `__main__.py`)
- ❌ MCP server (3 files)
- ❌ JSONL persistence (`output/jsonl.py`)
- ❌ Langfuse tracer (`observability/tracer.py`)
- ❌ Indexing script (can be done during setup)

### 5. Dependency Simplification

**Core dependencies (5):**
```txt
anthropic>=0.25.0             # LLM SDK
instructor>=1.0.0             # Structured extraction
pydantic>=2.0.0               # Data validation
qdrant-client>=1.8.0          # Vector store
sentence-transformers>=2.3.0  # Embeddings
```

**Removed dependencies (3):**
- ❌ `typer` — CLI framework (not needed)
- ❌ `rich` — Console formatting (not needed)
- ❌ `langfuse` — Observability backend (optional)

---

## 🚧 To Be Implemented

### 1. Demo Alert Fixtures (backend/fixtures/)

Create 4 JSON files per PRD Section 3.6.4:

- [ ] `checkout-deploy.json` — Critical outage after deploy
  - Alert timestamp: 12 min after deploy in CMDB
  - Maps to: `runbook_checkout_503.md`
  - Keywords: "503", "connection pool", "checkout"

- [ ] `slack-vague.json` — Ambiguous user report
  - Casual Slack message format
  - Maps to: `runbook_slack_vague_reports.md`
  - Low confidence expected

- [ ] `latency-no-deploy.json` — Performance degradation
  - No checkout deploys in 24h before alert
  - Maps to: `runbook_latency_investigation.md`
  - Clean negative case (no recent deploy)

- [ ] `past-incident-match.json` — Historical match
  - Auth service intermittent 401 errors
  - Maps to: `runbook_past_incident_pattern_matching.md`
  - Keywords: "OAuth", "401", "cache"

**Requirements:**
- Timestamps must align with CMDB data
- Keywords must overlap with runbooks (high similarity scores)
- All services must exist in `ownership.json`

### 2. Backend Implementation (backend/)

**Files to create:**

- [ ] `demo_app.py` — Modal deployment wrapper
  ```python
  import modal
  app = modal.App("sentinel-demo")
  # FastAPI endpoints with SSE streaming
  ```

- [ ] `demo_endpoints.py` — API routes
  - `GET /scenarios` — Return 4 scenario metadata
  - `POST /triage/stream` — Execute triage with SSE
  - `GET /health` — Health check

- [ ] `guardrails.py` — Rate limiting + budget
  - 5 requests per 10 min per IP
  - $50/month hard cap
  - Cache responses by (scenario_id, date)

**Tech stack:**
- FastAPI (async endpoints)
- sse-starlette (SSE streaming)
- Modal (serverless deployment)
- slowapi (rate limiting)
- Modal Dict or Redis (caching)

### 3. Frontend Implementation (frontend/)

**Files to create:**

- [ ] `index.html` — Single-page app
  - Header (title, description, tech badges)
  - Scenario picker (4 cards, primary CTA)
  - Trace panel (animated timeline)
  - Footer (attribution)

- [ ] `style.css` — Intentional design
  - Not default Bootstrap
  - Mobile-responsive
  - Dark mode support

- [ ] `app.js` — Client logic
  - Fetch scenarios from `/scenarios`
  - POST to `/triage/stream` with EventSource
  - Animate trace events as they arrive
  - Fallback to cached trace on timeout

- [ ] `scenarios.json` — Metadata
  ```json
  [
    {
      "id": "checkout-deploy",
      "title": "🔴 Checkout down after deploy",
      "severity_hint": "critical",
      "why_interesting": "Shows deploy correlation + ownership lookup"
    },
    ...
  ]
  ```

**Design goal:** Visible reasoning trace (not black box)

### 4. Deployment

- [ ] **Backend to Modal**
  ```bash
  cd backend/
  modal deploy demo_app.py
  # Get URL: https://sentinel-demo--web.modal.run
  ```

- [ ] **Frontend to GitHub Pages**
  - Enable GitHub Pages: Settings → Pages → `/frontend` folder
  - URL: `https://adubey.github.io/sentinel-demo/` (or custom domain)

- [ ] **Update frontend with backend URL**
  - Configure CORS in backend
  - Update `app.js` with Modal URL

---

## 📊 Success Metrics (from PRD)

**Quantitative (tracked via backend logs):**
- [ ] 50+ unique IPs in first week
- [ ] 150+ scenario clicks in first week
- [ ] P50 latency <2s cached, <10s fresh
- [ ] <$30 in first month
- [ ] >75% cache hit rate after day 1

**Qualitative (user feedback):**
- [ ] ≥3 comments about "didn't realize it was doing X under the hood"
- [ ] ≥1 question about architecture/implementation
- [ ] Zero "this looks fake" reports

**Red flags:**
- ⚠️ >10% users refresh mid-trace → too slow
- ⚠️ <30s avg session → not engaging
- ⚠️ >$10 spent in first 3 days → caching broken

---

## 🔗 Links

- **Demo Repo:** https://github.com/anjeshdubey/sentinel
- **PRD:** `/Users/anjesh.dubey/adubey-obsidian-vault/01_Projects/Sentinel SRE Triage Agent/sentinel_demo_mvp_prd.md`
- **Live Demo:** [To be deployed]

---

## 📝 Next Steps

1. **Create alert fixtures** (4 JSON files in `backend/fixtures/`)
2. **Implement backend** (`backend/demo_app.py`, `demo_endpoints.py`, `guardrails.py`)
3. **Implement frontend** (`frontend/index.html`, `style.css`, `app.js`)
4. **Deploy** (Modal + GitHub Pages)
5. **Test** (all 4 scenarios end-to-end)
6. **Soft launch** (share with 3-5 trusted colleagues)
7. **Public launch** (announce, monitor metrics)

---

**Last Updated:** 2026-07-09  
**Status:** Ready for implementation  
**Commit:** 726e136
