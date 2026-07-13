"""Modal deployment wrapper for the Sentinel demo API.

Wraps the plain FastAPI app in `backend.demo_app` with a Modal ASGI function.
Deploy with:

    modal deploy backend/modal_app.py

Requires:
    modal secret create anthropic-api-key ANTHROPIC_API_KEY=sk-ant-...
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
        "httpx>=0.27.0",
        "instructor>=1.5.0",
        "mcp[cli]>=1.20.0",
        "pydantic>=2.9.0",
        "pydantic-settings>=2.6.0",
        "typer>=0.12.0",
        "rich>=13.9.0",
        "pyyaml>=6.0.0",
        "python-frontmatter>=1.1.0",
        "sentence-transformers>=3.0.0",
        "numpy>=1.26.0",
        "qdrant-client>=1.12.0",
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
        ],
    )
    .run_commands(f"pip install -e {REPO_ROOT}")
    .workdir(REPO_ROOT)
)

app = modal.App("sentinel-demo", image=image)


@app.function(
    secrets=[modal.Secret.from_name("anthropic-api-key")],
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
