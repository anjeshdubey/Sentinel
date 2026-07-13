# Sentinel Demo Backend

FastAPI + Modal backend for streaming triage execution.

## Setup

1. Install Modal: `pip install modal`
2. Authenticate: `modal token new`
3. Set secrets: `modal secret create anthropic-api-key ANTHROPIC_API_KEY=sk-ant-...`
4. Deploy: `modal deploy backend/modal_app.py` (run from the repo root)

## Endpoints

- `GET /scenarios` — List demo scenarios
- `POST /triage/stream` — Execute triage with SSE streaming
- `GET /health` — Health check

See `demo_endpoints.py` for implementation.
