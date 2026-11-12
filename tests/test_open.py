import errno
import json
import os
import subprocess
from datetime import date

import pytest

from todo import ids, store
from todo.cli import main, parse_due
from todo.errors import TodoError
from todo.store import TIMESTAMP_RE, TMP_SUFFIX, load


@pytest.fixture
def data_file(tmp_path, monkeypatch):
    path = tmp_path / "todos.jsonl"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_FILE", str(path))
    return path


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def lock_file(data_file):
    return data_file.with_name(data_file.name + ".lock")


def open_todo(capsys, data_file, *argv):
    code, out, err = run(capsys, "--open", *argv)
    assert (code, err) == (0, "")
    [record] = [r for r in load(data_file) if r.id == out.rstrip("\n")]
    return record, out


def test_title_only_todo(capsys, data_file):
    record, out = open_todo(capsys, data_file, "Renew club membership")
    assert out == record.id + "\n"
    assert ids.ID_RE.fullmatch(record.id)
    assert record.title == "Renew club membership"
    assert record.priority == "normal"
    assert record.notes == []
    assert record.due_date is None
    assert record.status == "open"
    assert record.completed_at is None
    assert TIMESTAMP_RE.fullmatch(record.created_at)
    assert record.updated_at == record.created_at


def test_priority_due_and_note(capsys, data_file):
    record, _ = open_todo(
        capsys,
        data_file,
        "Pay rent",
        "--priority",
        "high",
        "--due",
        "2020-02-29",
        "--note",
        "  First line\nSecond — ünïcode  ",
    )
    assert record.priority == "high"
    assert record.due_date == "2020-02-29"
    assert len(record.notes) == 1
    assert record.notes[0].text == "  First line\nSecond — ünïcode  "
    assert record.notes[0].created_at == record.created_at


def test_due_today_stores_local_date(capsys, data_file):
    record, _ = open_todo(capsys, data_file, "Today", "--due", "today")
    assert record.due_date == date.today().isoformat()


def test_title_whitespace_is_stripped(capsys, data_file):
    record, _ = open_todo(capsys, data_file, "  Renew   club ünïcode \t ")
    assert record.title == "Renew   club ünïcode"


def test_second_todo_is_appended(capsys, data_file):
    first, _ = open_todo(capsys, data_file, "One")
    second, _ = open_todo(capsys, data_file, "Two")
    assert first.id != second.id
    assert {r.id for r in load(data_file)} == {first.id, second.id}


def test_id_collision_retries(capsys, data_file, monkeypatch):
    candidates = iter(["aaaa", "aaaa", "bbbb"])
    monkeypatch.setattr(ids, "_candidate", lambda: next(candidates))
    first, _ = open_todo(capsys, data_file, "One")
    second, _ = open_todo(capsys, data_file, "Two")
    assert (first.id, second.id) == ("aaaa", "bbbb")


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["", "--due", "today"], "title must not be blank"),
        (["   \t "], "title must not be blank"),
        (["One\nTwo"], "title must not contain a line break"),
        (["One\rTwo"], "title must not contain a line break"),
        (["Title", "--note", ""], "note must not be blank"),
        (["Title", "--note", " \n\t "], "note must not be blank"),
        (
            ["Title", "--due", "2026-02-30"],
            "invalid date '2026-02-30', use YYYY-MM-DD or today",
        ),
        (
            ["Title", "--due", "Today"],
            "invalid date 'Today', use YYYY-MM-DD or today",
        ),
    ],
)
def test_invalid_input_exits_2_and_leaves_file_unchanged(
    capsys, data_file, argv, message
):
    existing = json.dumps(
        {
            "id": "k7m2",
            "title": "Existing",
            "priority": "normal",
            "due_date": None,
            "status": "open",
            "notes": [],
            "created_at": "2026-11-12T14:00:00Z",
            "updated_at": "2026-11-12T14:00:00Z",
            "completed_at": None,
        },
        separators=(",", ":"),
    )
    data_file.write_text(existing + "\n")
    before = data_file.read_bytes()

    code, out, err = run(capsys, "--open", *argv)

    assert code == 2
    assert out == ""
    assert err == f"todo: error: {message}\n"
    assert data_file.read_bytes() == before
    assert not lock_file(data_file).exists()


def test_invalid_input_does_not_create_file(capsys, data_file):
    code, _, _ = run(capsys, "--open", "   ")
    assert code == 2
    assert not data_file.exists()
    assert not lock_file(data_file).exists()


def test_invalid_priority_exits_2(capsys, data_file):
    with pytest.raises(SystemExit) as exc:
        main(["--open", "Title", "--priority", "urgent"])
    assert exc.value.code == 2
    assert not data_file.exists()


TODAY = date(2026, 11, 12)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("today", "2026-11-12"),
        ("2026-11-20", "2026-11-20"),
        ("2024-02-29", "2024-02-29"),
        ("1999-01-01", "1999-01-01"),
    ],
)
def test_parse_due_accepts(text, expected):
    assert parse_due(text, TODAY) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "TODAY",
        " today",
        "tomorrow",
        "2026-02-30",
        "2025-02-29",
        "2026-13-01",
        "2026-1-01",
        "20261112",
        "2026-11-12T00:00",
        "2026-W46-4",
        " 2026-11-12",
        "2026-11-12 ",
        "２０２６-11-12",
    ],
)
def test_parse_due_rejects(text):
    with pytest.raises(TodoError) as exc:
        parse_due(text, TODAY)
    assert exc.value.exit_code == 2
    assert str(exc.value) == f"invalid date '{text}', use YYYY-MM-DD or today"


def test_storage_failure_exits_1_with_message(capsys, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_FILE", str(blocker / "todos.jsonl"))

    code, out, err = run(capsys, "--open", "Title")

    assert code == 1
    assert out == ""
    assert err.startswith("todo: error: ")
    assert str(blocker) in err
    assert "Traceback" not in err


def test_repository_list_keeps_git_status_clean(capsys, tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout

    root = tmp_path / "repo"
    (root / ".todo").mkdir(parents=True)
    (root / ".todo" / "todos.jsonl").touch()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("TODO_FILE", raising=False)
    monkeypatch.chdir(root)
    git("init", "-q")
    git("add", "-A")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    git("config", "commit.gpgsign", "false")
    git("commit", "-q", "-m", "list")

    code, out, err = run(capsys, "--open", "Shared")

    assert (code, err) == (0, "")
    [record] = load(root / ".todo" / "todos.jsonl")
    assert out == record.id + "\n"
    status = git("status", "--porcelain", "--untracked-files=all")
    assert status.splitlines() == [" M .todo/todos.jsonl"]
    assert sorted(p.name for p in (root / ".todo").iterdir()) == ["todos.jsonl"]


def test_failed_replace_names_the_data_file(capsys, data_file, monkeypatch):
    open_todo(capsys, data_file, "First")
    original = data_file.read_bytes()

    def fail(src, dst):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), src, dst)

    monkeypatch.setattr(store.os, "replace", fail)

    code, out, err = run(capsys, "--open", "Second")

    assert (code, out) == (1, "")
    assert err == f"todo: error: cannot write {data_file}: Permission denied\n"
    assert TMP_SUFFIX not in err
    assert data_file.read_bytes() == original
