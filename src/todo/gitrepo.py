"""The Git CLI calls that repository lists need."""

import subprocess
from pathlib import Path

from todo.errors import TodoError


def _rev_parse(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "rev-parse", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def git_toplevel(cwd: Path) -> Path | None:
    """Return the Git working-tree root of cwd, or None outside a working tree."""
    try:
        result = _rev_parse(cwd, "--show-toplevel")
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.rstrip("\n"))


def _query(cwd: Path, *args: str) -> str:
    try:
        result = _rev_parse(cwd, *args)
    except FileNotFoundError as error:
        raise TodoError("git is not installed") from error
    if result.returncode != 0:
        raise TodoError(f"{cwd}: git rev-parse failed: {result.stderr.strip()}")
    return result.stdout.rstrip("\n")


def git_dir(cwd: Path) -> Path:
    """Return the absolute Git metadata directory of the worktree at cwd."""
    return Path(_query(cwd, "--absolute-git-dir"))


def git_path(cwd: Path, name: str) -> Path:
    """Return the absolute path of name inside the Git metadata of cwd."""
    return cwd / _query(cwd, "--git-path", name)


def exclude_locally(cwd: Path, pattern: str) -> None:
    """Add pattern to info/exclude unless that exact line is already there."""
    exclude = git_path(cwd, "info/exclude")
    try:
        text = exclude.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    if pattern in text.splitlines():
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, "a", encoding="utf-8") as file:
        if text and not text.endswith("\n"):
            file.write("\n")
        file.write(pattern + "\n")
