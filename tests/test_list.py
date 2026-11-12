import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import pytest

from todo import store
from todo.cli import main
from todo.store import Todo, dumps

TODAY = date(2026, 11, 12)


@pytest.fixture
def data_file(tmp_path, monkeypatch):
    path = tmp_path / "todos.jsonl"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_FILE", str(path))
    monkeypatch.setattr(store, "today", lambda: TODAY)
    return path


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def todo(
    id,
    title,
    priority="normal",
    due_date=None,
    status="open",
    created_at="2026-11-01T10:00:00Z",
):
    return Todo(
        id=id,
        title=title,
        priority=priority,
        due_date=due_date,
        status=status,
        notes=[],
        created_at=created_at,
        updated_at=created_at,
        completed_at=created_at if status == "done" else None,
    )


def write(path, *records):
    path.write_text(dumps(list(records)))


def day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


def test_layout_matches_the_decided_example(capsys, data_file):
    write(
        data_file,
        todo("k7m2", "Pay rent", "high", "2026-11-10"),
        todo("q3x9", "Renew family membership", "high", "2026-11-20"),
        todo("a1b2", "Buy milk"),
        todo("z8c4", "Return library book", "low", "2026-11-01", "done"),
    )

    code, out, err = run(capsys, "--list", "--all")

    assert (code, err) == (0, "")
    assert out == (
        "k7m2  open  high    2026-11-10  Pay rent\n"
        "q3x9  open  high    2026-11-20  Renew family membership\n"
        "a1b2  open  normal  -           Buy milk\n"
        "z8c4  done  low     2026-11-01  Return library book\n"
    )


def test_rows_have_no_trailing_whitespace(capsys, data_file):
    write(data_file, todo("a1b2", "x", "low"), todo("b2c3", "y", "high", day(0)))
    _, out, _ = run(capsys, "--list")
    for row in out.splitlines():
        assert row == row.rstrip()


def test_sort_by_priority_then_due_date_with_undated_last(capsys, data_file):
    # Spec acceptance check 3: yesterday, today, tomorrow and undated.
    write(
        data_file,
        todo("aaaa", "normal undated"),
        todo("bbbb", "normal tomorrow", due_date=day(1)),
        todo("cccc", "normal today", due_date=day(0)),
        todo("dddd", "normal yesterday", due_date=day(-1)),
        todo("eeee", "low yesterday", "low", day(-1)),
        todo("ffff", "high undated", "high"),
        todo("gggg", "high tomorrow", "high", day(1)),
    )

    _, out, _ = run(capsys, "--list")

    assert [row[32:] for row in out.splitlines()] == [
        "high tomorrow",
        "high undated",
        "normal yesterday",
        "normal today",
        "normal tomorrow",
        "normal undated",
        "low yesterday",
    ]


def test_ties_break_by_created_at_then_id(capsys, data_file):
    write(
        data_file,
        todo("aaaa", "later", created_at="2026-11-02T00:00:00Z"),
        todo("zzzz", "earlier", created_at="2026-11-01T00:00:00Z"),
        todo("mmmm", "later, higher id", created_at="2026-11-02T00:00:00Z"),
    )

    _, out, _ = run(capsys, "--list")

    assert [row[:4] for row in out.splitlines()] == ["zzzz", "aaaa", "mmmm"]


def test_order_does_not_depend_on_file_order(capsys, data_file):
    records = [
        todo("aaaa", "low", "low"),
        todo("bbbb", "high", "high"),
        todo("cccc", "normal"),
    ]
    data_file.write_text("".join(dumps([r]) for r in reversed(records)))
    _, reversed_out, _ = run(capsys, "--list")
    write(data_file, *records)
    _, sorted_out, _ = run(capsys, "--list")

    assert reversed_out == sorted_out
    assert [row[:4] for row in sorted_out.splitlines()] == ["bbbb", "cccc", "aaaa"]


@pytest.mark.parametrize("argv", [[], ["--list"], ["--all"]])
def test_list_shows_open_todos_including_past_due(capsys, data_file, argv):
    write(
        data_file,
        todo("aaaa", "past due", due_date=day(-30)),
        todo("bbbb", "finished", status="done"),
    )

    code, out, err = run(capsys, *argv)

    assert (code, err) == (0, "")
    assert out == f"aaaa  open  normal  {day(-30)}  past due\n"


def test_list_all_shows_open_then_done_each_sorted(capsys, data_file):
    write(
        data_file,
        todo("aaaa", "done high", "high", status="done"),
        todo("bbbb", "open low", "low"),
        todo("cccc", "done low", "low", status="done"),
        todo("dddd", "open high", "high"),
    )

    _, out, _ = run(capsys, "--list", "--all")

    assert [row[:4] for row in out.splitlines()] == ["dddd", "bbbb", "aaaa", "cccc"]


def test_today_shows_only_open_todos_due_today(capsys, data_file):
    write(
        data_file,
        todo("aaaa", "yesterday", due_date=day(-1)),
        todo("bbbb", "today low", "low", day(0)),
        todo("cccc", "tomorrow", due_date=day(1)),
        todo("dddd", "undated"),
        todo("eeee", "today done", due_date=day(0), status="done"),
        todo("ffff", "today high", "high", day(0)),
    )

    code, out, err = run(capsys, "--today")

    assert (code, err) == (0, "")
    assert out == (
        f"ffff  open  high    {day(0)}  today high\n"
        f"bbbb  open  low     {day(0)}  today low\n"
    )


@pytest.mark.parametrize("argv", [["--list"], ["--list", "--all"], ["--today"]])
def test_missing_file_prints_nothing_and_exits_0(capsys, data_file, argv):
    assert run(capsys, *argv) == (0, "", "")
    assert not data_file.exists()


@pytest.mark.parametrize("argv", [["--list"], ["--today"]])
def test_no_match_prints_nothing_and_exits_0(capsys, data_file, argv):
    write(data_file, todo("aaaa", "finished", due_date=day(0), status="done"))
    assert run(capsys, *argv) == (0, "", "")


def test_list_does_not_change_the_file(capsys, data_file):
    write(data_file, todo("aaaa", "one"))
    before = data_file.read_bytes()
    run(capsys, "--list", "--all")
    assert data_file.read_bytes() == before


def test_invalid_file_exits_1_with_line_number(capsys, data_file):
    data_file.write_text("not json\n")
    code, out, err = run(capsys, "--list")
    assert code == 1
    assert out == ""
    assert err.startswith(f"todo: error: {data_file}:1: malformed JSON")


def test_unreadable_path_exits_1(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_FILE", str(tmp_path))
    code, out, err = run(capsys, "--list")
    assert code == 1
    assert out == ""
    assert err.startswith(f"todo: error: {tmp_path}: cannot read")


# The midnight boundary: 2026-11-12 23:59:59 and 2026-11-13 00:00:00 in
# America/Chicago (UTC-6 in November). At 23:59:59 the UTC date is already
# 2026-11-13, so a UTC-based date would fail the first step.
BEFORE_MIDNIGHT = datetime(2026, 11, 13, 5, 59, 59, tzinfo=UTC).timestamp()
AT_MIDNIGHT = datetime(2026, 11, 13, 6, 0, 0, tzinfo=UTC).timestamp()

CLOCKED_CLI = """\
import sys, time
time.time = lambda: float(sys.argv[1])
from todo.cli import main
raise SystemExit(main(sys.argv[2:]))
"""


def run_at(tmp_path, epoch, *argv):
    env = {
        **os.environ,
        "TZ": "America/Chicago",
        "HOME": str(tmp_path),
        "TODO_FILE": str(tmp_path / "todos.jsonl"),
    }
    result = subprocess.run(
        [sys.executable, "-c", CLOCKED_CLI, str(epoch), *argv],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_today_follows_the_local_date_across_midnight(tmp_path):
    first = run_at(tmp_path, BEFORE_MIDNIGHT, "--open", "First", "--due", "today")
    second = run_at(tmp_path, AT_MIDNIGHT, "--open", "Second", "--due", "today")
    first_id, second_id = first.strip(), second.strip()

    assert run_at(tmp_path, BEFORE_MIDNIGHT, "--today") == (
        f"{first_id}  open  normal  2026-11-12  First\n"
    )
    assert run_at(tmp_path, AT_MIDNIGHT, "--today") == (
        f"{second_id}  open  normal  2026-11-13  Second\n"
    )
