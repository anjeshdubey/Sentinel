# Deployment & Secrets

Two independent deploy targets, one per half of the app — see
[Architecture § Deployment targets](architecture.md#deployment-targets) for
how they relate.

## Backend — Modal

```bash
modal deploy backend/modal_app.py  # run from repo root; relative-CWD paths will fail otherwise
```

`backend/modal_app.py` wraps `demo_app.app` in `@modal.asgi_app()`. `sentinel.yaml`
is baked into the image via `add_local_dir`, so **a redeploy is required after
any `sentinel.yaml` change** — there's no live-reload on Modal.

Provider secrets live in Modal's own encrypted secret store, entirely separate
from `.env` and the git repo:

```bash
modal secret create anthropic-api-key ANTHROPIC_API_KEY=sk-ant-...
modal secret create gemini-api-key GEMINI_API_KEY=...
modal secret create groq-api-key GROQ_API_KEY=...
modal secret create sentinel-provider SENTINEL_PROVIDER=anthropic  # fallback only
```

All three provider secrets are typically created once, up front, so any
provider can be selected via `sentinel.yaml` + redeploy without re-touching
Modal secrets.

## Frontend — GitHub Pages

`frontend/` is the working copy; `docs/` is a generated mirror kept in sync by
CI (`scripts/sync_frontend_to_docs.py`, see
[Contributing § Frontend / docs sync](contributing.md#frontend-docs-sync)).
GitHub Pages is dashboard-configured (Settings → Pages) to serve `/docs` on
`main` — pushing to `frontend/` and letting the sync bot update `docs/` is the
entire deploy step; there's no separate build or publish command to run.

## Local development

**Local secrets**: `backend/.env` (git-ignored) holds provider keys as a
reference file, but **is not auto-loaded** — nothing calls `load_dotenv()`.
Export it into the shell running `uvicorn` yourself:

```bash
export $(grep -v '^#' backend/.env | xargs)
uvicorn backend.demo_app:app --reload --port 8000
```

Switching provider/model: edit `model.provider` / `model.default` in
`sentinel.yaml` (the one file to touch for this, locally or on Modal). The
dev server re-reads it per request via `load_settings()` — no restart needed
for a yaml-only change; restart if you added a new env var.

Full detail (secrets precedence, provider-key resolution order) lives in the
root [`README.md`](https://github.com/anjeshdubey/sentinel/blob/main/README.md#secrets-management).
