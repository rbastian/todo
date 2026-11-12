"""The todo record, its JSONL serialization and the validated load."""

import contextlib
import fcntl
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime

from todo.errors import TodoError
from todo.ids import ID_RE

PRIORITIES = ("high", "normal", "low")
STATUSES = ("open", "done")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")

DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

TMP_PREFIX = ".todos."
TMP_SUFFIX = ".tmp"

LOCK_TIMEOUT = 10.0
LOCK_RETRY = 0.05


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
        return _to_todo(json.loads(raw, object_pairs_hook=_unique_keys))
    except json.JSONDecodeError as error:
        raise ValueError(f"malformed JSON: {error.msg}") from error
    except _InvalidRecord as error:
        raise ValueError(f"invalid record: {error}") from error


class _InvalidRecord(Exception):
    pass


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise _InvalidRecord(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def _check_keys(obj: object, keys: list[str], what: str) -> None:
    if not isinstance(obj, dict):
        raise _InvalidRecord(f"{what} is not a JSON object")
    missing = next((key for key in keys if key not in obj), None)
    if missing is not None:
        raise _InvalidRecord(f"{what} is missing key {missing!r}")
    unknown = next((key for key in obj if key not in keys), None)
    if unknown is not None:
        raise _InvalidRecord(f"{what} has unknown key {unknown!r}")


def _check_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _InvalidRecord(f"{name} must be a nonblank string")
    return value


def _check_date(value: object, name: str) -> None:
    if isinstance(value, str) and DATE_RE.fullmatch(value):
        try:
            date.fromisoformat(value)
            return
        except ValueError:
            pass
    raise _InvalidRecord(f"{name} must be null or a date YYYY-MM-DD")


def _check_timestamp(value: object, name: str) -> str:
    if isinstance(value, str) and TIMESTAMP_RE.fullmatch(value):
        try:
            datetime.strptime(value, TIMESTAMP_FORMAT)
            return value
        except ValueError:
            pass
    raise _InvalidRecord(f"{name} must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")


def _to_todo(obj: object) -> Todo:
    _check_keys(obj, TODO_KEYS, "record")

    if not isinstance(obj["id"], str) or not ID_RE.fullmatch(obj["id"]):
        raise _InvalidRecord("id must be four characters from 0-9a-z")

    title = _check_text(obj["title"], "title")
    if "\n" in title or "\r" in title:
        raise _InvalidRecord("title must not contain a line break")

    if obj["priority"] not in PRIORITIES:
        raise _InvalidRecord("priority must be high, normal or low")

    if obj["due_date"] is not None:
        _check_date(obj["due_date"], "due_date")

    if obj["status"] not in STATUSES:
        raise _InvalidRecord("status must be open or done")

    if not isinstance(obj["notes"], list):
        raise _InvalidRecord("notes must be a list")
    notes = []
    for note in obj["notes"]:
        _check_keys(note, NOTE_KEYS, "note")
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
            raise _InvalidRecord("completed_at must be null when status is open")
    else:
        _check_timestamp(obj["completed_at"], "completed_at")

    return Todo(**{**obj, "notes": notes})


def mutate[T](
    path: str | os.PathLike,
    change: Callable[[list[Todo]], tuple[list[Todo], T]],
    *,
    lock_path: str | os.PathLike | None = None,
    tmp_dir: str | os.PathLike | None = None,
    tmp_prefix: str = TMP_PREFIX,
    new_mode: int | None = 0o600,
) -> T:
    """Apply change to the records under the lock and replace the file atomically.

    change takes the loaded records and returns (new_records, result). mutate
    returns result. If the new records equal the old ones, the file is not
    written. The temporary file is named tmp_prefix + random + TMP_SUFFIX. A new
    data file gets new_mode, or the umask default if it is None.
    """
    path = os.path.realpath(path)
    directory = os.path.dirname(path)
    lock_path = lock_path if lock_path is not None else path + ".lock"
    tmp_dir = tmp_dir if tmp_dir is not None else directory
    for needed in {directory, os.path.dirname(lock_path), tmp_dir}:
        os.makedirs(needed, exist_ok=True)

    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _lock(lock_fd, path)
        records = load(path)
        new_records, result = change(records)
        if new_records != records:
            _replace(path, dumps(new_records), tmp_dir, tmp_prefix, new_mode)
        return result
    finally:
        os.close(lock_fd)


def _lock(fd: int, path: str) -> None:
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TodoError(f"timed out waiting for the lock on {path}") from None
            time.sleep(LOCK_RETRY)


def _before_replace(tmp_name: str) -> None:
    """Run between the temporary write and the replace. Tests patch it to fail."""


def _replace(
    path: str,
    text: str,
    tmp_dir: str | os.PathLike,
    tmp_prefix: str,
    new_mode: int | None,
) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, prefix=tmp_prefix, suffix=TMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(text.encode("utf-8"))
            file.flush()
            os.fsync(file.fileno())
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except FileNotFoundError:
            mode = new_mode if new_mode is not None else 0o666 & ~_umask()
        os.chmod(tmp_name, mode)
        _before_replace(tmp_name)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    _fsync_dir(os.path.dirname(path))


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def _fsync_dir(directory: str) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
