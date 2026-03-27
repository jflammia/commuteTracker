---
name: commit-pr
description: >
  Write commit messages and PR descriptions for this repo using conventional
  commits. Use this skill whenever the user asks to commit, create a PR, write
  a commit message, push changes, or says things like "commit this", "make a
  PR", "push", "/commit", or "prepare a PR". Also use when the user has just
  finished a chunk of work and wants to ship it. This skill ensures every
  commit and PR follows the release-please conventions so automated releases
  work correctly.
---

# Commit & PR Messages for commuteTracker

This repo uses [release-please](https://github.com/googleapis/release-please)
for automated releases. Every commit message on `main` is parsed to determine
version bumps and changelog entries. Getting the format right matters — a
malformed commit means a missed changelog entry or wrong version bump.

## Conventional Commit Format

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

### Types and their release-please effect

| Type | Changelog section | Version bump | When to use |
|------|------------------|--------------|-------------|
| `feat` | Features | patch (pre-1.0) | New user-facing functionality |
| `fix` | Bug Fixes | patch | Bug fix |
| `docs` | Documentation | none | Docs only |
| `style` | Styles | none | Formatting, whitespace |
| `refactor` | Code Refactoring | none | Neither fixes a bug nor adds a feature |
| `perf` | Performance | none | Performance improvement |
| `test` | Tests | none | Adding/fixing tests |
| `build` | Build System | none | Build tooling, dependencies |
| `ci` | CI | none | CI/CD workflow changes |
| `chore` | Miscellaneous | none | Anything else (deps, config) |
| `revert` | Reverts | patch | Revert a previous commit |

**Breaking changes:** Add `!` after the type (e.g. `feat!:`) to trigger a
minor bump (pre-1.0) or major bump (post-1.0). Only use for actual breaking
changes to the API, config, or data format.

### Scope (optional)

Use a scope when the change is clearly scoped to one area:
- `feat(api):`, `fix(pipeline):`, `ci(docker):`, `docs(mcp):`
- Omit the scope for cross-cutting changes

## Writing the Commit Message

### Step 1: Understand the changes

Read the diff and recent log before writing anything:

```bash
git diff --staged --stat    # What files changed
git diff --staged           # The actual changes
git log --oneline -5        # Recent commit style
```

If nothing is staged, check unstaged changes with `git diff` and `git status`.

### Step 2: Pick the type

Look at what actually changed, not what the user said. A PR titled "refactor
auth" that also fixes a crash is a `fix`, not a `refactor` — the type should
reflect the most significant change for users.

### Step 3: Write the subject line

- **Under 70 characters** (the type prefix counts)
- **Imperative mood**: "add", "fix", "update" (not "added", "fixes", "updates")
- **Focus on why/what changed**, not how: "fix pipeline crash on null timestamps"
  is better than "add null check to pipeline.py line 45"
- **Lowercase** after the colon (no capital letter)
- **No period** at the end

### Step 4: Write the body (for non-trivial changes)

Separate from subject with a blank line. Explain:
- **Why** the change was made (motivation, bug report, user request)
- **What** changed at a high level (not a file-by-file list)
- Keep it concise — a few bullet points or a short paragraph

### Step 5: No AI attribution trailers

Do NOT add `Co-Authored-By`, `Signed-off-by`, or any other trailer that
attributes the commit to an AI. The repo owner is the author — the commit
should look like any other human-written commit. This applies to commits,
PRs, and any git metadata.

### Step 6: Use a HEREDOC for the commit

Always pass the message via HEREDOC to preserve formatting:
```bash
git commit -m "$(cat <<'EOF'
fix(pipeline): filter non-location records before enrichment

Pipeline crashed with 500 error when rebuilding without filters because
transition and card records have null timestamps. Now filters
msg_type=location at the DB query level.
EOF
)"
```

## Writing a PR

### Title

The PR title becomes the squash-merge commit message, so it follows the same
conventional commit format. Under 70 characters.

### Body

Use this structure:

```markdown
## Summary
- <1-3 concise bullet points describing what and why>

## Test plan
- [ ] <how to verify this works>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

### Creating the PR

```bash
gh pr create --title "<conventional commit title>" --body "$(cat <<'EOF'
## Summary
- <bullets>

## Test plan
- [ ] <verification steps>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

## Examples

**Simple fix:**
```
fix: pipeline crash when rebuilding without date filters
```

**Feature with scope and body:**
```
feat(api): add batch segments endpoint

Replaces N+1 per-commute API calls in dashboard pages 2 and 4 with a
single GET /api/v1/segments query. Reduces page load from ~20 HTTP
round-trips to 1.
```

**CI change:**
```
ci: add release-please for one-click automated releases
```

**Multi-fix commit with body:**
```
fix: MCP server, SQL injection, pipeline crashes, and dashboard perf

- Fix MCP server mounting (double path + missing lifespan init)
- Parameterize DuckDB queries to prevent SQL injection
- Filter non-location records before pipeline enrichment
- Add batch segments endpoint to fix N+1 in dashboard
```

## Common Mistakes to Avoid

- **"Update foo.py"** — describes the file, not the change
- **"Fix bug"** — too vague; say what bug
- **Starting with a capital letter** after the colon
- **Using past tense** ("fixed", "added") instead of imperative ("fix", "add")
- **Adding AI attribution trailers** (`Co-Authored-By: Claude ...`) — never do this
- **Using `feat` for non-user-facing changes** — internal refactors are `refactor`
- **Putting everything in one giant commit** — prefer logical, atomic commits
