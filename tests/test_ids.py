import pytest

from todo import ids
from todo.cli import main
from todo.errors import TodoError


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "todos.jsonl"))


def force_candidates(monkeypatch, values):
    calls = iter(values)
    monkeypatch.setattr(ids, "_candidate", lambda: next(calls))


def test_new_id_is_four_base36_characters():
    for _ in range(50):
        assert ids.ID_RE.fullmatch(ids.new_id(set()))


def test_forced_collision_retries_and_returns_free_id(monkeypatch):
    force_candidates(monkeypatch, ["k7m2", "k7m2", "ab12"])
    assert ids.new_id({"k7m2"}) == "ab12"


def test_existing_ids_can_be_any_iterable(monkeypatch):
    force_candidates(monkeypatch, ["k7m2", "ab12"])
    assert ids.new_id(id for id in ["k7m2"]) == "ab12"


def test_100_collisions_raise_exit_1(monkeypatch):
    # A 101st call would raise StopIteration instead of TodoError.
    force_candidates(monkeypatch, ["k7m2"] * 100)
    with pytest.raises(TodoError) as exc:
        ids.new_id({"k7m2"})
    assert exc.value.exit_code == 1
    assert str(exc.value) == "could not allocate a free ID after 100 attempts"


def test_collision_on_last_attempt_still_succeeds(monkeypatch):
    force_candidates(monkeypatch, ["k7m2"] * 99 + ["ab12"])
    assert ids.new_id({"k7m2"}) == "ab12"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("k7m2", "k7m2"), ("K7M2", "k7m2"), ("  k7M2\n", "k7m2"), ("0000", "0000")],
)
def test_normalize_id_accepts_full_ids(text, expected):
    assert ids.normalize_id(text) == expected


@pytest.mark.parametrize(
    "text", ["k7m", "k7", "k7m2a", "", "   ", "k7-2", "k7m_", "ké12"]
)
def test_normalize_id_rejects_partial_or_malformed_ids(text):
    with pytest.raises(TodoError) as exc:
        ids.normalize_id(text)
    assert exc.value.exit_code == 2
    assert str(exc.value) == f"invalid ID '{text}'"


def test_invalid_id_error_reaches_stderr_with_exit_2(capsys, monkeypatch):
    from todo import cli

    def handler(args):
        ids.normalize_id(args.show)
        return 0

    monkeypatch.setitem(cli.HANDLERS, "show", handler)
    assert main(["--show", "k7m"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "todo: error: invalid ID 'k7m'\n"
