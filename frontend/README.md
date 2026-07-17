# Sentinel Demo Frontend

Static HTML/CSS/JS frontend deployed to GitHub Pages.

## Local Development

```bash
# Serve locally
python -m http.server 8000

# Open http://localhost:8000
```

## Deployment

`frontend/` is the working copy; `docs/` is a byte-for-byte mirror kept in sync by
`scripts/sync_frontend_to_docs.py` (auto-committed to `main` by
`.github/workflows/frontend-docs-sync.yml` — see the root README's "Repository layout"
section). Do not edit `docs/*.html`/`.js`/`.css`/`.json` directly; edit here instead.

1. Push a change to `frontend/` on GitHub
2. GitHub Pages serves it from `docs/` (Settings → Pages → Source: `main` branch,
   `/docs` folder) once the sync bot updates the mirror
3. Live at: https://anjeshdubey.github.io/sentinel/

## Files

- `index.html` — Landing page + scenario picker + trace panel
- `style.css` — Styling
- `app.js` — Fetch logic + SSE streaming + trace rendering
- `scenarios.json` — Metadata for 4 demo scenarios
