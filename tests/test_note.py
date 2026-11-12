import json
import subprocess
import sys

import pytest

from todo.cli import main
from todo.store import TIMESTAMP_RE, load


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


def write_record(data_file, **fields):
    record = {
        "id": "k7m2",
        "title": "Existing",
        "priority": "normal",
        "due_date": None,
        "status": "open",
        "notes": [],
        "created_at": "2026-11-12T14:00:00Z",
        "updated_at": "2026-11-12T14:00:00Z",
        "completed_at": None,
        **fields,
    }
    data_file.write_text(json.dumps(record, separators=(",", ":")) + "\n")


def test_note_is_appended_and_id_printed(capsys, data_file, monkeypatch):
    write_record(data_file)
    monkeypatch.setattr("todo.cli.utc_now", lambda: "2026-11-12T15:00:00Z")

    code, out, err = run(capsys, "--note", "k7m2", "Check family pricing first")

    assert (code, out, err) == (0, "k7m2\n", "")
    [record] = load(data_file)
    assert [(n.created_at, n.text) for n in record.notes] == [
        ("2026-11-12T15:00:00Z", "Check family pricing first")
    ]
    assert record.updated_at == "2026-11-12T15:00:00Z"
    assert record.created_at == "2026-11-12T14:00:00Z"
    assert record.status == "open"
    assert record.completed_at is None


def test_two_notes_keep_order_and_content_across_processes(data_file):
    def cli(*argv):
        return subprocess.run(
            [sys.executable, "-m", "todo", *argv],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    todo_id = cli("--open", "Renew club membership").rstrip("\n")
    first = "  First line\nSecond — ünïcode  "
    second = "Check family pricing first"
    assert cli("--note", todo_id, first) == todo_id + "\n"
    assert cli("--note", todo_id, second) == todo_id + "\n"

    [record] = load(data_file)
    assert [n.text for n in record.notes] == [first, second]
    assert all(TIMESTAMP_RE.fullmatch(n.created_at) for n in record.notes)
    assert record.notes[0].created_at <= record.notes[1].created_at
    assert record.updated_at == record.notes[1].created_at


def test_note_on_done_todo_keeps_status(capsys, data_file):
    write_record(data_file, status="done", completed_at="2026-11-12T14:30:00Z")

    code, out, err = run(capsys, "--note", "k7m2", "Afterthought")

    assert (code, out, err) == (0, "k7m2\n", "")
    [record] = load(data_file)
    assert [n.text for n in record.notes] == ["Afterthought"]
    assert record.status == "done"
    assert record.completed_at == "2026-11-12T14:30:00Z"


def test_id_is_normalized(capsys, data_file):
    write_record(data_file)

    code, out, err = run(capsys, "--note", " K7M2 ", "Text")

    assert (code, out, err) == (0, "k7m2\n", "")
    assert [n.text for n in load(data_file)[0].notes] == ["Text"]


@pytest.mark.parametrize(
    ("argv", "code", "message"),
    [
        (["k7m2", ""], 2, "note must not be blank"),
        (["k7m2", " \n\t "], 2, "note must not be blank"),
        (["k7m", "Text"], 2, "invalid ID 'k7m'"),
        (["k7m2!", "Text"], 2, "invalid ID 'k7m2!'"),
        (["zzzz", "Text"], 1, "no todo with ID 'zzzz'"),
    ],
)
def test_invalid_input_leaves_file_unchanged(capsys, data_file, argv, code, message):
    write_record(data_file)
    before = data_file.read_bytes()

    result = run(capsys, "--note", *argv)

    assert result == (code, "", f"todo: error: {message}\n")
    assert data_file.read_bytes() == before


@pytest.mark.parametrize("argv", [["k7m2", " "], ["k7m", "Text"]])
def test_invalid_input_does_not_take_the_lock(capsys, data_file, argv):
    write_record(data_file)

    code, _, _ = run(capsys, "--note", *argv)

    assert code == 2
    assert not data_file.with_name(data_file.name + ".lock").exists()


def test_unknown_id_on_missing_file_exits_1(capsys, data_file):
    code, out, err = run(capsys, "--note", "zzzz", "Text")

    assert (code, out) == (1, "")
    assert err == "todo: error: no todo with ID 'zzzz'\n"
    assert not data_file.exists()
