# Contributing

## Local setup

```bash
pip install -r requirements.txt
uvicorn backend.demo_app:app --reload --port 8000
```

For frontend work, serve `frontend/` locally (`python -m http.server` from
that directory) and open it via `localhost` — `app.js` auto-targets your local
backend when served that way.

## Before opening a PR

- `pytest -m "not integration and not slow"` passes
- `ruff check .` passes (if Python files changed)
- The PR template checklist is filled out honestly, not rubber-stamped —
  it exists so reviewers don't have to re-derive "did docs get updated?"
  from the diff themselves

## Frontend / docs sync

`frontend/` is the only place to edit the demo UI. `docs/` is a generated
mirror, kept in sync by `scripts/sync_frontend_to_docs.py`, run automatically
by `.github/workflows/frontend-docs-sync.yml`:

- On push to `main` touching `frontend/**`: a bot commit updates `docs/` to
  match, with a rebase-retry loop if it races another push.
- On a PR touching `frontend/**` or `docs/*`: a read-only advisory check warns
  if they've drifted — it does **not** block the PR, since the mirror
  self-heals on merge regardless.

Never hand-edit `docs/*.html`/`.js`/`.css`/`.json` directly — those edits will
be silently overwritten by the next sync.

## Editing this site

This site (`engineering-docs/` → `docs/engineering/`) is built with
[MkDocs Material](https://squidfunk.github.io/mkdocs-material/) and Mermaid
diagrams (fenced ` ```mermaid ` blocks, rendered client-side in the browser).

Preview locally before opening a PR:

```bash
pip install -e ".[docs]"
mkdocs serve
```

`mkdocs serve` renders in-memory and won't touch `docs/engineering/` on disk —
use it for iteration, not `mkdocs build`, so you don't leave a stale local
build sitting in your working tree.

**Mermaid diagrams aren't syntax-checked by CI** — `mkdocs build --strict`
validates MkDocs structure (nav references, internal links, plugin config),
not what's inside a Mermaid fence, since Mermaid renders client-side at view
time. Always preview a diagram change with `mkdocs serve` before merging.

The built site publishes the same way the frontend mirror does: a bot commit
on push to `main`, path-scoped to `docs/engineering/**` only, so it can never
touch the sibling frontend-mirror files that also live under `docs/`.

## Architecture Decision Records

If your PR makes an architecturally-significant call (new dependency, changed
data flow, changed deploy target), add an ADR under `engineering-docs/adr/` —
see [Architecture Decisions](adr/index.md) for the template and existing
records. This is how "why did we do it this way" survives past the PR
discussion that decided it.
