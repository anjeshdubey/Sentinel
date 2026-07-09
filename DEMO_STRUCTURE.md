# Sentinel Demo Repository Structure

## Overview

This is a **clean, demo-focused repository** containing only the essential files needed for the public demo. All test files, development scripts, and internal documentation have been excluded.

## Repository: https://git.soma.salesforce.com/adubey/sentinel-demo

## What's Included

### Core Implementation (66 files)

**Complete triage engine:**
- ✅ RAG retrieval with Qdrant + BGE-small embedder
- ✅ Tool enrichment (ownership, deploys, dependencies)
- ✅ Structured extraction with Instructor + Anthropic SDK
- ✅ Trace instrumentation with correlation IDs
- ✅ Security hardening (XML escaping, validation, size limits)

**Production data:**
- ✅ 8 SRE runbooks with realistic troubleshooting content
- ✅ CMDB data aligned for demo scenarios
- ✅ Service ownership, deployment history, dependencies

**Output formats:**
- ✅ Rich console output
- ✅ JSONL persistence
- ✅ SSE-ready trace events

**Optional:**
- ✅ MCP server for tool discovery

### What's Excluded

❌ **Tests** (56 integration tests, unit tests remain in main repo)  
❌ **Evaluation scripts** (eval dataset, accuracy scoring)  
❌ **Development docs** (planning, architecture reviews, spike designs)  
❌ **Build artifacts** (.venv, __pycache__, data/qdrant/)  
❌ **Internal workflows** (.claude/workflows/)

## Directory Structure

```
sentinel-demo/
├── src/sentinel/           # Core implementation (complete)
│   ├── models/            # RawAlert, IncidentSummary, enums
│   ├── ingestion/         # Service extraction, alert loading
│   ├── triage/            # Engine, extractor, prompts
│   ├── tools/             # Ownership, deploys, dependencies
│   ├── retrieval/         # RAG with Qdrant + embedder
│   ├── observability/     # TraceCollector, Langfuse integration
│   ├── output/            # Console, JSONL
│   ├── utils/             # Hashing utilities
│   ├── mcp/               # MCP server (optional)
│   └── data/              # CMDB + runbooks
│       ├── cmdb/          # ownership.json, deploys.json, dependencies.json
│       └── runbooks/      # 8 markdown runbooks
├── backend/               # Modal deployment (stubs, to be implemented)
│   ├── README.md
│   ├── requirements.txt
│   ├── fixtures/          # 4 demo alert JSON files (to be created)
│   ├── demo_app.py        # FastAPI + Modal (to be implemented)
│   ├── demo_endpoints.py  # /scenarios, /triage/stream (to be implemented)
│   └── guardrails.py      # Rate limiting, budget controls (to be implemented)
├── frontend/              # GitHub Pages deployment (stubs, to be implemented)
│   ├── README.md
│   ├── index.html         # Landing page + scenario picker (to be implemented)
│   ├── style.css          # Styling (to be implemented)
│   ├── app.js             # SSE streaming + trace rendering (to be implemented)
│   └── scenarios.json     # Metadata for 4 scenarios (to be implemented)
├── scripts/               # Utilities
│   └── index_seed_runbooks.py  # Index runbooks into Qdrant
├── sentinel.yaml          # Configuration
├── pyproject.toml         # Python project metadata
├── requirements.txt       # Dependencies
├── README.md              # Getting started
├── .gitignore             # Ignore patterns
└── DEMO_STRUCTURE.md      # This file
```

## File Count Comparison

| Category | Main Repo | Demo Repo | Excluded |
|----------|-----------|-----------|----------|
| Source files (.py) | 67 | 67 | 0 |
| Data files (CMDB, runbooks) | 11 | 11 | 0 |
| Config files | 3 | 3 | 0 |
| Test files | 56 | 0 | **56** |
| Fixtures (test alerts) | 21 | 0 | **21** |
| Documentation | 15 | 3 | **12** |
| Scripts | 5 | 1 | **4** |
| **Total** | **178** | **85** | **93** |

**Reduction: 52% fewer files** (93 excluded)

## Next Steps (as per PRD)

### 1. Create Demo Alert Fixtures (backend/fixtures/)

According to `sentinel_demo_mvp_prd.md` Section 3.6.4:

- `checkout-deploy.json` — Critical outage after deploy (maps to runbook_checkout_503)
- `slack-vague.json` — Ambiguous user report (maps to runbook_slack_vague_reports)
- `latency-no-deploy.json` — Performance degradation without recent changes
- `past-incident-match.json` — Symptom match to historical incident

**Key Requirements:**
- Timestamps must align with CMDB deploy data (checkout-deploy alert 12 min after deploy)
- No checkout deploys 24h before latency-no-deploy alert
- Keywords overlap with runbooks for high similarity scores

### 2. Implement Backend (backend/)

**demo_app.py** — Modal deployment wrapper  
**demo_endpoints.py** — FastAPI endpoints:
- `GET /scenarios` — Return 4 scenario metadata
- `POST /triage/stream` — Execute triage with SSE streaming
- `GET /health` — Health check

**guardrails.py** — Rate limiting + budget controls:
- 5 requests per 10 minutes per IP
- $50/month hard cap
- Cache responses (scenario_id, date) to minimize costs

### 3. Implement Frontend (frontend/)

**index.html** — Single-page app:
- Header with title + description + tech badges
- 4 scenario picker cards (primary CTA)
- Trace panel (appears after scenario click)
- Footer with attribution

**app.js** — Client logic:
- Fetch scenarios from `/scenarios`
- POST to `/triage/stream` with EventSource for SSE
- Animate trace timeline as events arrive
- Fallback to cached trace on timeout

**style.css** — Intentional design (not default Bootstrap)

### 4. Deploy

**Backend:** `modal deploy backend/demo_app.py`  
**Frontend:** GitHub Pages from `/frontend` folder

## Testing Locally

```bash
cd /Users/anjesh.dubey/AIProjects/sentinel-demo

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Index runbooks
python scripts/index_seed_runbooks.py

# Test triage (once alert fixtures created)
python -m sentinel.cli triage --alert backend/fixtures/checkout-deploy.json --verbose
```

## Repository URLs

- **Demo Repo:** https://git.soma.salesforce.com/adubey/sentinel-demo
- **Main Repo:** https://git.soma.salesforce.com/adubey/sentinel
- **Live Demo:** [Coming Soon after frontend deployment]

## Architecture

Built from **raw Anthropic SDK** (not a framework):
- Claude Sonnet 4 for structured extraction
- Qdrant for vector search (embedded mode, no Docker)
- BGE-small-en-v1.5 for text embeddings
- FastAPI + Modal for serverless backend
- Static HTML/CSS/JS frontend (GitHub Pages)

## Success Metrics (from PRD)

**Quantitative:**
- 50+ unique IPs in first week
- 150+ scenario clicks in first week
- P50 latency <2s cached, <10s fresh
- <$30 in first month
- >75% cache hit rate

**Qualitative:**
- ≥3 comments about "didn't realize it was doing X under the hood"
- ≥1 question about architecture/implementation
- Zero "this looks fake" reports

## License

MIT (to be added)

---

**Last Updated:** 2026-07-09  
**Created By:** Anjesh Dubey  
**Commit:** 9e67b2d (Initial structure)
