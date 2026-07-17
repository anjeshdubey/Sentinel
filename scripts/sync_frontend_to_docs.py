"""Mirror frontend/ into docs/ for GitHub Pages.

frontend/ is the human-edited source; docs/ is a generated copy (GitHub Pages
is dashboard-configured to serve /docs on main). This script is the single
definition of what gets mirrored, called by both CI jobs in
.github/workflows/frontend-docs-sync.yml so "what's mirrored" can't drift
between a push-time sync job and a PR-time drift check.

    python scripts/sync_frontend_to_docs.py          # copy frontend/ -> docs/
    python scripts/sync_frontend_to_docs.py --check   # report drift, exit 1 if any
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
DOCS_DIR = REPO_ROOT / "docs"

# frontend/README.md documents the source copy itself and has no docs/
# counterpart to mirror into.
_EXCLUDE_NAMES = {"README.md"}
_MIRRORED_SUFFIXES = {".html", ".js", ".css", ".json"}


def mirrored_files() -> list[Path]:
    return sorted(
        p
        for p in FRONTEND_DIR.iterdir()
        if p.is_file()
        and p.suffix in _MIRRORED_SUFFIXES
        and p.name not in _EXCLUDE_NAMES
    )


def check() -> int:
    """Report which mirrored files differ; exit 1 if any do."""
    stale: list[str] = []
    for src in mirrored_files():
        dst = DOCS_DIR / src.name
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            stale.append(src.name)

    if stale:
        print("docs/ is out of sync with frontend/ for:")
        for name in stale:
            print(f"  - {name}")
        return 1

    print("docs/ mirrors frontend/ for all tracked files.")
    return 0


def sync() -> int:
    """Copy frontend/ -> docs/, reporting which files actually changed."""
    changed: list[str] = []
    for src in mirrored_files():
        dst = DOCS_DIR / src.name
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dst)
            changed.append(src.name)

    if changed:
        print("Updated docs/ from frontend/ for:")
        for name in changed:
            print(f"  - {name}")
    else:
        print("docs/ already matched frontend/ — nothing to do.")
    return 0


def main() -> int:
    if "--check" in sys.argv[1:]:
        return check()
    return sync()


if __name__ == "__main__":
    sys.exit(main())
