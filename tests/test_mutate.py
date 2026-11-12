import os
import stat
import subprocess
import sys
import textwrap
from dataclasses import replace

import pytest

from todo import store
from todo.errors import TodoError
from todo.store import Note, Todo, dumps, load, mutate


def todo(id, title="A todo"):
    return Todo(
        id=id,
        title=title,
        priority="normal",
        due_date=None,
        status="open",
        notes=[Note(created_at="2026-11-12T14:05:00Z", text="note")],
        created_at="2026-11-12T14:00:00Z",
        updated_at="2026-11-12T14:00:00Z",
        completed_at=None,
    )


def add(new):
    def change(records):
        return [*records, new], new.id

    return change


def leftovers(directory):
    return sorted(name for name in os.listdir(directory) if name.endswith(".tmp"))


def test_mutate_writes_new_records_and_returns_result(tmp_path):
    path = tmp_path / "todos.jsonl"

    result = mutate(path, add(todo("aaaa")))

    assert result == "aaaa"
    assert load(path) == [todo("aaaa")]
    assert path.read_text(encoding="utf-8") == dumps([todo("aaaa")])


def test_mutate_passes_loaded_records_to_change(tmp_path):
    path = tmp_path / "todos.jsonl"
    path.write_text(dumps([todo("bbbb"), todo("aaaa")]), encoding="utf-8")
    seen = []

    def change(records):
        seen.extend(records)
        return [replace(r, title="Changed") for r in records], None

    mutate(path, change)

    assert seen == [todo("aaaa"), todo("bbbb")]
    assert [t.title for t in load(path)] == ["Changed", "Changed"]


def test_first_write_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "a" / "b" / "todos.jsonl"

    mutate(path, add(todo("aaaa")))

    assert load(path) == [todo("aaaa")]
    assert (tmp_path / "a" / "b" / "todos.jsonl.lock").exists()


def test_lock_and_tmp_dir_can_live_elsewhere(tmp_path):
    path = tmp_path / "list" / "todos.jsonl"
    lock_path = tmp_path / "meta" / "deep" / "todo.lock"
    tmp_dir = tmp_path / "meta" / "tmp"

    mutate(path, add(todo("aaaa")), lock_path=lock_path, tmp_dir=tmp_dir)

    assert load(path) == [todo("aaaa")]
    assert lock_path.exists()
    assert tmp_dir.is_dir()
    assert os.listdir(path.parent) == ["todos.jsonl"]


def test_lock_file_is_kept_after_write(tmp_path):
    path = tmp_path / "todos.jsonl"

    mutate(path, add(todo("aaaa")))
    mutate(path, add(todo("bbbb")))

    assert (tmp_path / "todos.jsonl.lock").exists()
    assert leftovers(tmp_path) == []


def test_failure_before_replace_leaves_file_intact(tmp_path, monkeypatch):
    path = tmp_path / "todos.jsonl"
    original = dumps([todo("aaaa")])
    path.write_text(original, encoding="utf-8")

    def fail(tmp_name):
        assert os.path.exists(tmp_name)
        raise OSError("simulated failure")

    monkeypatch.setattr(store, "_before_replace", fail)

    with pytest.raises(OSError, match="simulated failure"):
        mutate(path, add(todo("bbbb")))

    assert path.read_text(encoding="utf-8") == original
    assert load(path) == [todo("aaaa")]
    assert leftovers(tmp_path) == []


def test_failure_in_change_leaves_file_intact(tmp_path):
    path = tmp_path / "todos.jsonl"
    original = dumps([todo("aaaa")])
    path.write_text(original, encoding="utf-8")

    def change(records):
        raise TodoError("no such todo")

    with pytest.raises(TodoError, match="no such todo"):
        mutate(path, change)

    assert path.read_text(encoding="utf-8") == original
    assert leftovers(tmp_path) == []


def test_validation_error_aborts_without_change(tmp_path):
    path = tmp_path / "todos.jsonl"
    original = "not json\n"
    path.write_text(original, encoding="utf-8")
    called = []

    def change(records):
        called.append(records)
        return records, None

    with pytest.raises(TodoError, match="malformed JSON"):
        mutate(path, change)

    assert called == []
    assert path.read_text(encoding="utf-8") == original
    assert leftovers(tmp_path) == []


def test_unchanged_records_skip_the_write(tmp_path):
    path = tmp_path / "todos.jsonl"
    path.write_text(dumps([todo("aaaa")]), encoding="utf-8")
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    inode = path.stat().st_ino

    result = mutate(path, lambda records: (list(records), "same"))

    assert result == "same"
    assert path.stat().st_mtime_ns == 1_000_000_000
    assert path.stat().st_ino == inode


def test_symlinked_data_file_updates_its_target(tmp_path):
    target = tmp_path / "real" / "todos.jsonl"
    target.parent.mkdir()
    target.write_text(dumps([todo("aaaa")]), encoding="utf-8")
    link = tmp_path / "todos.jsonl"
    link.symlink_to(target)

    mutate(link, add(todo("bbbb")))

    assert link.is_symlink()
    assert load(target) == [todo("aaaa"), todo("bbbb")]
    assert (target.parent / "todos.jsonl.lock").exists()


def test_new_data_file_gets_mode_0600(tmp_path):
    path = tmp_path / "todos.jsonl"

    mutate(path, add(todo("aaaa")))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_new_data_file_gets_umask_default_when_mode_is_none(tmp_path):
    path = tmp_path / "todos.jsonl"
    old = os.umask(0o022)
    try:
        mutate(path, add(todo("aaaa")), new_mode=None)
    finally:
        os.umask(old)

    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_existing_data_file_keeps_its_mode(tmp_path):
    path = tmp_path / "todos.jsonl"
    path.write_text(dumps([todo("aaaa")]), encoding="utf-8")
    path.chmod(0o640)

    mutate(path, add(todo("bbbb")))

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_lock_timeout_raises_todo_error(tmp_path, monkeypatch):
    path = tmp_path / "todos.jsonl"
    lock_path = tmp_path / "todos.jsonl.lock"
    monkeypatch.setattr(store, "LOCK_TIMEOUT", 0.2)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import fcntl, os, sys, time
                fd = os.open({str(lock_path)!r}, os.O_RDWR | os.O_CREAT, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                print("locked", flush=True)
                sys.stdin.read()
                """
            ),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline() == "locked\n"
        with pytest.raises(TodoError) as caught:
            mutate(path, add(todo("aaaa")))
        assert str(caught.value) == f"timed out waiting for the lock on {path}"
        assert caught.value.exit_code == 1
    finally:
        holder.communicate("")
    assert not path.exists()


def test_two_writers_in_separate_processes_both_persist(tmp_path):
    path = tmp_path / "todos.jsonl"
    count = 40
    script = textwrap.dedent(
        """
        import sys
        from todo.store import Note, Todo, mutate

        path, prefix, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
        for n in range(count):
            new = Todo(
                id=f"{prefix}{n:03d}",
                title="Writer",
                priority="normal",
                due_date=None,
                status="open",
                notes=[],
                created_at="2026-11-12T14:00:00Z",
                updated_at="2026-11-12T14:00:00Z",
                completed_at=None,
            )
            mutate(path, lambda records: ([*records, new], None))
        """
    )
    writers = [
        subprocess.Popen([sys.executable, "-c", script, str(path), prefix, str(count)])
        for prefix in ("a", "b")
    ]
    for writer in writers:
        assert writer.wait(timeout=60) == 0

    ids = [t.id for t in load(path)]
    expected = sorted(f"{p}{n:03d}" for p in ("a", "b") for n in range(count))
    assert ids == expected
    assert leftovers(tmp_path) == []
