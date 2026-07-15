# Sentinel Demo Backend

FastAPI + Modal backend for streaming triage execution.

## Setup

1. Install Modal: `pip install modal`
2. Authenticate: `modal token new`
3. Set secrets (once; separate store from local `.env`, see repo root
   README's "Secrets management" section for the full picture):
   ```bash
   modal secret create anthropic-api-key ANTHROPIC_API_KEY=sk-ant-...
   modal secret create gemini-api-key GEMINI_API_KEY=...
   modal secret create groq-api-key GROQ_API_KEY=...
   modal secret create sentinel-provider SENTINEL_PROVIDER=anthropic
   ```
4. Deploy: `modal deploy backend/modal_app.py` (run from the repo root)

To switch which LLM provider is active (locally or on Modal), edit
`model.provider`/`model.default` in `sentinel.yaml` at the repo root and
redeploy — see the repo root README's "Switching LLM provider/model" section.

## Endpoints

- `GET /scenarios` — List demo scenarios
- `POST /triage/stream` — Execute triage with SSE streaming
- `GET /health` — Health check

See `demo_endpoints.py` for implementation.
