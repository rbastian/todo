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


def open_todo(capsys, *argv):
    code, out, err = run(capsys, "--open", *argv)
    assert (code, err) == (0, "")
    return out.rstrip("\n")


def record(data_file, todo_id):
    [todo] = [r for r in load(data_file) if r.id == todo_id]
    return todo


def test_done_completes_open_todo(capsys, data_file, monkeypatch):
    todo_id = open_todo(capsys, "Pay rent", "--note", "Before the 1st")
    before = record(data_file, todo_id)
    monkeypatch.setattr("todo.cli.utc_now", lambda: "2030-01-02T03:04:05Z")

    code, out, err = run(capsys, "--done", todo_id)

    assert (code, out, err) == (0, todo_id + "\n", "")
    after = record(data_file, todo_id)
    assert after.status == "done"
    assert after.completed_at == "2030-01-02T03:04:05Z"
    assert after.updated_at == after.completed_at
    assert TIMESTAMP_RE.fullmatch(after.completed_at)
    assert after.notes == before.notes
    assert after.title == before.title
    assert after.created_at == before.created_at


def test_done_keeps_other_todos(capsys, data_file):
    first = open_todo(capsys, "First")
    second = open_todo(capsys, "Second")

    assert run(capsys, "--done", first)[0] == 0

    assert record(data_file, first).status == "done"
    assert record(data_file, second).status == "open"
    assert len(load(data_file)) == 2


def test_repeated_done_preserves_timestamps(capsys, data_file, monkeypatch):
    todo_id = open_todo(capsys, "Pay rent")
    run(capsys, "--done", todo_id)
    first = record(data_file, todo_id)
    content = data_file.read_bytes()
    monkeypatch.setattr("todo.cli.utc_now", lambda: "2099-12-31T23:59:59Z")

    code, out, err = run(capsys, "--done", todo_id)

    assert (code, out, err) == (0, todo_id + "\n", "")
    assert record(data_file, todo_id) == first
    assert data_file.read_bytes() == content


def test_done_normalizes_id(capsys, data_file):
    todo_id = open_todo(capsys, "Pay rent")

    code, out, err = run(capsys, "--done", f"  {todo_id.upper()} ")

    assert (code, out, err) == (0, todo_id + "\n", "")
    assert record(data_file, todo_id).status == "done"


def test_unknown_id_exits_1(capsys, data_file):
    open_todo(capsys, "Pay rent")
    content = data_file.read_bytes()
    unknown = "zzzz" if load(data_file)[0].id != "zzzz" else "yyyy"

    code, out, err = run(capsys, "--done", unknown)

    assert (code, out) == (1, "")
    assert err == f"todo: error: no todo with ID '{unknown}'\n"
    assert data_file.read_bytes() == content


def test_unknown_id_on_missing_file_exits_1(capsys, data_file):
    code, out, err = run(capsys, "--done", "abcd")

    assert (code, out) == (1, "")
    assert "no todo with ID 'abcd'" in err
    assert not data_file.exists()


@pytest.mark.parametrize("bad", ["abc", "abcde", "ab-d", ""])
def test_malformed_id_exits_2(capsys, data_file, bad):
    code, out, err = run(capsys, "--done", bad)

    assert (code, out) == (2, "")
    assert f"invalid ID '{bad}'" in err
    assert not data_file.exists()
