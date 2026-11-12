import json
import re

import pytest

from todo import store
from todo.errors import TodoError
from todo.store import Note, Todo, dumps, load, utc_now

SPEC_LINE = (
    '{"id":"k7m2","title":"Renew family membership","priority":"high",'
    '"due_date":"2026-11-20","status":"open","notes":[{"created_at":'
    '"2026-11-12T14:05:00Z","text":"Check family pricing first"}],'
    '"created_at":"2026-11-12T14:00:00Z","updated_at":"2026-11-12T14:05:00Z",'
    '"completed_at":null}\n'
)

SPEC_RECORD = Todo(
    id="k7m2",
    title="Renew family membership",
    priority="high",
    due_date="2026-11-20",
    status="open",
    notes=[Note(created_at="2026-11-12T14:05:00Z", text="Check family pricing first")],
    created_at="2026-11-12T14:00:00Z",
    updated_at="2026-11-12T14:05:00Z",
    completed_at=None,
)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "todos.jsonl"))


def record(**fields):
    base = json.loads(SPEC_LINE)
    base.update(fields)
    return base


def line(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def load_error(path):
    before = path.read_bytes()
    with pytest.raises(TodoError) as exc:
        load(path)
    assert exc.value.exit_code == 1
    assert path.read_bytes() == before
    return str(exc.value)


def test_dumps_spec_record_matches_spec_line():
    assert dumps([SPEC_RECORD]) == SPEC_LINE


def test_load_spec_line(tmp_path):
    assert load(write(tmp_path / "t.jsonl", SPEC_LINE)) == [SPEC_RECORD]


def test_round_trip_preserves_unicode_and_multiline_notes(tmp_path):
    todo = Todo(
        id="a1z9",
        title="Café ☕ – naïve “quotes” \\ back\tslash   sep",
        priority="low",
        due_date=None,
        status="done",
        notes=[
            Note(created_at="2026-11-12T14:05:00Z", text="line one\nline two\r\n三"),
            Note(created_at="2026-11-12T14:06:00Z", text="😀 \x85 next"),
        ],
        created_at="2026-11-12T14:00:00Z",
        updated_at="2026-11-12T14:06:00Z",
        completed_at="2026-11-12T14:06:00Z",
    )
    text = dumps([todo, SPEC_RECORD])
    assert text.count("\n") == 2
    path = tmp_path / "t.jsonl"
    path.write_bytes(text.encode("utf-8"))
    assert load(path) == [todo, SPEC_RECORD]
    assert "☕" in text


def test_dumps_sorts_by_id_with_fixed_key_order():
    b = Todo(**{**vars(SPEC_RECORD), "id": "b000", "notes": []})
    a = Todo(**{**vars(SPEC_RECORD), "id": "0zzz", "notes": []})
    text = dumps([b, SPEC_RECORD, a])
    lines = text.split("\n")
    assert lines[-1] == ""
    assert [json.loads(x)["id"] for x in lines[:-1]] == ["0zzz", "b000", "k7m2"]
    for x in lines[:-1]:
        assert list(json.loads(x)) == [
            "id",
            "title",
            "priority",
            "due_date",
            "status",
            "notes",
            "created_at",
            "updated_at",
            "completed_at",
        ]


def test_dumps_empty_list():
    assert dumps([]) == ""


def test_missing_file_loads_as_empty(tmp_path):
    path = tmp_path / "absent" / "todos.jsonl"
    assert load(path) == []
    assert not path.exists()
    assert not path.parent.exists()


def test_blank_lines_skipped_and_final_newline_optional(tmp_path):
    other = line(record(id="zz99", notes=[]))
    path = write(tmp_path / "t.jsonl", "\n   \n" + SPEC_LINE + " \t\n" + other.rstrip("\n"))
    assert [t.id for t in load(path)] == ["k7m2", "zz99"]


def test_empty_file_loads_as_empty(tmp_path):
    assert load(write(tmp_path / "t.jsonl", "")) == []


def test_malformed_json_names_line(tmp_path):
    path = write(tmp_path / "t.jsonl", SPEC_LINE + "\n{not json\n")
    message = load_error(path)
    assert message.startswith(f"{path}:3: ")
    assert "malformed JSON" in message


def test_first_error_is_reported(tmp_path):
    path = write(tmp_path / "t.jsonl", "{bad\n[1]\n")
    assert load_error(path).startswith(f"{path}:1: ")


@pytest.mark.parametrize("marker", ["<<<<<<< HEAD", "=======", ">>>>>>> branch"])
def test_conflict_markers_are_reported(tmp_path, marker):
    path = write(tmp_path / "t.jsonl", SPEC_LINE + marker + "\n")
    message = load_error(path)
    assert message.startswith(f"{path}:2: ")
    assert "conflict marker" in message


def test_duplicate_id_names_both_lines(tmp_path):
    dup = line(record(title="Other"))
    path = write(tmp_path / "t.jsonl", SPEC_LINE + "\n" + dup)
    message = load_error(path)
    assert message.startswith(f"{path}:3: ")
    assert "duplicate id k7m2" in message
    assert "line 1" in message


def test_unknown_key_is_invalid(tmp_path):
    path = write(tmp_path / "t.jsonl", line(record(extra=1)))
    message = load_error(path)
    assert message.startswith(f"{path}:1: ")
    assert "invalid record" in message
    assert "extra" in message


def test_missing_key_is_invalid(tmp_path):
    obj = record()
    del obj["updated_at"]
    path = write(tmp_path / "t.jsonl", SPEC_LINE.replace("k7m2", "aaaa") + line(obj))
    message = load_error(path)
    assert message.startswith(f"{path}:2: ")
    assert "invalid record" in message
    assert "updated_at" in message


NOTE = {"created_at": "2026-11-12T14:05:00Z", "text": "x"}


@pytest.mark.parametrize(
    "obj",
    [
        [1, 2],
        "string",
        None,
        record(id="K7M2"),
        record(id="k7m"),
        record(id="k7m22"),
        record(id="k7-2"),
        record(id=1234),
        record(title=""),
        record(title="   "),
        record(title="a\nb"),
        record(title="a\rb"),
        record(title=None),
        record(priority="urgent"),
        record(priority=None),
        record(due_date="2026-02-30"),
        record(due_date="2026-1-05"),
        record(due_date="2026-11-20T00:00:00Z"),
        record(due_date="today"),
        record(due_date="２０２６-11-20"),
        record(due_date=20261120),
        record(status="closed"),
        record(notes=None),
        record(notes={}),
        record(notes=["text"]),
        record(notes=[{"text": "x"}]),
        record(notes=[{**NOTE, "extra": 1}]),
        record(notes=[{**NOTE, "text": "  \n "}]),
        record(notes=[{**NOTE, "text": 5}]),
        record(notes=[{**NOTE, "created_at": "2026-11-12 14:05:00"}]),
        record(created_at="2026-11-12T14:00:00"),
        record(created_at="2026-11-12T14:00:00.123Z"),
        record(created_at="2026-11-12T14:00:00+00:00"),
        record(created_at="2026-13-12T14:00:00Z"),
        record(created_at="2026-11-12T25:00:00Z"),
        record(updated_at=None),
        record(completed_at="2026-11-12T14:00:00Z"),
        record(status="done", completed_at=None),
        record(status="done", completed_at="yesterday"),
    ],
)
def test_invalid_records(tmp_path, obj):
    path = write(tmp_path / "t.jsonl", line(obj))
    message = load_error(path)
    assert message.startswith(f"{path}:1: ")
    assert "invalid record" in message


def test_duplicate_key_in_object_is_invalid(tmp_path):
    text = SPEC_LINE.replace('"priority":"high"', '"priority":"high","priority":"low"')
    message = load_error(write(tmp_path / "t.jsonl", text))
    assert "invalid record" in message
    assert "priority" in message


def test_done_record_loads(tmp_path):
    obj = record(status="done", completed_at="2026-11-13T09:00:00Z", due_date=None)
    (todo,) = load(write(tmp_path / "t.jsonl", line(obj)))
    assert todo.status == "done"
    assert todo.completed_at == "2026-11-13T09:00:00Z"
    assert todo.due_date is None


def test_invalid_utf8_is_storage_error(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(SPEC_LINE.encode() + b'{"id":"\xff\xfe"}\n')
    message = load_error(path)
    assert message.startswith(f"{path}:2: ")
    assert "UTF-8" in message


def test_unreadable_path_is_storage_error(tmp_path):
    path = tmp_path / "dir.jsonl"
    path.mkdir()
    with pytest.raises(TodoError) as exc:
        load(path)
    assert exc.value.exit_code == 1
    assert str(path) in str(exc.value)


def test_load_accepts_str_path(tmp_path):
    path = write(tmp_path / "t.jsonl", SPEC_LINE)
    assert load(str(path)) == [SPEC_RECORD]


def test_utc_now_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", utc_now())


def test_utc_now_is_patchable(monkeypatch):
    monkeypatch.setattr(store, "utc_now", lambda: "2000-01-01T00:00:00Z")
    assert store.utc_now() == "2000-01-01T00:00:00Z"
