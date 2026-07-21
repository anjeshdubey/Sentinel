"""Modal deployment wrapper for the Sentinel demo API.

Wraps the plain FastAPI app in `backend.demo_app` with a Modal ASGI function.
Deploy with:

    modal deploy backend/modal_app.py

Requires (create once; each provider's key only needs to exist so its
container has the credential available -- it doesn't need to be "active"):
    modal secret create together-api-key TOGETHER_API_KEY=...
    modal secret create anthropic-api-key ANTHROPIC_API_KEY=sk-ant-...
    modal secret create gemini-api-key GEMINI_API_KEY=...
    modal secret create groq-api-key GROQ_API_KEY=...
    modal secret create sentinel-provider SENTINEL_PROVIDER=together
    modal secret create upstash-redis UPSTASH_REDIS_REST_URL=... UPSTASH_REDIS_REST_TOKEN=...

sentinel.gateway.GatewayConfig.chain_from_env() automatically builds a
fallback chain from whichever of the above provider keys are present
(Together AI -> Groq -> Gemini -> Anthropic priority) -- set as many as you
have for real resilience. The `sentinel-provider` secret only pins which one
is *primary*; sentinel.yaml's model.provider is threaded explicitly through
triage_alert() -> extract_incident() -> GatewayConfig.chain_from_env(
primary_provider=...), which takes priority over this env var. The
`upstash-redis` secret is optional: when both UPSTASH_REDIS_REST_URL and
UPSTASH_REDIS_REST_TOKEN are set, successful primary-provider extractions
are cached so repeated identical alerts don't re-hit the LLM. See the
"Switching LLM provider/model" section in the repo root README.md for the
full picture (local + Modal).

To switch the primary provider, edit `model.provider` (and `model.default`)
in sentinel.yaml at the repo root, then redeploy:
    modal deploy backend/modal_app.py
"""

from __future__ import annotations

from pathlib import Path

import modal

REPO_ROOT = "/root/app"
LOCAL_REPO_ROOT = Path(__file__).resolve().parent.parent

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "fastapi>=0.109.0",
        "sse-starlette>=2.0.0",
        "slowapi>=0.1.9",
        "redis>=5.0.0",
        "anthropic>=0.40.0",
        "google-genai>=1.0.0",
        "jsonref>=1.1.0",
        "groq>=0.11.0",
        "httpx>=0.27.0",
        "instructor>=1.5.0",
        "mcp[cli]>=1.20.0",
        "openai>=1.30.0",
        "pydantic>=2.9.0",
        "pydantic-settings>=2.6.0",
        "typer>=0.12.0",
        "rich>=13.9.0",
        "pyyaml>=6.0.0",
        "python-frontmatter>=1.1.0",
        "sentence-transformers>=3.0.0",
        "numpy>=1.26.0",
        "qdrant-client>=1.12.0",
        "langgraph>=0.2.0",
    )
    .add_local_dir(
        str(LOCAL_REPO_ROOT),
        remote_path=REPO_ROOT,
        copy=True,
        ignore=[
            ".git",
            ".git/**",
            "__pycache__",
            "**/__pycache__",
            "frontend",
            "frontend/**",
            "backend/.cache",
            "backend/.cache/**",
            "*.pyc",
            ".venv",
            ".venv/**",
        ],
    )
    .run_commands(f"pip install -e {REPO_ROOT}")
    .workdir(REPO_ROOT)
)

app = modal.App("sentinel-demo", image=image)


@app.function(
    secrets=[
        modal.Secret.from_name("together-api-key"),
        modal.Secret.from_name("anthropic-api-key"),
        modal.Secret.from_name("gemini-api-key"),
        modal.Secret.from_name("groq-api-key"),
        modal.Secret.from_name("sentinel-provider"),
        modal.Secret.from_name("upstash-redis"),
    ],
    min_containers=0,
)
@modal.concurrent(max_inputs=10)
@modal.asgi_app()
def web():
    import os
    import sys

    os.chdir(REPO_ROOT)
    sys.path.insert(0, REPO_ROOT)

    from backend.demo_app import app as fastapi_app

    return fastapi_app
