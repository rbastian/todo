"""The error type that main reports with an exit code."""


class TodoError(Exception):
    """An error that main prints and exits with. Exit 1 unless exit_code says otherwise."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code
