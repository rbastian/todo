"""Todo ID allocation and normalization."""

import re
import secrets
from collections.abc import Iterable

from todo.errors import TodoError

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
ID_LENGTH = 4
ID_RE = re.compile(f"[0-9a-z]{{{ID_LENGTH}}}")


def _candidate() -> str:
    """Return one random ID. Tests patch this function to force collisions."""
    return "".join(secrets.choice(ALPHABET) for _ in range(ID_LENGTH))


def new_id(existing_ids: Iterable[str], *, attempts: int = 100) -> str:
    """Return a random ID that is not in existing_ids.

    Call it inside the mutate() change function with the IDs of every
    current record, open and done, so that the check runs under the lock.
    """
    taken = set(existing_ids)
    for _ in range(attempts):
        candidate = _candidate()
        if candidate not in taken:
            return candidate
    raise TodoError(f"could not allocate a free ID after {attempts} attempts")


def normalize_id(text: str) -> str:
    """Return the ID in canonical form, or raise a CLI input error (exit 2)."""
    normalized = text.strip().lower()
    if not ID_RE.fullmatch(normalized):
        raise TodoError(f"invalid ID '{text}'", exit_code=2)
    return normalized
