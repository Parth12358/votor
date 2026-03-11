import os
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich import box

console = Console()

PROJECT_ROOT = Path(".")


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------

def read_file(path: str) -> dict:
    """
    Read a file and return its contents.
    Returns dict with content, lines, exists.
    """
    p = Path(path)
    if not p.exists():
        return {"content": "", "lines": 0, "exists": False, "error": f"File not found: {path}"}

    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
        return {
            "content": content,
            "lines":   len(content.splitlines()),
            "exists":  True,
            "path":    str(p)
        }
    except Exception as e:
        return {"content": "", "lines": 0, "exists": False, "error": str(e)}


def create_file(path: str, content: str, commit_msg: Optional[str] = None) -> dict:
    """
    Create a new file with given content.
    Optionally commits to git with votor: prefix.
    Returns dict with success, path, error.
    """
    p = Path(path)

    if p.exists():
        return {"success": False, "error": f"File already exists: {path}. Use edit_file to modify it."}

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

        msg = commit_msg or f"votor: created {path}"
        git_commit([path], msg)

        return {"success": True, "path": str(p), "lines": len(content.splitlines())}
    except Exception as e:
        return {"success": False, "error": str(e)}


def edit_file(path: str, old_str: str, new_str: str, commit_msg: Optional[str] = None) -> dict:
    """
    Replace old_str with new_str in file.
    old_str must appear exactly once.
    Commits to git with votor: prefix.
    Returns dict with success, diff_preview, error.
    """
    p = Path(path)

    if not p.exists():
        return {"success": False, "error": f"File not found: {path}"}

    content = p.read_text(encoding="utf-8", errors="ignore")

    count = content.count(old_str)
    if count == 0:
        return {"success": False, "error": f"String not found in {path}"}
    if count > 1:
        return {"success": False, "error": f"String appears {count} times in {path}. Make it more specific."}

    new_content = content.replace(old_str, new_str, 1)

    try:
        p.write_text(new_content, encoding="utf-8")

        msg = commit_msg or f"votor: edited {path}"
        git_commit([path], msg)

        diff = _make_diff_preview(old_str, new_str, path)
        return {"success": True, "path": str(p), "diff_preview": diff}
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_file(path: str, commit_msg: Optional[str] = None) -> dict:
    """
    Delete a file and commit the deletion.
    Returns dict with success, error.
    """
    p = Path(path)

    if not p.exists():
        return {"success": False, "error": f"File not found: {path}"}

    try:
        p.unlink()
        msg = commit_msg or f"votor: deleted {path}"
        git_commit([path], msg)
        return {"success": True, "path": str(p)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _make_diff_preview(old_str: str, new_str: str, path: str) -> str:
    """Generate a simple unified diff preview string."""
    old_lines = old_str.splitlines()
    new_lines = new_str.splitlines()

    lines = [f"--- {path}", f"+++ {path}"]
    for line in old_lines:
        lines.append(f"-{line}")
    for line in new_lines:
        lines.append(f"+{line}")

    return "\n".join(lines)


def show_diff(diff_str: str, title: str = "diff"):
    """Render a diff string with syntax highlighting."""
    console.print(Panel(
        Syntax(diff_str, "diff", theme="monokai", line_numbers=False),
        title=f"[dim]{title}[/dim]",
        border_style="#1e1e2e"
    ))


# ---------------------------------------------------------------------------
# Git tools
# ---------------------------------------------------------------------------

def _run_git(args: list[str]) -> tuple[bool, str]:
    """Run a git command. Returns (success, output)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT)
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except FileNotFoundError:
        return False, "git not found. Install git to use change tracking."
    except Exception as e:
        return False, str(e)


def git_commit(paths: list[str], message: str) -> dict:
    """
    Stage specific files and commit with votor: prefix.
    Ensures commit message always starts with 'votor:'.
    """
    if not message.startswith("votor:"):
        message = f"votor: {message}"

    # Stage files
    for path in paths:
        ok, err = _run_git(["add", path])
        if not ok:
            return {"success": False, "error": f"git add failed: {err}"}

    # Commit
    ok, out = _run_git(["commit", "-m", message])
    if not ok:
        # Nothing to commit is not an error
        if "nothing to commit" in out.lower():
            return {"success": True, "message": "nothing to commit"}
        return {"success": False, "error": out}

    return {"success": True, "message": message, "output": out}


def git_log(limit: int = 20) -> list[dict]:
    """
    Return list of votor commits from git log.
    Filters only commits with 'votor:' prefix.
    """
    ok, out = _run_git([
        "log",
        f"--max-count={limit * 3}",  # fetch extra to filter
        "--pretty=format:%H|%s|%ar",
        "--grep=votor:"
    ])

    if not ok or not out:
        return []

    results = []
    n = 1
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            results.append({
                "n":    n,
                "hash": parts[0][:7],
                "msg":  parts[1],
                "time": parts[2]
            })
            n += 1
            if n > limit:
                break

    return results


def git_undo() -> dict:
    """Revert the most recent votor commit."""
    commits = git_log(limit=1)
    if not commits:
        return {"success": False, "error": "No votor commits found to undo."}

    last = commits[0]
    ok, out = _run_git(["revert", "--no-edit", last["hash"]])
    if not ok:
        return {"success": False, "error": out}

    return {"success": True, "reverted": last["msg"], "output": out}


def git_revert_to(n: int) -> dict:
    """
    Revert to state before change #n.
    Reverts all votor commits from #1 up to and including #n.
    """
    commits = git_log(limit=n)
    if not commits:
        return {"success": False, "error": "No votor commits found."}

    if n > len(commits):
        return {"success": False, "error": f"Only {len(commits)} votor commits found, cannot revert to #{n}."}

    # Revert commits from most recent back to #n (reverse order)
    to_revert = commits[:n]  # commits 1..n
    reverted = []

    for commit in to_revert:
        ok, out = _run_git(["revert", "--no-edit", commit["hash"]])
        if not ok:
            return {"success": False, "error": f"Failed reverting {commit['hash']}: {out}", "reverted": reverted}
        reverted.append(commit["msg"])

    return {"success": True, "reverted": reverted}


def git_diff(n: int) -> dict:
    """
    Return the diff of votor commit #n.
    """
    commits = git_log(limit=n)
    if not commits or n > len(commits):
        return {"success": False, "error": f"Commit #{n} not found.", "diff": ""}

    commit_hash = commits[n - 1]["hash"]
    ok, diff = _run_git(["show", "--no-color", commit_hash])

    if not ok:
        return {"success": False, "error": diff, "diff": ""}

    return {"success": True, "hash": commit_hash, "diff": diff}


def git_push(remote: str = "origin", branch: str = "main") -> dict:
    """Push to remote if configured."""
    ok, out = _run_git(["push", remote, branch])
    if not ok:
        return {"success": False, "error": out}
    return {"success": True, "output": out}


def git_status() -> dict:
    """Return current git status summary."""
    ok, out = _run_git(["status", "--short"])
    if not ok:
        return {"success": False, "error": out, "clean": False}

    return {
        "success": True,
        "clean":   out == "",
        "changes": out
    }


# ---------------------------------------------------------------------------
# Tool dispatcher — called by LLM tool use
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the project.",
        "parameters": {
            "path": "string — relative path to the file"
        }
    },
    {
        "name": "create_file",
        "description": "Create a new file with given content. File must not already exist.",
        "parameters": {
            "path":    "string — relative path to create",
            "content": "string — full file content"
        }
    },
    {
        "name": "edit_file",
        "description": "Replace a specific string in a file. The old_str must appear exactly once.",
        "parameters": {
            "path":    "string — relative path to the file",
            "old_str": "string — exact text to replace",
            "new_str": "string — replacement text"
        }
    },
    {
        "name": "delete_file",
        "description": "Delete a file from the project.",
        "parameters": {
            "path": "string — relative path to the file"
        }
    },
]


def dispatch_tool(tool_name: str, params: dict) -> dict:
    """
    Execute a tool call from the LLM.
    Returns result dict with success and relevant fields.
    """
    if tool_name == "read_file":
        return read_file(params.get("path", ""))

    elif tool_name == "create_file":
        return create_file(
            path=params.get("path", ""),
            content=params.get("content", ""),
            commit_msg=params.get("commit_msg")
        )

    elif tool_name == "edit_file":
        return edit_file(
            path=params.get("path", ""),
            old_str=params.get("old_str", ""),
            new_str=params.get("new_str", ""),
            commit_msg=params.get("commit_msg")
        )

    elif tool_name == "delete_file":
        return delete_file(
            path=params.get("path", ""),
            commit_msg=params.get("commit_msg")
        )

    else:
        return {"success": False, "error": f"Unknown tool: {tool_name}"}