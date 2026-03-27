---
name: fix-issue
description: >
  Fix a GitHub issue end-to-end: investigate, fix, test, commit, close. Use
  this skill whenever the user asks to fix an issue, address a bug report,
  mentions a GitHub issue number (#N), says "check for issues", "fix the
  issue", "look at the bug", or wants to work through open issues. Also use
  when the user pastes a GitHub issue URL or references a specific problem
  reported by a user. This skill ensures every fix includes regression tests
  before closing.
---

# Fixing GitHub Issues

This is the established process for fixing issues in this repo. Every fix
must end with regression tests that prevent the issue from recurring. No
exceptions — a fix without a test is incomplete.

## The Process

```
1. Read the issue
2. Reproduce / confirm the problem
3. Investigate root cause
4. Implement the fix
5. Write regression tests  ← required, not optional
6. Format, lint, and test
7. Commit with "Fixes #N"
8. Push and verify issue closes + CI green
```

## Step 1: Read the Issue

```bash
gh issue list --state open
gh issue view <number>
```

Read the full issue. Understand:
- What the user expected to happen
- What actually happened
- Any reproduction steps or error messages
- The environment (Docker, reverse proxy, OS, etc.)

## Step 2: Reproduce / Confirm

Before writing any code, confirm the problem exists. This could mean:
- Running the reproduction commands from the issue
- Writing a failing test that demonstrates the bug
- Reading the code path and confirming the flaw logically

If you can't reproduce it, ask for more information before proceeding.

## Step 3: Investigate Root Cause

Don't just patch the symptom. Trace the problem to its origin:
- Read the relevant source code
- Check the SDK/library source if the issue is in a dependency's behavior
- Look at git blame to understand when and why the current code was written
- Consider whether the fix might break other things

Document your understanding of the root cause — it goes in the commit body.

## Step 4: Implement the Fix

Keep fixes minimal and targeted:
- Fix the root cause, not the symptom
- Don't refactor surrounding code in the same change
- If the fix requires a config change, make it backward-compatible
- If the fix involves a dependency's behavior, add a code comment explaining
  the constraint (so future developers don't unknowingly revert it)

## Step 5: Write Regression Tests

This is the most important step. The fix is not complete without tests that
would catch this exact problem if it were reintroduced.

Good regression tests:
- **Test the specific failure mode** from the issue, not just the happy path
- **Include the issue URL** in the test docstring for traceability
- **Test at the right level** — unit test if it's a logic bug, integration test
  if it's a wiring/config issue
- **Have descriptive names** that explain what they guard against

Add tests to `tests/test_audit_fixes.py` (the regression test file for this
repo). Follow the existing patterns there — fixtures, helpers, and section
comments.

Example pattern:
```python
def test_mcp_host_not_localhost():
    """MCP host must not be localhost to avoid DNS rebinding protection.

    When host is 127.0.0.1/localhost, the MCP SDK rejects requests from
    reverse proxies with 421 Invalid Host header.
    See: https://github.com/jflammia/commuteTracker/issues/3
    """
    from src.mcp_server import mcp

    assert mcp.settings.host not in ("127.0.0.1", "localhost", "::1")
```

Think about what could reintroduce this bug:
- Someone reverts the fix
- A dependency updates and changes defaults
- A refactor moves code and loses a critical detail
- A new feature adds a code path that bypasses the fix

Write tests that catch each of those scenarios.

## Step 6: Format, Lint, and Test

Run these in order — format first, then check, then test:

```bash
ruff format src/ tests/               # Auto-fix formatting FIRST
ruff check src/ tests/                # Then check lint
python -m pytest --tb=short           # Then run tests
```

Format first because the pre-commit hook (`.githooks/pre-commit`) and
Claude Code hook (`.claude/settings.json`) will block your commit if
formatting is off. Running `ruff format` proactively avoids this.

If existing tests break, that means the fix has side effects — address
them before committing.

## Step 7: Commit with "Fixes #N"

Use the `commit-pr` skill for the message format. The commit must include
`Fixes #N` in the body — GitHub auto-closes the issue when this lands on
main.

Structure the commit as two atomic changes if it makes sense:
1. The fix itself: `fix: <description>` with `Fixes #N`
2. The regression tests: `test: add regression tests for <description> (#N)`

Or combine them if the fix is small:
```
fix: MCP server 421 error behind reverse proxy

The MCP SDK auto-enables DNS rebinding protection when host is localhost,
rejecting all proxy Host headers. Set host=RECEIVER_HOST (0.0.0.0) to
skip auto-protection.

Fixes #3
```

## Step 8: Push and Verify

```bash
git pull        # Rebase on remote (release-please or other bot commits)
git push
```

If push is rejected, `git pull` again and retry. The repo is configured with
`pull.rebase=true` and `rebase.autoStash=true` so this handles divergence
from bot commits automatically — no manual stash/pop.

Then verify:
- `gh issue view <number>` — should show CLOSED
- `gh run list --limit 2` — CI should be green

If CI fails, that is a real problem. Do not move on — diagnose and fix it.
Do not use workarounds like manually triggering workflows.

After pushing `fix:` commits, release-please will open or update a Release
PR. You don't need to merge it immediately — it accumulates changes until
the user is ready to release.

## Checklist

Before calling an issue done, verify every item:

- [ ] Root cause understood and documented in the commit body
- [ ] Fix is minimal and targeted
- [ ] Regression tests added to `tests/test_audit_fixes.py`
- [ ] Tests include the issue URL in docstrings
- [ ] `ruff format src/ tests/` run (auto-fix formatting)
- [ ] Full test suite passes (`python -m pytest`)
- [ ] Lint passes (`ruff check src/ tests/`)
- [ ] Commit message includes `Fixes #N`
- [ ] Issue is closed on GitHub
- [ ] CI is green
