import subprocess
from pathlib import Path

import pytest

from todo import gitrepo
from todo.cli import main
from todo.datafile import REPO_LIST, mutate_resolved, resolve_data_file
from todo.store import Note, Todo, dumps, load

INVALID = '{"id": "k7m2"\n'


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "elsewhere" / "todos.jsonl"))


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    return path.resolve()


def init(capsys, monkeypatch, cwd):
    monkeypatch.chdir(cwd)
    code = main(["--init"])
    out, err = capsys.readouterr()
    return code, out, err


def a_todo():
    return Todo(
        id="k7m2",
        title="Renew club membership",
        priority="normal",
        due_date=None,
        status="open",
        notes=[Note(created_at="2026-11-12T14:05:00Z", text="note")],
        created_at="2026-11-12T14:00:00Z",
        updated_at="2026-11-12T14:00:00Z",
        completed_at=None,
    )


def test_init_from_subdirectory_creates_unstaged_list(tmp_path, capsys, monkeypatch):
    root = git_init(tmp_path / "repo")
    sub = root / "a" / "b"
    sub.mkdir(parents=True)

    code, out, err = init(capsys, monkeypatch, sub)

    data = root / REPO_LIST
    assert (code, out, err) == (0, f"{data}\n", "")
    assert data.read_bytes() == b""
    assert not (sub / ".todo").exists()
    assert git(root, "diff", "--cached", "--name-only") == ""
    assert git(root, "status", "--porcelain", "--untracked-files=all") == (
        "?? .todo/todos.jsonl\n"
    )


def test_init_preserves_existing_todos(tmp_path, capsys, monkeypatch):
    root = git_init(tmp_path / "repo")
    assert init(capsys, monkeypatch, root)[0] == 0

    monkeypatch.delenv("TODO_FILE")
    resolved = resolve_data_file(root)
    assert resolved.path == root / REPO_LIST
    mutate_resolved(resolved, lambda records: ([*records, a_todo()], None))
    before = (root / REPO_LIST).read_bytes()

    code, out, err = init(capsys, monkeypatch, root)

    assert (code, out, err) == (0, f"{root / REPO_LIST}\n", "")
    assert (root / REPO_LIST).read_bytes() == before
    assert load(root / REPO_LIST) == [a_todo()]


def test_init_ignores_todo_file(tmp_path, capsys, monkeypatch):
    root = git_init(tmp_path / "repo")
    todo_file = tmp_path / "elsewhere" / "todos.jsonl"
    monkeypatch.setenv("TODO_FILE", str(todo_file))

    code, out, _ = init(capsys, monkeypatch, root)

    assert (code, out) == (0, f"{root / REPO_LIST}\n")
    assert (root / REPO_LIST).exists()
    assert not todo_file.exists()
    assert not todo_file.parent.exists()


def test_init_on_invalid_file_exits_1_and_leaves_it(tmp_path, capsys, monkeypatch):
    root = git_init(tmp_path / "repo")
    data = root / REPO_LIST
    data.parent.mkdir()
    data.write_text(INVALID)

    code, out, err = init(capsys, monkeypatch, root)

    assert code == 1
    assert out == ""
    assert err.startswith(f"todo: error: {data}:1: ")
    assert data.read_text() == INVALID


def test_init_on_valid_file_does_not_rewrite_it(tmp_path, capsys, monkeypatch):
    root = git_init(tmp_path / "repo")
    data = root / REPO_LIST
    data.parent.mkdir()
    # Valid but not in the canonical serialization: --init must not normalize it.
    text = dumps([a_todo()]).replace(",", ", ")
    data.write_text(text)
    mtime = data.stat().st_mtime_ns

    assert init(capsys, monkeypatch, root)[0] == 0
    assert data.read_text() == text
    assert data.stat().st_mtime_ns == mtime


def test_init_outside_a_repository_exits_1(tmp_path, capsys, monkeypatch):
    outside = tmp_path / "plain"
    outside.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    code, out, err = init(capsys, monkeypatch, outside)

    assert (code, out) == (1, "")
    assert err == "todo: error: todo --init needs a Git working tree\n"
    assert not (outside / ".todo").exists()


def test_init_without_git_exits_1(tmp_path, capsys, monkeypatch):
    root = git_init(tmp_path / "repo")
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))

    code, out, err = init(capsys, monkeypatch, root)

    assert (code, out) == (1, "")
    assert err == "todo: error: todo --init needs a Git working tree\n"
    assert not (root / ".todo").exists()


def test_init_keeps_lock_and_temp_files_out_of_git_status(
    tmp_path, capsys, monkeypatch
):
    root = git_init(tmp_path / "repo")

    assert init(capsys, monkeypatch, root)[0] == 0

    assert (gitrepo.git_dir(root) / "todo.lock").exists()
    assert sorted(p.name for p in (root / ".todo").iterdir()) == ["todos.jsonl"]
    assert git(root, "status", "--porcelain", "--untracked-files=all") == (
        "?? .todo/todos.jsonl\n"
    )
