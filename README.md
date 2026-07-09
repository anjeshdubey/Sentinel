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

## Quick Start (Local)

```bash
# Install dependencies
pip install -r requirements.txt

# Index runbooks
python scripts/index_seed_runbooks.py

# Run triage on demo alert
python -m sentinel.cli triage --alert backend/fixtures/checkout-deploy.json

# Start MCP server (optional)
python -m sentinel.mcp
```

## Demo Deployment

See `backend/` and `frontend/` directories for deployment instructions.

## License

MIT
