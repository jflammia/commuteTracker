#!/usr/bin/env bash
# One-time repo setup: hooks, linear history, rebase strategy.
# Run after cloning: bash .githooks/setup.sh

set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "Setting up git hooks and config..."

# Use repo hooks directory
git config core.hooksPath .githooks

# Linear history: rebase on pull, fast-forward only merges
git config pull.rebase true
git config merge.ff only

# Auto-stash dirty working tree during rebase (no manual stash/pop)
git config rebase.autoStash true

echo "Done. Git is configured for this repo:"
echo "  core.hooksPath = .githooks"
echo "  pull.rebase    = true"
echo "  merge.ff       = only"
echo "  rebase.autoStash = true"
