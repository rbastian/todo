"""End-to-end Git acceptance checks: the CLI runs in a subprocess."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_LIST = Path(".todo") / "todos.jsonl"
PERSONAL_LIST = Path(".local") / "share" / "todo" / "todos.jsonl"


@pytest.fixture
def env(tmp_path):
    env = dict(os.environ)
    env.pop("TODO_FILE", None)
    for name in list(env):
        if name.startswith("GIT_"):
            del env[name]
    env.update(
        HOME=str(tmp_path / "home"),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        # Stop Git discovery at tmp_path, so that "outside" is outside any repository.
        GIT_CEILING_DIRECTORIES=str(tmp_path),
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
    git(env, path, "config", "user.name", "Test")
    git(env, path, "config", "user.email", "test@example.com")
    return path.resolve()


def opened(env, cwd, title):
    result = todo(env, cwd, "--open", title)
    assert (result.returncode, result.stderr) == (0, "")
    return result.stdout.strip()


def titles(path):
    return [json.loads(line)["title"] for line in path.read_text().splitlines()]


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


def test_check_2_repository_personal_and_todo_file(tmp_path, env):
    root = make_repo(env, tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    personal = tmp_path / "home" / PERSONAL_LIST
    assert todo(env, root, "--init").returncode == 0

    opened(env, root, "In the repository")
    opened(env, outside, "Outside")

    assert titles(root / REPO_LIST) == ["In the repository"]
    assert titles(personal) == ["Outside"]
    assert "In the repository" in todo(env, root, "--list").stdout
    assert "Outside" not in todo(env, root, "--list").stdout
    assert "Outside" in todo(env, outside, "--list").stdout
    assert "In the repository" not in todo(env, outside, "--list").stdout

    override = tmp_path / "override" / "todos.jsonl"
    env["TODO_FILE"] = str(override)
    opened(env, root, "Override from the repository")
    opened(env, outside, "Override from outside")

    assert sorted(titles(override)) == [
        "Override from outside",
        "Override from the repository",
    ]
    assert titles(root / REPO_LIST) == ["In the repository"]
    assert titles(personal) == ["Outside"]
    listed = todo(env, root, "--list").stdout
    assert "Override from outside" in listed
    assert "In the repository" not in listed


def test_check_3_one_changed_line_and_clean_status(tmp_path, env):
    root = make_repo(env, tmp_path / "repo")
    assert todo(env, root, "--init").returncode == 0
    ids = [opened(env, root, title) for title in ("First", "Second", "Third")]
    git(env, root, "add", str(REPO_LIST))
    git(env, root, "commit", "-q", "-m", "Add todos")

    target = ids[1]
    result = todo(env, root, "--update", target, "--title", "Second, edited")

    assert (result.returncode, result.stderr) == (0, "")
    assert git(env, root, "diff", "--numstat") == f"1\t1\t{REPO_LIST}\n"
    changed = [
        line
        for line in git(env, root, "diff", "--unified=0").splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    assert [line[0] for line in changed] == ["-", "+"]
    assert all(json.loads(line[1:])["id"] == target for line in changed)
    assert git(env, root, "status", "--porcelain", "--untracked-files=all") == (
        f" M {REPO_LIST}\n"
    )


@pytest.mark.parametrize(
    "command", [["--list"], ["--update", "{id}", "--title", "New"]]
)
def test_check_4_conflict_markers(tmp_path, env, command):
    root = make_repo(env, tmp_path / "repo")
    assert todo(env, root, "--init").returncode == 0
    ids = [opened(env, root, title) for title in ("First", "Second")]
    data = root / REPO_LIST
    lines = data.read_text().splitlines(keepends=True)
    text = lines[0] + "<<<<<<< HEAD\n" + lines[1] + "=======\n" + lines[1]
    text += ">>>>>>> other\n"
    data.write_text(text)
    before = data.read_bytes()

    result = todo(env, root, *(arg.format(id=ids[0]) for arg in command))

    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == f"todo: error: {data}:2: Git conflict marker\n"
    assert data.read_bytes() == before


@pytest.mark.parametrize(
    "command", [["--list"], ["--update", "{id}", "--title", "New"]]
)
def test_check_4_duplicate_ids(tmp_path, env, command):
    root = make_repo(env, tmp_path / "repo")
    assert todo(env, root, "--init").returncode == 0
    ids = [opened(env, root, title) for title in ("First", "Second")]
    data = root / REPO_LIST
    lines = data.read_text().splitlines(keepends=True)
    data.write_text(lines[0] + lines[1] + lines[0])
    before = data.read_bytes()
    dup = json.loads(lines[0])["id"]

    result = todo(env, root, *(arg.format(id=ids[1]) for arg in command))

    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == (
        f"todo: error: {data}:3: duplicate id {dup} (first on line 1)\n"
    )
    assert data.read_bytes() == before
