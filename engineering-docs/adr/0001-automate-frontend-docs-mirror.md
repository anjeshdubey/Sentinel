# 0001. Automate the frontend/ → docs/ mirror instead of retiring it

**Status**: Accepted
**Date**: 2026-07-17

## Context

`frontend/architecture.html` and the other demo UI files are hand-authored,
JS/JSON-driven pages. GitHub Pages is dashboard-configured to serve `/docs` on
`main`, and the FastAPI backend never serves `frontend/` directly (confirmed:
no `StaticFiles`/`Jinja2Templates` mount, and `backend/modal_app.py` explicitly
excludes `frontend/**` from the Modal deploy image) — so `docs/` had to exist
as a copy of `frontend/` for the live demo to work at all.

That copy was maintained by hand. Git history shows at least one case
(commit `125c8a6`) where a `frontend/` change shipped without the matching
`docs/` update, requiring a manual catch-up commit — the live demo silently
served stale content in the interim.

The pages themselves are externally visible (they're the actual live demo) —
rewriting them as Markdown/MkDocs output, or otherwise retiring them, was
explicitly ruled out. The problem to solve was the manual sync step, not the
pages.

## Decision

Keep `frontend/` and `docs/` as two real file trees (not a symlink — GitHub
Pages' Jekyll pipeline runs in safe mode and silently refuses to follow
symlinks, which would risk quietly dropping files from the live deploy with
no CI signal). Designate `frontend/` the human-edited source and `docs/` a
generated mirror, kept identical by `scripts/sync_frontend_to_docs.py`:

- On push to `main` touching `frontend/**`: a bot commit updates `docs/`,
  with a rebase-retry loop on push races.
- On a PR touching `frontend/**` or `docs/*`: a read-only advisory check
  warns on drift without blocking the PR — main self-heals on merge either
  way, and PR-branch bot-pushes have their own failure modes (force-push
  during review, no-op on fork PRs with a read-only `GITHUB_TOKEN`).

## Consequences

The manual sync step — the thing that actually failed in commit `125c8a6` —
is gone; `docs/` cannot silently drift from `frontend/` past the next push to
`main`. The pages themselves are untouched, satisfying the external-visibility
constraint. The cost is one more CI workflow
(`.github/workflows/frontend-docs-sync.yml`) and one more thing a contributor
needs to know: edit `frontend/`, never `docs/*.html`/`.js`/`.css`/`.json`
directly — documented in [Contributing](../contributing.md#frontend-docs-sync)
and the PR template checklist.
