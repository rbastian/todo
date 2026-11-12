import os
import stat
import subprocess
from pathlib import Path

import pytest

from todo import datafile, gitrepo, store
from todo.datafile import Resolved, mutate_resolved, resolve_data_file
from todo.errors import TodoError
from todo.store import Note, Todo, load

EXCLUDE_LINE = ".todo/.todos.*.tmp"


def todo(id, title="A todo"):
    return Todo(
        id=id,
        title=title,
        priority="normal",
        due_date=None,
        status="open",
        notes=[Note(created_at="2026-11-12T14:05:00Z", text="note")],
        created_at="2026-11-12T14:00:00Z",
        updated_at="2026-11-12T14:00:00Z",
        completed_at=None,
    )


def add(new):
    def change(records):
        return [*records, new], new.id

    return change


def retitle(id, title):
    def change(records):
        return [todo(id, title) if r.id == id else r for r in records], None

    return change


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "config", "commit.gpgsign", "false")
    return path.resolve()


def add_repo_list(root: Path) -> Path:
    data = root / ".todo" / "todos.jsonl"
    data.parent.mkdir()
    data.touch()
    return data


def commit_all(root: Path) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "list")


def porcelain(root: Path) -> list[str]:
    return git(root, "status", "--porcelain", "--untracked-files=all").splitlines()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("TODO_FILE", raising=False)
    return home


@pytest.fixture
def repo(tmp_path):
    root = git_init(tmp_path / "repo")
    add_repo_list(root)
    commit_all(root)
    return root


@pytest.fixture
def cross_filesystem(monkeypatch):
    monkeypatch.setattr(datafile, "same_filesystem", lambda a, b: False)


def exclude_file(root: Path) -> Path:
    return gitrepo.git_path(root, "info/exclude")


def test_git_dir_is_absolute_metadata_directory(repo):
    assert gitrepo.git_dir(repo) == repo / ".git"
    assert gitrepo.git_dir(repo / ".todo") == repo / ".git"


def test_git_dir_outside_repository_is_an_error(tmp_path):
    with pytest.raises(TodoError):
        gitrepo.git_dir(tmp_path)


def test_mutations_leave_only_the_data_file_in_git_status(repo):
    resolved = resolve_data_file(cwd=repo, env={})

    mutate_resolved(resolved, add(todo("aaaa")))
    commit_all(repo)
    mutate_resolved(resolved, retitle("aaaa", "Changed"))

    assert porcelain(repo) == [" M .todo/todos.jsonl"]
    assert (repo / ".git" / "todo.lock").exists()
    assert sorted(os.listdir(repo / ".todo")) == ["todos.jsonl"]
    assert not [n for n in os.listdir(repo / ".git") if n.endswith(".tmp")]
    assert load(resolved.path)[0].title == "Changed"


def test_temporary_file_is_created_in_git_dir(repo, monkeypatch):
    seen = []
    monkeypatch.setattr(store, "_before_replace", seen.append)
    mutate_resolved(resolve_data_file(cwd=repo, env={}), add(todo("aaaa")))

    (tmp_name,) = seen
    assert Path(tmp_name).parent == repo / ".git"
    assert Path(tmp_name).name.startswith("todo-")
    assert tmp_name.endswith(".tmp")


def test_cross_filesystem_fallback_writes_exclude_once(
    repo, cross_filesystem, monkeypatch
):
    seen = []

    def record(tmp_name):
        seen.append(tmp_name)
        # The temporary file exists here and must already be ignored.
        assert not [line for line in porcelain(repo) if line.endswith(".tmp")]

    monkeypatch.setattr(store, "_before_replace", record)
    resolved = resolve_data_file(cwd=repo, env={})
    mutate_resolved(resolved, add(todo("aaaa")))
    mutate_resolved(resolved, add(todo("bbbb")))

    assert len(seen) == 2
    for tmp_name in seen:
        assert Path(tmp_name).parent == repo / ".todo"
        assert Path(tmp_name).name.startswith(".todos.")
    lines = exclude_file(repo).read_text().splitlines()
    assert lines.count(EXCLUDE_LINE) == 1
    assert (repo / ".git" / "todo.lock").exists()
    assert porcelain(repo) == [" M .todo/todos.jsonl"]


def test_fallback_creates_missing_exclude_file(repo, cross_filesystem):
    exclude = exclude_file(repo)
    if exclude.exists():
        exclude.unlink()

    mutate_resolved(resolve_data_file(cwd=repo, env={}), add(todo("aaaa")))

    assert exclude.read_text() == EXCLUDE_LINE + "\n"


def test_fallback_appends_after_existing_lines_without_newline(repo, cross_filesystem):
    exclude = exclude_file(repo)
    exclude.write_text("*.log")

    mutate_resolved(resolve_data_file(cwd=repo, env={}), add(todo("aaaa")))

    assert exclude.read_text() == f"*.log\n{EXCLUDE_LINE}\n"


def test_same_filesystem_does_not_touch_exclude(repo):
    exclude = exclude_file(repo)
    before = exclude.read_text() if exclude.exists() else None

    mutate_resolved(resolve_data_file(cwd=repo, env={}), add(todo("aaaa")))

    assert (exclude.read_text() if exclude.exists() else None) == before


def test_new_repository_list_gets_umask_mode(tmp_path):
    root = git_init(tmp_path / "repo")
    (root / ".todo").mkdir()
    resolved = Resolved(root / ".todo" / "todos.jsonl", root)

    old = os.umask(0o022)
    try:
        mutate_resolved(resolved, add(todo("aaaa")))
    finally:
        os.umask(old)

    assert stat.S_IMODE(os.stat(resolved.path).st_mode) == 0o644


def test_personal_list_keeps_private_defaults(tmp_path):
    path = tmp_path / "personal" / "todos.jsonl"

    mutate_resolved(Resolved(path, None), add(todo("aaaa")))

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert (tmp_path / "personal" / "todos.jsonl.lock").exists()


def test_linked_worktree_uses_its_own_git_dir(repo, tmp_path):
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", str(linked))
    linked = linked.resolve()
    resolved = resolve_data_file(cwd=linked, env={})
    assert resolved.repo_root == linked
    linked_git_dir = gitrepo.git_dir(linked)
    assert linked_git_dir != repo / ".git"

    mutate_resolved(resolved, add(todo("aaaa")))

    assert (linked_git_dir / "todo.lock").exists()
    assert not (repo / ".git" / "todo.lock").exists()
    assert porcelain(linked) == [" M .todo/todos.jsonl"]
    assert porcelain(repo) == []


def test_linked_worktree_fallback_stays_out_of_status(repo, tmp_path, cross_filesystem):
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", str(linked))
    resolved = resolve_data_file(cwd=linked, env={})

    mutate_resolved(resolved, add(todo("aaaa")))

    assert exclude_file(linked.resolve()).read_text().count(EXCLUDE_LINE) == 1
    assert porcelain(linked) == [" M .todo/todos.jsonl"]
