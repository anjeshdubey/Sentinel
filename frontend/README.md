# Sentinel Demo Frontend

Static HTML/CSS/JS frontend deployed to GitHub Pages.

## Local Development

```bash
# Serve locally
python -m http.server 8000

# Open http://localhost:8000
```

## Deployment

1. Push to GitHub
2. Enable GitHub Pages: Settings → Pages → Source: `main` branch, `/frontend` folder
3. Access at: `https://<username>.github.io/sentinel-demo/`

## Files

- `index.html` — Landing page + scenario picker + trace panel
- `style.css` — Styling
- `app.js` — Fetch logic + SSE streaming + trace rendering
- `scenarios.json` — Metadata for 4 demo scenarios
