"""Choose the data file that a command uses."""

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

from todo.errors import TodoError
from todo.gitrepo import exclude_locally, git_dir, git_toplevel
from todo.store import TMP_PREFIX, TMP_SUFFIX, Todo, mutate

REPO_LIST = Path(".todo") / "todos.jsonl"
PERSONAL_LIST = Path(".local") / "share" / "todo" / "todos.jsonl"
# The exclude pattern of the reserved temporary file name in .todo/.
FALLBACK_EXCLUDE = f"{REPO_LIST.parent}/{TMP_PREFIX}*{TMP_SUFFIX}"


class Resolved(NamedTuple):
    path: Path
    # Set only for a repository list. Later code places the lock and the
    # temporary files under it.
    repo_root: Path | None


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


def same_filesystem(a: Path, b: Path) -> bool:
    return os.stat(a).st_dev == os.stat(b).st_dev


def mutate_resolved[T](
    resolved: Resolved,
    change: Callable[[list[Todo]], tuple[list[Todo], T]],
    create: bool = False,
) -> T:
    """Run mutate on a resolved data file with the lock and temporary file placement.

    A repository list keeps its lock and temporary file in the worktree's Git
    metadata directory, so that they stay out of Git status. If that directory
    is on another filesystem, the temporary file goes in .todo/ and is
    excluded through info/exclude.
    """
    if resolved.repo_root is None:
        return mutate(resolved.path, change, create=create)

    meta = git_dir(resolved.repo_root)
    list_dir = resolved.path.parent
    # same_filesystem stats the directory, so it must exist first.
    list_dir.mkdir(parents=True, exist_ok=True)
    if same_filesystem(meta, list_dir):
        tmp_dir, tmp_prefix = meta, "todo-"
    else:
        exclude_locally(resolved.repo_root, FALLBACK_EXCLUDE)
        tmp_dir, tmp_prefix = list_dir, TMP_PREFIX
    return mutate(
        resolved.path,
        change,
        lock_path=meta / "todo.lock",
        tmp_dir=tmp_dir,
        tmp_prefix=tmp_prefix,
        new_mode=None,
        create=create,
    )


def init_repo_list(cwd: Path | None = None) -> Path:
    """Create or validate the repository list of cwd and return its path.

    It ignores TODO_FILE. A missing list is created empty under the repository
    lock. An existing list is validated and left unchanged.
    """
    cwd = Path.cwd() if cwd is None else Path(cwd)
    root = git_toplevel(cwd)
    if root is None:
        raise TodoError("todo --init needs a Git working tree")
    path = root / REPO_LIST
    mutate_resolved(Resolved(path, root), lambda records: (records, None), create=True)
    return path
