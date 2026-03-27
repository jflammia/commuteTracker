#!/usr/bin/env bash
# Log worktree removal for operational visibility
set -euo pipefail

INPUT=$(cat)
WORKTREE_PATH=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('worktreePath',''))" 2>/dev/null)
BRANCH=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('branchName',''))" 2>/dev/null)

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Worktree removed: ${WORKTREE_PATH:-unknown} (branch: ${BRANCH:-unknown})" >> /tmp/claude-worktree-audit.log 2>/dev/null
exit 0
