#!/usr/bin/env bash
# One-time repo setup: hooks, rebase strategy, autostash.
# Run after cloning: bash .githooks/setup.sh

set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "Setting up git hooks and config..."

# Use repo hooks directory
git config core.hooksPath .githooks

# Always rebase on pull (no merge commits from bot pushes)
git config pull.rebase true

# Auto-stash dirty working tree during rebase (no manual stash/pop)
git config rebase.autoStash true

echo "Done. Git is configured for this repo:"
echo "  core.hooksPath = .githooks"
echo "  pull.rebase = true"
echo "  rebase.autoStash = true"
