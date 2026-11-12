"""The error type that main reports with an exit code."""


class TodoError(Exception):
    """An error that is not a CLI input error. main prints it and exits."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code
