import os
import subprocess
import sys
from pathlib import Path

import pytest

from todo.cli import TodoError, main

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "todos.jsonl"))


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def assert_usage_error(capsys, *argv):
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage:" in err
    assert "todo: error:" in err
    return err


def test_main_returns_int(capsys):
    code, _, _ = run(capsys, "--list")
    assert isinstance(code, int)


def test_todo_error_defaults_to_exit_1():
    assert TodoError("boom").exit_code == 1


@pytest.mark.parametrize(
    ("argv", "action"),
    [
        (["--note", "k7m2", "text"], "--note"),
        (["--done", "k7m2"], "--done"),
        (["--delete", "k7m2"], "--delete"),
    ],
)
def test_actions_dispatch_to_their_handler(capsys, argv, action):
    code, out, err = run(capsys, *argv)
    assert code == 1
    assert out == ""
    assert err == f"todo: error: {action} is not implemented yet\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["--list", "--today"],
        ["--open", "Title", "--list"],
        ["--done", "k7m2", "--delete", "k7m2"],
        ["--init", "--show", "k7m2"],
        ["--note", "k7m2", "text", "--list"],
        ["--note", "k7m2", "text", "--done", "k7m2"],
    ],
)
def test_two_actions_exit_2(capsys, argv):
    assert_usage_error(capsys, *argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["--list", "--priority", "high"],
        ["--priority", "high"],
        ["--today", "--due", "today"],
        ["--open", "Title", "--clear-due"],
        ["--open", "Title", "--title", "Other"],
        ["--open", "Title", "--all"],
        ["--show", "k7m2", "--all"],
        ["--done", "k7m2", "--priority", "low"],
        ["--init", "--title", "x"],
        ["--update", "k7m2", "--all"],
        ["--update", "k7m2", "--due", "today", "--clear-due"],
    ],
)
def test_modifier_with_wrong_action_exits_2(capsys, argv):
    assert_usage_error(capsys, *argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["--note", "k7m2"],
        ["--note", "k7m2", "a", "b"],
        ["--open", "Title", "--note", "a", "b"],
    ],
)
def test_note_value_count_exits_2(capsys, argv):
    assert_usage_error(capsys, *argv)


def test_invalid_priority_exits_2(capsys):
    assert_usage_error(capsys, "--open", "Title", "--priority", "urgent")


def test_help_shows_usage_and_examples(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out, _ = capsys.readouterr()
    assert out.startswith("usage: todo")
    for example in [
        "todo --init",
        'todo --open "Renew club membership"',
        "todo --today",
        "todo --update k7m2 --clear-due",
        'todo --note k7m2 "Check family pricing first"',
        "todo --list --all",
        "todo --delete k7m2",
    ]:
        assert example in out


def test_module_help_in_subprocess(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "TODO_FILE": str(tmp_path / "t")}
    result = subprocess.run(
        [sys.executable, "-m", "todo", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("usage: todo")
    assert "todo --done k7m2" in result.stdout
