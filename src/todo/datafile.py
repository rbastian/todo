"""Choose the data file that a command uses."""

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

REPO_LIST = Path(".todo") / "todos.jsonl"
PERSONAL_LIST = Path(".local") / "share" / "todo" / "todos.jsonl"


class Resolved(NamedTuple):
    path: Path
    # Set only for a repository list. Later code places the lock and the
    # temporary files under it.
    repo_root: Path | None


def git_toplevel(cwd: Path) -> Path | None:
    """Return the Git working-tree root of cwd, or None outside a working tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.rstrip("\n"))


def resolve_data_file(
    cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> Resolved:
    """Resolve TODO_FILE, then the repository list, then the personal list."""
    cwd = Path.cwd() if cwd is None else Path(cwd)
    env = os.environ if env is None else env

    if todo_file := env.get("TODO_FILE"):
        return Resolved(cwd / todo_file, None)

    root = git_toplevel(cwd)
    if root is not None and (root / REPO_LIST).exists():
        return Resolved(root / REPO_LIST, root)

    return Resolved(Path.home() / PERSONAL_LIST, None)
