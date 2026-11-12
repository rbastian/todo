"""The todo record, its JSONL serialization and the validated load."""

import json
import os
import re
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime

from todo.errors import TodoError

PRIORITIES = ("high", "normal", "low")
STATUSES = ("open", "done")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")

ID_RE = re.compile(r"[0-9a-z]{4}")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


@dataclass
class Note:
    created_at: str
    text: str


@dataclass
class Todo:
    id: str
    title: str
    priority: str
    due_date: str | None
    status: str
    notes: list[Note]
    created_at: str
    updated_at: str
    completed_at: str | None


TODO_KEYS = [f.name for f in fields(Todo)]
NOTE_KEYS = [f.name for f in fields(Note)]


def utc_now() -> str:
    """Return the current UTC time in whole seconds, as stored in records."""
    return datetime.now(UTC).strftime(TIMESTAMP_FORMAT)


def dumps(records: list[Todo]) -> str:
    """Serialize records sorted by ID, one JSON line each."""
    return "".join(
        json.dumps(asdict(todo), ensure_ascii=False, separators=(",", ":")) + "\n"
        for todo in sorted(records, key=lambda todo: todo.id)
    )


def load(path: str | os.PathLike) -> list[Todo]:
    """Read and validate a data file. A missing file is an empty list."""
    try:
        with open(path, "rb") as file:
            data = file.read()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise TodoError(f"{path}: cannot read: {error.strerror}") from error

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        number = data.count(b"\n", 0, error.start) + 1
        raise TodoError(f"{path}:{number}: invalid UTF-8") from error

    records = []
    first_line = {}
    # Split on "\n" only: str.splitlines also splits on characters such as
    # U+2028 that json.dumps(ensure_ascii=False) leaves unescaped in strings.
    for number, raw in enumerate(text.split("\n"), start=1):
        if not raw.strip():
            continue
        try:
            todo = _parse_line(raw)
        except ValueError as error:
            raise TodoError(f"{path}:{number}: {error}") from error
        if todo.id in first_line:
            raise TodoError(
                f"{path}:{number}: duplicate id {todo.id}"
                f" (first on line {first_line[todo.id]})"
            )
        first_line[todo.id] = number
        records.append(todo)
    return records


def _parse_line(raw: str) -> Todo:
    if raw.startswith(CONFLICT_MARKERS):
        raise ValueError("Git conflict marker")
    try:
        obj = json.loads(raw, object_pairs_hook=_unique_keys)
    except json.JSONDecodeError as error:
        raise ValueError(f"malformed JSON: {error.msg}") from error
    except _DuplicateKey as error:
        raise ValueError(f"invalid record: duplicate key {error}") from error
    try:
        return _to_todo(obj)
    except ValueError as error:
        raise ValueError(f"invalid record: {error}") from error


class _DuplicateKey(Exception):
    pass


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise _DuplicateKey(repr(key))
        obj[key] = value
    return obj


def _check_keys(obj: object, keys: list[str], what: str) -> dict:
    if not isinstance(obj, dict):
        raise ValueError(f"{what} is not a JSON object")
    missing = [key for key in keys if key not in obj]
    if missing:
        raise ValueError(f"{what} is missing key {missing[0]!r}")
    unknown = [key for key in obj if key not in keys]
    if unknown:
        raise ValueError(f"{what} has unknown key {unknown[0]!r}")
    return obj


def _check_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value


def _check_timestamp(value: object, name: str) -> str:
    if isinstance(value, str) and TIMESTAMP_RE.fullmatch(value):
        try:
            datetime.strptime(value, TIMESTAMP_FORMAT)
            return value
        except ValueError:
            pass
    raise ValueError(f"{name} must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")


def _to_todo(obj: object) -> Todo:
    obj = _check_keys(obj, TODO_KEYS, "record")

    if not isinstance(obj["id"], str) or not ID_RE.fullmatch(obj["id"]):
        raise ValueError("id must be four characters from 0-9a-z")

    title = _check_text(obj["title"], "title")
    if "\n" in title or "\r" in title:
        raise ValueError("title must not contain a line break")

    if obj["priority"] not in PRIORITIES:
        raise ValueError("priority must be high, normal or low")

    due_date = obj["due_date"]
    if due_date is not None:
        try:
            if not (isinstance(due_date, str) and DATE_RE.fullmatch(due_date)):
                raise ValueError
            date.fromisoformat(due_date)
        except ValueError:
            raise ValueError("due_date must be null or a date YYYY-MM-DD") from None

    if obj["status"] not in STATUSES:
        raise ValueError("status must be open or done")

    if not isinstance(obj["notes"], list):
        raise ValueError("notes must be a list")
    notes = []
    for note in obj["notes"]:
        note = _check_keys(note, NOTE_KEYS, "note")
        notes.append(
            Note(
                created_at=_check_timestamp(note["created_at"], "note created_at"),
                text=_check_text(note["text"], "note text"),
            )
        )

    _check_timestamp(obj["created_at"], "created_at")
    _check_timestamp(obj["updated_at"], "updated_at")
    if obj["status"] == "open":
        if obj["completed_at"] is not None:
            raise ValueError("completed_at must be null when status is open")
    else:
        _check_timestamp(obj["completed_at"], "completed_at")

    return Todo(**{**obj, "notes": notes})
