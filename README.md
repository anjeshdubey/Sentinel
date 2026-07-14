# Sentinel — AI SRE Triage Agent (Demo)

An LLM-powered agent that reads incident alerts, calls tools to gather context, retrieves relevant runbooks, and produces a structured diagnosis.

**Live Demo:** [Coming Soon]

## Architecture

Built from raw Anthropic SDK (not a framework) with:
- **Claude Sonnet 4** for structured extraction
- **RAG (Qdrant)** for runbook retrieval
- **Tool Calling** for context enrichment (ownership, deploys, dependencies)
- **FastAPI + Modal** for serverless backend
- **Static frontend** (GitHub Pages)

## Quick Start (Backend Development)

This repo is designed for deployment as a demo backend + frontend. For local development:

```bash
# Install dependencies
pip install -r requirements.txt

# Test triage function directly
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

**Note:** CLI and MCP server have been removed to minimize dependencies for demo deployment.

### Switching LLM provider/model

Edit `model.provider` and `model.default` in `sentinel.yaml`, save, and the
running dev server (`uvicorn --reload`) picks it up on the next request — no
restart needed. Supported providers: `anthropic`, `gemini`, `groq`. Set the
matching API key env var (`ANTHROPIC_API_KEY` / `GEMINI_API_KEY` /
`GROQ_API_KEY`).

## Demo Deployment

See `backend/` and `frontend/` directories for deployment instructions.

## License

MIT
