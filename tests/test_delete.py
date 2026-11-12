import pytest

from todo import ids
from todo.cli import main
from todo.store import load


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


def open_todo(capsys, *argv):
    code, out, err = run(capsys, "--open", *argv)
    assert (code, err) == (0, "")
    return out.rstrip("\n")


def test_delete_removes_todo_and_notes(capsys, data_file):
    keep = open_todo(capsys, "Keep me")
    gone = open_todo(capsys, "Delete me", "--note", "A note")

    assert run(capsys, "--delete", gone) == (0, gone + "\n", "")

    assert [record.id for record in load(data_file)] == [keep]
    assert "Delete me" not in data_file.read_text()
    assert "A note" not in data_file.read_text()


def test_show_of_deleted_id_exits_1(capsys, data_file):
    todo_id = open_todo(capsys, "Delete me")
    assert run(capsys, "--delete", todo_id)[0] == 0

    code, out, err = run(capsys, "--show", todo_id)
    assert code == 1
    assert out == ""
    assert err.startswith("todo: error:")


def test_delete_done_todo(capsys, data_file):
    todo_id = open_todo(capsys, "Done already")
    text = data_file.read_text().replace('"status":"open"', '"status":"done"')
    text = text.replace(
        '"completed_at":null', '"completed_at":"2026-11-12T14:00:00Z"'
    )
    data_file.write_text(text)
    assert load(data_file)[0].status == "done"

    assert run(capsys, "--delete", todo_id) == (0, todo_id + "\n", "")
    assert load(data_file) == []


def test_delete_accepts_uppercase_id(capsys, data_file):
    todo_id = open_todo(capsys, "Delete me")
    assert run(capsys, "--delete", f" {todo_id.upper()} ") == (0, todo_id + "\n", "")
    assert load(data_file) == []


def test_unknown_id_exits_1_and_file_unchanged(capsys, data_file):
    todo_id = open_todo(capsys, "Keep me")
    unknown = "zzzz" if todo_id != "zzzz" else "yyyy"
    before = data_file.read_bytes()

    code, out, err = run(capsys, "--delete", unknown)

    assert code == 1
    assert out == ""
    assert unknown in err
    assert err.startswith("todo: error:")
    assert data_file.read_bytes() == before


def test_unknown_id_on_missing_file_exits_1(capsys, data_file):
    code, out, err = run(capsys, "--delete", "k7m2")
    assert (code, out) == (1, "")
    assert err.startswith("todo: error:")


@pytest.mark.parametrize("bad", ["k7m", "k7m2x", "k7-2", ""])
def test_malformed_id_exits_2_and_file_unchanged(capsys, data_file, bad):
    open_todo(capsys, "Keep me")
    before = data_file.read_bytes()

    code, out, err = run(capsys, "--delete", bad)

    assert (code, out) == (2, "")
    assert err.startswith("todo: error:")
    assert data_file.read_bytes() == before


def test_deleted_id_is_free_for_reuse(capsys, data_file, monkeypatch):
    monkeypatch.setattr(ids, "_candidate", lambda: "k7m2")
    assert open_todo(capsys, "First") == "k7m2"
    assert run(capsys, "--delete", "k7m2")[0] == 0
    assert open_todo(capsys, "Second") == "k7m2"
    assert [record.title for record in load(data_file)] == ["Second"]
