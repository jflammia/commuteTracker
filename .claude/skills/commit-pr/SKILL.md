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

Only `feat` and `fix` appear in the changelog and trigger releases. Other
types are valid conventional commits (and required by `lint-pr.yml`) but are
invisible to release-please — they won't create a release or changelog entry.

| Type | In changelog? | Version bump | When to use |
|------|:------------:|:------------:|-------------|
| `feat` | yes — "Features" | patch (pre-1.0) | New user-facing functionality |
| `fix` | yes — "Bug Fixes" | patch | Bug fix |
| `revert` | yes — "Reverts" | patch | Revert a previous commit |
| `docs` | no | none | Docs only |
| `style` | no | none | Formatting, whitespace |
| `refactor` | no | none | Neither fixes a bug nor adds a feature |
| `perf` | no | none | Performance improvement |
| `test` | no | none | Adding/fixing tests |
| `build` | no | none | Build tooling, dependencies |
| `ci` | no | none | CI/CD workflow changes |
| `chore` | no | none | Anything else (deps, config) |

If all commits since the last release are non-feat/fix types, release-please
will **not** create a Release PR. Those commits accumulate silently and get
bundled into the next release that includes a `feat:` or `fix:`.

**Breaking changes:** Add `!` after the type (e.g. `feat!:`) or include a
`BREAKING CHANGE:` footer in the commit body. Either triggers a minor bump
(pre-1.0) or major bump (post-1.0). Only use for actual breaking changes to
the API, config, or data format.

### Scope (optional)

Use a scope when the change is clearly scoped to one area:
- `feat(api):`, `fix(pipeline):`, `ci(docker):`, `docs(mcp):`
- Omit the scope for cross-cutting changes

## How Commits Reach release-please

This repo uses **squash merge** for PRs. When a PR is merged, GitHub
combines all commits into one and uses the **PR title** as the commit
message. That squashed commit is what release-please parses.

This means:
- **For PRs**: the PR title is what matters. Individual commits within the
  PR can be messy — only the PR title needs to be a valid conventional
  commit. The `lint-pr.yml` workflow enforces this.
- **For direct pushes to main** (no PR): the commit message itself is
  parsed. Follow the same conventional commit format.

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

Feature/fix PRs are **squash merged** — the PR title becomes the commit
message on main. Release PRs from release-please are **regular merged**
(not squash). Never squash a Release PR.

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

**CI change (won't appear in changelog or trigger release):**
```
ci: add release-please for one-click automated releases
```

**Breaking change with footer:**
```
feat(api): redesign query endpoint response format

Wrap all query results in a {data: [...], meta: {...}} envelope.

BREAKING CHANGE: /api/v1/query now returns {data, meta} instead of a bare array.
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
