import importlib.metadata

import pytest

from todo.cli import main


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "todos.jsonl"))


def expected_version() -> str:
    major, minor = importlib.metadata.version("todo").split(".")[:2]
    return f"todo {major}.{minor}\n"


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_version_prints_major_minor(capsys):
    code, out, err = run(capsys, "--version")
    assert code == 0
    assert out == expected_version()
    assert err == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["--version", "--list"],
        ["--version", "--priority", "high"],
        ["--version", "--open", "Title"],
        ["--version", "--all"],
        ["--version", "--note", "k7m2", "text"],
        ["--version", "word"],
    ],
)
def test_version_with_other_arguments_exits_2(capsys, argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage:" in err
    assert "todo: error:" in err


def test_version_ignores_malformed_data_file(tmp_path, monkeypatch, capsys):
    data = tmp_path / "bad.jsonl"
    data.write_bytes(b"not json\n")
    monkeypatch.setenv("TODO_FILE", str(data))
    code, out, err = run(capsys, "--version")
    assert code == 0
    assert out == expected_version()
    assert err == ""
    assert data.read_bytes() == b"not json\n"
    assert list(tmp_path.iterdir()) == [data]


def test_help_lists_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out, _ = capsys.readouterr()
    assert "--version" in out
    assert "  todo --version\n" in out
