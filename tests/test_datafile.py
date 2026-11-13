import subprocess
from pathlib import Path

import pytest

from todo.datafile import Resolved, resolve_data_file


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def personal(home):
    return home / ".todo" / "todos.jsonl"


@pytest.fixture
def outside(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    return outside


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path.resolve()


def add_repo_list(root: Path) -> Path:
    data = root / ".todo" / "todos.jsonl"
    data.parent.mkdir()
    data.touch()
    return data


def test_todo_file_overrides_repository_and_personal(tmp_path, home):
    root = git_init(tmp_path / "repo")
    add_repo_list(root)
    target = tmp_path / "explicit.jsonl"
    result = resolve_data_file(cwd=root, env={"TODO_FILE": str(target)})
    assert result == Resolved(path=target, repo_root=None)


def test_relative_todo_file_resolves_against_cwd(tmp_path, home):
    result = resolve_data_file(cwd=tmp_path, env={"TODO_FILE": "sub/t.jsonl"})
    assert result == Resolved(path=tmp_path / "sub" / "t.jsonl", repo_root=None)


def test_todo_file_is_not_created(tmp_path, home):
    target = tmp_path / "explicit.jsonl"
    resolve_data_file(cwd=tmp_path, env={"TODO_FILE": str(target)})
    assert not target.exists()


def test_empty_todo_file_counts_as_unset(home, personal, outside):
    result = resolve_data_file(cwd=outside, env={"TODO_FILE": ""})
    assert result == Resolved(path=personal, repo_root=None)


def test_repository_list_from_root(tmp_path, home):
    root = git_init(tmp_path / "repo")
    data = add_repo_list(root)
    result = resolve_data_file(cwd=root, env={})
    assert result == Resolved(path=data, repo_root=root)


def test_repository_list_from_subdirectory(tmp_path, home):
    root = git_init(tmp_path / "repo")
    data = add_repo_list(root)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    result = resolve_data_file(cwd=sub, env={})
    assert result == Resolved(path=data, repo_root=root)


def test_repository_without_list_uses_personal(tmp_path, home, personal):
    root = git_init(tmp_path / "repo")
    result = resolve_data_file(cwd=root, env={})
    assert result == Resolved(path=personal, repo_root=None)


def test_outside_repository_uses_personal(home, personal, outside):
    result = resolve_data_file(cwd=outside, env={})
    assert result == Resolved(path=personal, repo_root=None)


def test_inside_git_dir_uses_personal(tmp_path, home, personal):
    root = git_init(tmp_path / "repo")
    add_repo_list(root)
    result = resolve_data_file(cwd=root / ".git", env={})
    assert result == Resolved(path=personal, repo_root=None)


def test_git_missing_uses_personal(tmp_path, home, personal, monkeypatch):
    root = git_init(tmp_path / "repo")
    add_repo_list(root)

    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    result = resolve_data_file(cwd=root, env={})
    assert result == Resolved(path=personal, repo_root=None)


def test_home_as_repository_root_resolves_as_repository_list(home):
    root = git_init(home)
    data = add_repo_list(root)
    result = resolve_data_file(cwd=root, env={})
    assert result == Resolved(path=data, repo_root=root)


def test_personal_list_is_not_created(home, personal, outside):
    resolve_data_file(cwd=outside, env={})
    assert not personal.exists()
    assert not personal.parent.exists()


def test_defaults_use_process_cwd_and_environment(tmp_path, home, monkeypatch):
    root = git_init(tmp_path / "repo")
    data = add_repo_list(root)
    monkeypatch.chdir(root)
    monkeypatch.delenv("TODO_FILE", raising=False)
    assert resolve_data_file() == Resolved(path=data, repo_root=root)
    monkeypatch.setenv("TODO_FILE", "x.jsonl")
    assert resolve_data_file().path == root / "x.jsonl"
