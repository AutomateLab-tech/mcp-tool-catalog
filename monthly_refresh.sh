#!/usr/bin/env bash
# Monthly auto-update cron for the MCP catalog.
# Schedule: 0 3 1 * * /opt/automatelab/mcp-tool-catalog/monthly_refresh.sh
#
# Pipeline:
#   1. snapshot last month's data
#   2. re-validate npm packages
#   3. re-introspect all servers
#   4. diff vs snapshot -> changelog draft
#   5. package parquet + dataset card
#   6. (manual) upload to HuggingFace
#
# Requires: node, npm, python3, pandas, pyarrow, huggingface_hub installed.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATE="$(date -u +%Y-%m-%d)"
SNAPSHOTS="$ROOT/snapshots"
mkdir -p "$SNAPSHOTS"

echo "[$DATE] starting monthly MCP catalog refresh"

# 1. Snapshot
if [[ -f "$ROOT/data/tools.jsonl" ]]; then
  cp "$ROOT/data/tools.jsonl" "$SNAPSHOTS/tools-prev.jsonl"
  cp "$ROOT/data/servers.jsonl" "$SNAPSHOTS/servers-prev.jsonl"
fi

# 2. Validate against npm (catches packages that disappeared)
python3 "$ROOT/validate_npm.py" servers-final.validated.json

# 3. Introspect
SERVERS_FILE="servers-final.validated.json" CONCURRENCY=10 TIMEOUT=120 \
  python3 "$ROOT/introspect.py"

# 4. Diff
python3 "$ROOT/diff_snapshot.py" "$SNAPSHOTS/tools-prev.jsonl" "$ROOT/data/tools.jsonl" \
  > "$ROOT/changelog-$DATE.md" || echo "(no previous snapshot, skipping diff)"

# 5. Package
python3 "$ROOT/package_dataset.py"

echo "[$DATE] done. Review:"
echo "  $ROOT/changelog-$DATE.md"
echo "  $ROOT/hf-dataset/  (manual: huggingface-cli upload automatelab/mcp-servers-tool-catalog .)"
