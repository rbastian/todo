"""End-to-end Git acceptance checks: the CLI runs in a subprocess."""

import json
import os
import subprocess
import sys

import pytest

from todo.datafile import PERSONAL_LIST, REPO_LIST
from todo.store import load


@pytest.fixture
def env(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "TODO_FILE" and not key.startswith("GIT_")
    }
    env.update(
        HOME=str(tmp_path / "home"),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        # Stop Git discovery at tmp_path, so that "outside" is outside any repository.
        GIT_CEILING_DIRECTORIES=str(tmp_path),
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.com",
    )
    return env


def todo(env, cwd, *args):
    return subprocess.run(
        [sys.executable, "-m", "todo", *args],
        check=False,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def git(env, cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


def make_repo(env, path):
    path.mkdir(parents=True)
    git(env, path, "init", "-q")
    return path.resolve()


@pytest.fixture
def repo(tmp_path, env):
    """A Git repository with an initialized repository list."""
    root = make_repo(env, tmp_path / "repo")
    assert todo(env, root, "--init").returncode == 0
    return root


def opened(env, cwd, title):
    result = todo(env, cwd, "--open", title)
    assert (result.returncode, result.stderr) == (0, "")
    return result.stdout.strip()


def titles(path):
    return sorted(record.title for record in load(path))


def test_check_1_init_from_subdirectory(tmp_path, env):
    root = make_repo(env, tmp_path / "repo")
    sub = root / "a" / "b"
    sub.mkdir(parents=True)

    result = todo(env, sub, "--init")

    assert (result.returncode, result.stderr) == (0, "")
    assert (root / REPO_LIST).is_file()
    assert git(env, root, "diff", "--cached", "--name-only") == ""
    opened(env, sub, "Survives init")
    assert todo(env, sub, "--init").returncode == 0
    assert titles(root / REPO_LIST) == ["Survives init"]


def test_check_2_repository_personal_and_todo_file(tmp_path, env, repo):
    outside = tmp_path / "outside"
    outside.mkdir()
    personal = tmp_path / "home" / PERSONAL_LIST

    opened(env, repo, "In the repository")
    opened(env, outside, "Outside")

    assert titles(repo / REPO_LIST) == ["In the repository"]
    assert titles(personal) == ["Outside"]
    in_repo = todo(env, repo, "--list").stdout
    assert "In the repository" in in_repo
    assert "Outside" not in in_repo
    out_of_repo = todo(env, outside, "--list").stdout
    assert "Outside" in out_of_repo
    assert "In the repository" not in out_of_repo

    override = tmp_path / "override" / "todos.jsonl"
    env["TODO_FILE"] = str(override)
    opened(env, repo, "Override from the repository")
    opened(env, outside, "Override from outside")

    assert titles(override) == [
        "Override from outside",
        "Override from the repository",
    ]
    assert titles(repo / REPO_LIST) == ["In the repository"]
    assert titles(personal) == ["Outside"]
    listed = todo(env, repo, "--list").stdout
    assert "Override from outside" in listed
    assert "In the repository" not in listed


def test_check_3_one_changed_line_and_clean_status(env, repo):
    _, target, _ = [opened(env, repo, title) for title in ("First", "Second", "Third")]
    git(env, repo, "add", str(REPO_LIST))
    git(env, repo, "commit", "-q", "-m", "Add todos")

    result = todo(env, repo, "--update", target, "--title", "Second, edited")

    assert (result.returncode, result.stderr) == (0, "")
    assert git(env, repo, "diff", "--numstat") == f"1\t1\t{REPO_LIST}\n"
    changed = [
        line[1:]
        for line in git(env, repo, "diff", "--unified=0").splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    assert [json.loads(line)["id"] for line in changed] == [target, target]
    assert git(env, repo, "status", "--porcelain", "--untracked-files=all") == (
        f" M {REPO_LIST}\n"
    )


def with_conflict(a, b):
    text = f"{a}<<<<<<< HEAD\n{b}=======\n{b}>>>>>>> other\n"
    return text, "{data}:2: Git conflict marker"


def with_duplicate(a, b):
    return a + b + a, "{data}:3: duplicate id {dup} (first on line 1)"


@pytest.mark.parametrize("corrupt", [with_conflict, with_duplicate])
@pytest.mark.parametrize(
    "command", [["--list"], ["--update", "{id}", "--title", "New"]]
)
def test_check_4_reports_lines_and_leaves_file(env, repo, corrupt, command):
    ids = [opened(env, repo, title) for title in ("First", "Second")]
    data = repo / REPO_LIST
    a, b = data.read_text().splitlines(keepends=True)
    text, expected = corrupt(a, b)
    data.write_text(text)

    result = todo(env, repo, *(arg.format(id=ids[0]) for arg in command))

    assert (result.returncode, result.stdout) == (1, "")
    message = expected.format(data=data, dup=json.loads(a)["id"])
    assert result.stderr == f"todo: error: {message}\n"
    assert data.read_bytes() == text.encode()
