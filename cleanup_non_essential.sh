#!/bin/bash
#
# Remove non-essential files from sentinel-demo
# These files are not needed for the demo backend/frontend

set -e

DEMO_DIR="/Users/anjesh.dubey/AIProjects/sentinel-demo"
cd "$DEMO_DIR"

echo "🧹 Removing non-essential files from sentinel-demo..."
echo ""

# Files to remove:
# 1. CLI - Demo backend calls triage_alert() directly
# 2. __main__ - CLI entry point
# 3. MCP server - Not used in demo flow
# 4. output/jsonl.py - Demo doesn't persist to files
# 5. observability/tracer.py - Langfuse is optional, adds complexity
# 6. scripts/index_seed_runbooks.py - Can be done once during setup

FILES_TO_REMOVE=(
    "src/sentinel/cli.py"
    "src/sentinel/__main__.py"
    "src/sentinel/mcp/__init__.py"
    "src/sentinel/mcp/__main__.py"
    "src/sentinel/mcp/server.py"
    "src/sentinel/output/jsonl.py"
    "src/sentinel/observability/tracer.py"
    "scripts/index_seed_runbooks.py"
)

for file in "${FILES_TO_REMOVE[@]}"; do
    if [ -f "$file" ]; then
        echo "  ❌ Removing: $file"
        git rm "$file"
    else
        echo "  ⚠️  Not found: $file"
    fi
done

# Remove empty mcp directory
if [ -d "src/sentinel/mcp" ]; then
    echo "  ❌ Removing empty directory: src/sentinel/mcp/"
    rmdir src/sentinel/mcp 2>/dev/null || echo "  ⚠️  Directory not empty or already removed"
fi

# Remove scripts directory if empty
if [ -d "scripts" ]; then
    if [ -z "$(ls -A scripts)" ]; then
        echo "  ❌ Removing empty directory: scripts/"
        rmdir scripts
    fi
fi

echo ""
echo "✅ Cleanup complete!"
echo ""
echo "Files removed:"
echo "  • CLI interface (cli.py, __main__.py)"
echo "  • MCP server (3 files)"
echo "  • JSONL persistence (output/jsonl.py)"
echo "  • Langfuse tracer (observability/tracer.py)"
echo "  • Indexing script (can be run once during setup)"
echo ""
echo "Remaining files: Demo backend will:"
echo "  ✅ Load alerts from JSON"
echo "  ✅ Call triage_alert() directly"
echo "  ✅ Stream TraceCollector events via SSE"
echo "  ✅ Return IncidentSummary as JSON"
echo ""
echo "Next: Review changes and commit"
echo "  git status"
echo "  git commit -m 'refactor: remove non-essential files (CLI, MCP, JSONL, Langfuse)'"
echo "  git push"
