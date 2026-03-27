#!/usr/bin/env bash
# Symlink .venv from main worktree into new worktrees
set -euo pipefail

INPUT=$(cat)
WORKTREE_PATH=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('worktreePath',''))" 2>/dev/null)

if [[ -z "$WORKTREE_PATH" ]]; then exit 0; fi

MAIN_WORKTREE=$(git worktree list --porcelain | head -1 | sed 's/^worktree //')

if [[ -z "$MAIN_WORKTREE" ]] || [[ "$WORKTREE_PATH" == "$MAIN_WORKTREE" ]]; then exit 0; fi

# Symlink .venv (single venv, standard Python project layout)
if [[ -d "$MAIN_WORKTREE/.venv" ]] && [[ ! -e "$WORKTREE_PATH/.venv" ]]; then
    ln -s "$MAIN_WORKTREE/.venv" "$WORKTREE_PATH/.venv"
fi

exit 0
