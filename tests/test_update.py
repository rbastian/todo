import json
import os
import subprocess
import sys
from datetime import date

import pytest

from todo.cli import main
from todo.store import TIMESTAMP_RE

OLD = "2026-11-12T14:00:00Z"


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


def cli(data_file, *argv):
    """Run the CLI in a new process, as the acceptance check asks."""
    env = {**os.environ, "TODO_FILE": str(data_file), "HOME": str(data_file.parent)}
    return subprocess.run(
        [sys.executable, "-m", "todo", *argv],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )


def record(**fields):
    base = {
        "id": "k7m2",
        "title": "Renew club membership",
        "priority": "normal",
        "due_date": "2026-11-15",
        "status": "open",
        "notes": [{"created_at": OLD, "text": "First"}],
        "created_at": OLD,
        "updated_at": OLD,
        "completed_at": None,
    }
    return {**base, **fields}


def write(data_file, *records):
    data_file.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
    )


def stored(data_file):
    return {r["id"]: r for r in map(json.loads, data_file.read_text().splitlines())}


def test_acceptance_update_then_clear_due_across_processes(data_file):
    opened = cli(data_file, "--open", "Renew club membership")
    assert opened.returncode == 0
    todo_id = opened.stdout.strip()

    result = cli(
        data_file,
        "--update",
        todo_id,
        "--title",
        "Renew family membership",
        "--priority",
        "high",
        "--due",
        "2026-11-20",
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        todo_id + "\n",
        "",
    )
    after = stored(data_file)[todo_id]
    assert after["title"] == "Renew family membership"
    assert after["priority"] == "high"
    assert after["due_date"] == "2026-11-20"

    result = cli(data_file, "--update", todo_id, "--clear-due")
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        todo_id + "\n",
        "",
    )
    cleared = stored(data_file)[todo_id]
    assert cleared["due_date"] is None
    assert cleared["title"] == "Renew family membership"
    assert cleared["priority"] == "high"
    assert cleared["status"] == "open"
    assert cleared["created_at"] == after["created_at"]


def test_update_sets_fields_and_updated_at_only(capsys, data_file):
    write(data_file, record())

    code, out, err = run(capsys, "--update", "K7M2", "--title", "  New title ")

    assert (code, out, err) == (0, "k7m2\n", "")
    after = stored(data_file)["k7m2"]
    assert after["title"] == "New title"
    assert after["priority"] == "normal"
    assert after["due_date"] == "2026-11-15"
    assert after["notes"] == [{"created_at": OLD, "text": "First"}]
    assert after["created_at"] == OLD
    assert after["completed_at"] is None
    assert TIMESTAMP_RE.fullmatch(after["updated_at"])
    assert after["updated_at"] != OLD


def test_update_due_today_stores_local_date(capsys, data_file):
    write(data_file, record())
    code, _, _ = run(capsys, "--update", "k7m2", "--due", "today")
    assert code == 0
    assert stored(data_file)["k7m2"]["due_date"] == date.today().isoformat()


def test_update_leaves_other_records_alone(capsys, data_file):
    other = record(id="ab12", title="Other")
    write(data_file, other, record())
    code, _, _ = run(capsys, "--update", "k7m2", "--priority", "low")
    assert code == 0
    assert stored(data_file)["ab12"] == other


@pytest.mark.parametrize(
    "argv",
    [
        ["--title", "Renew club membership"],
        ["--title", "  Renew club membership  "],
        ["--priority", "normal"],
        ["--due", "2026-11-15"],
        ["--title", "Renew club membership", "--priority", "normal"],
    ],
)
def test_unchanged_update_preserves_file(capsys, data_file, argv):
    write(data_file, record())
    before = data_file.read_bytes()
    mtime = data_file.stat().st_mtime_ns

    code, out, err = run(capsys, "--update", "k7m2", *argv)

    assert (code, out, err) == (0, "k7m2\n", "")
    assert data_file.read_bytes() == before
    assert data_file.stat().st_mtime_ns == mtime
    assert stored(data_file)["k7m2"]["updated_at"] == OLD


def test_clear_due_on_undated_todo_is_unchanged(capsys, data_file):
    write(data_file, record(due_date=None))
    before = data_file.read_bytes()
    code, out, _ = run(capsys, "--update", "k7m2", "--clear-due")
    assert (code, out) == (0, "k7m2\n")
    assert data_file.read_bytes() == before


def test_update_done_todo_exits_1(capsys, data_file):
    write(data_file, record(status="done", completed_at=OLD))
    before = data_file.read_bytes()

    code, out, err = run(capsys, "--update", "k7m2", "--title", "New")

    assert (code, out) == (1, "")
    assert err == "todo: error: todo k7m2 is done; only open todos can be updated\n"
    assert data_file.read_bytes() == before


def test_update_unknown_id_exits_1(capsys, data_file):
    write(data_file, record())
    before = data_file.read_bytes()

    code, out, err = run(capsys, "--update", "zzzz", "--title", "New")

    assert (code, out) == (1, "")
    assert err == "todo: error: todo zzzz not found\n"
    assert data_file.read_bytes() == before


def test_update_unknown_id_on_missing_file_exits_1(capsys, data_file):
    code, _, err = run(capsys, "--update", "zzzz", "--clear-due")
    assert code == 1
    assert err == "todo: error: todo zzzz not found\n"
    assert not data_file.exists()


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["k7m2"], "--update needs --title, --priority, --due or --clear-due"),
        (["k7m", "--title", "New"], "invalid ID 'k7m'"),
        (["k7m2", "--title", "   "], "title must not be blank"),
        (["k7m2", "--title", "One\nTwo"], "title must not contain a line break"),
        (
            ["k7m2", "--due", "2026-02-30"],
            "invalid date '2026-02-30', use YYYY-MM-DD or today",
        ),
    ],
)
def test_invalid_input_exits_2_and_leaves_file_unchanged(
    capsys, data_file, argv, message
):
    write(data_file, record())
    before = data_file.read_bytes()

    code, out, err = run(capsys, "--update", *argv)

    assert (code, out) == (2, "")
    assert err == f"todo: error: {message}\n"
    assert data_file.read_bytes() == before
    assert not data_file.with_name(data_file.name + ".lock").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["k7m2", "--due", "2026-11-20", "--clear-due"],
        ["k7m2", "--priority", "urgent"],
    ],
)
def test_argparse_rejections_exit_2(capsys, data_file, argv):
    write(data_file, record())
    before = data_file.read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(["--update", *argv])
    assert exc.value.code == 2
    assert data_file.read_bytes() == before
