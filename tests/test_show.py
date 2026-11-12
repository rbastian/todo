import json

import pytest

from todo.cli import main


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


def record(**overrides):
    base = {
        "id": "k7m2",
        "title": "Renew family membership",
        "priority": "high",
        "due_date": "2026-11-20",
        "status": "open",
        "notes": [],
        "created_at": "2026-11-12T14:00:00Z",
        "updated_at": "2026-11-12T14:05:00Z",
        "completed_at": None,
    }
    return {**base, **overrides}


def write(data_file, *records):
    data_file.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )


def test_open_todo_with_multiline_notes(capsys, data_file):
    notes = [
        {"created_at": "2026-11-12T14:05:00Z", "text": "Check family pricing first"},
        {"created_at": "2026-11-12T14:06:00Z", "text": "Line one\n  line two\nüñî"},
    ]
    write(data_file, record(notes=notes), record(id="a1b2", title="Other"))
    before = data_file.read_bytes()

    code, out, err = run(capsys, "--show", "k7m2")

    assert (code, err) == (0, "")
    assert out == (
        "id:        k7m2\n"
        "title:     Renew family membership\n"
        "status:    open\n"
        "priority:  high\n"
        "due:       2026-11-20\n"
        "created:   2026-11-12T14:00:00Z\n"
        "updated:   2026-11-12T14:05:00Z\n"
        "completed: -\n"
        "notes:\n"
        "  2026-11-12T14:05:00Z\n"
        "    Check family pricing first\n"
        "  2026-11-12T14:06:00Z\n"
        "    Line one\n"
        "      line two\n"
        "    üñî\n"
    )
    assert data_file.read_bytes() == before
    assert not data_file.with_name(data_file.name + ".lock").exists()


def test_done_todo_without_notes_or_due(capsys, data_file):
    write(
        data_file,
        record(
            status="done",
            priority="low",
            due_date=None,
            completed_at="2026-11-13T09:00:00Z",
        ),
    )

    code, out, err = run(capsys, "--show", " K7M2 ")

    assert (code, err) == (0, "")
    assert out == (
        "id:        k7m2\n"
        "title:     Renew family membership\n"
        "status:    done\n"
        "priority:  low\n"
        "due:       -\n"
        "created:   2026-11-12T14:00:00Z\n"
        "updated:   2026-11-12T14:05:00Z\n"
        "completed: 2026-11-13T09:00:00Z\n"
        "notes:     -\n"
    )


def test_unknown_id_exits_1(capsys, data_file):
    write(data_file, record())
    code, out, err = run(capsys, "--show", "zzzz")
    assert code == 1
    assert out == ""
    assert err.startswith("todo: error: ") and "zzzz" in err


def test_missing_file_is_unknown_id(capsys, data_file):
    code, out, err = run(capsys, "--show", "k7m2")
    assert (code, out) == (1, "")
    assert "k7m2" in err
    assert not data_file.exists()


def test_malformed_id_exits_2(capsys, data_file):
    write(data_file, record())
    code, out, err = run(capsys, "--show", "k7m")
    assert (code, out) == (2, "")
    assert "invalid ID" in err


def test_malformed_file_exits_1(capsys, data_file):
    data_file.write_text(json.dumps(record()) + "\n{not json\n", encoding="utf-8")
    before = data_file.read_bytes()
    code, out, err = run(capsys, "--show", "k7m2")
    assert (code, out) == (1, "")
    assert ":2:" in err
    assert data_file.read_bytes() == before
