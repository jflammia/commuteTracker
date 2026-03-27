#!/usr/bin/env python3
"""PreToolUse Hook: Enforce Worktree Isolation

When a worktree session marker exists (.claude/worktree-session), this hook
blocks file operations that target the main repo. This prevents parallel
Claude sessions from stepping on each other.

Allows git operations needed for merging (fetch, pull, merge --ff-only, push).

Exit codes:
  0: Allow the operation
  2: Block the operation (stderr shown to Claude)
"""
import json
import re
import sys
from pathlib import Path


def find_project_root():
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            return parent
    return cwd


def load_session_marker(project_root: Path) -> dict | None:
    marker_path = project_root / ".claude" / "worktree-session"
    if not marker_path.exists():
        return None
    session = {}
    try:
        with open(marker_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    session[key.strip()] = value.strip()
    except Exception:
        return None
    return session if session else None


def normalize_path(path_str: str) -> Path:
    if not path_str:
        return Path.cwd()
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.resolve()
    except Exception:
        return path


def is_allowed_git_operation(command: str) -> bool:
    """Git operations needed for merge/push workflows."""
    allowed_patterns = [
        r"\bgit\s+fetch\b",
        r"\bgit\s+pull\b",
        r"\bgit\s+merge\s+--ff-only\b",
        r"\bgit\s+push\b",
        r"\bgit\s+worktree\s+remove\b",
        r"\bgit\s+worktree\s+list\b",
        r"\bgit\s+branch\s+-d\b",
        r"\bgit\s+branch\s+--show-current\b",
        r"\bgit\s+log\b",
        r"\bgit\s+status\b",
    ]
    return any(re.search(p, command) for p in allowed_patterns)


def extract_paths_from_bash(command: str) -> list[str]:
    if is_allowed_git_operation(command):
        return []
    paths = []
    for match in re.finditer(r"(?:^|[\s=])([/~][^\s;|&><\"'`]+)", command):
        path = match.group(1)
        if path in ["/dev/null", "/dev/stderr", "/dev/stdout", "/dev/stdin"]:
            continue
        paths.append(path)
    for match in re.finditer(r"cd\s+([^\s;|&><]+)", command):
        paths.append(match.group(1))
    return paths


def is_path_in_main_repo(path: Path, main_worktree: Path, current_worktree: Path) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_main = main_worktree.resolve()
        resolved_current = current_worktree.resolve()
        try:
            resolved_path.relative_to(resolved_current)
            return False
        except ValueError:
            pass
        try:
            resolved_path.relative_to(resolved_main)
            return True
        except ValueError:
            pass
        return False
    except Exception:
        return False


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name not in ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]:
        sys.exit(0)

    project_root = find_project_root()
    session = load_session_marker(project_root)
    if not session:
        sys.exit(0)

    worktree_path = session.get("WORKTREE_PATH")
    main_worktree = session.get("MAIN_WORKTREE")
    if not worktree_path or not main_worktree:
        sys.exit(0)

    worktree_path = Path(worktree_path)
    main_worktree = Path(main_worktree)

    paths_to_check = []
    if tool_name == "Bash":
        paths_to_check = extract_paths_from_bash(tool_input.get("command", ""))
    elif tool_name in ["Read", "Write", "Edit"]:
        file_path = tool_input.get("file_path", "")
        if file_path:
            paths_to_check = [file_path]
    elif tool_name in ["Glob", "Grep"]:
        path = tool_input.get("path", "")
        if path:
            paths_to_check = [path]

    violations = []
    for path_str in paths_to_check:
        path = normalize_path(path_str)
        if is_path_in_main_repo(path, main_worktree, worktree_path):
            violations.append(str(path))

    if violations:
        msg = f"""
WORKTREE ISOLATION VIOLATION

You are in a WORKTREE session but trying to access the MAIN REPO.

Current worktree: {worktree_path}
Main repo:        {main_worktree}

Blocked path(s):
"""
        for v in violations:
            msg += f"  - {v}\n"
        msg += f"""
Stay within your worktree: {worktree_path}
If you need to merge to main, use git operations (fetch, merge --ff-only, push).
"""
        print(msg, file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
