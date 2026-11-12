"""The argparse command line for todo."""

import argparse
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import date

from todo import store
from todo.datafile import init_repo_list, mutate_resolved, resolve_data_file
from todo.errors import TodoError
from todo.ids import new_id, normalize_id
from todo.store import PRIORITIES, Note, Todo, is_date, load, utc_now

EXAMPLES = """\
examples:
  todo --init
  todo --open "Renew club membership"
  todo --list
  todo --today
  todo --show k7m2
  todo --update k7m2 --title "Renew family membership" --priority high --due 2026-11-20
  todo --update k7m2 --clear-due
  todo --note k7m2 "Check family pricing first"
  todo --done k7m2
  todo --list --all
  todo --delete k7m2
"""

# The action flags in the mutually exclusive group. --note is also an action,
# but it sits outside the group because it is a modifier of --open too.
ACTIONS = ("init", "open", "list", "today", "show", "update", "done", "delete")

# The actions that each modifier can go with, keyed by argparse dest.
MODIFIERS = {
    "priority": {"open", "update"},
    "due": {"open", "update"},
    "clear_due": {"update"},
    "title": {"update"},
    "all": {"list"},
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="todo",
        description="Capture, prioritize, update and complete todos.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--init", action="store_true", help="initialize a list in this Git repository"
    )
    actions.add_argument("--open", metavar="TITLE", help="create an open todo")
    actions.add_argument("--list", action="store_true", help="list open todos")
    actions.add_argument(
        "--today", action="store_true", help="list open todos due today"
    )
    actions.add_argument("--show", metavar="ID", help="show one todo")
    actions.add_argument("--update", metavar="ID", help="set fields on an open todo")
    actions.add_argument("--done", metavar="ID", help="mark a todo complete")
    actions.add_argument("--delete", metavar="ID", help="permanently remove a todo")
    parser.add_argument(
        "--note",
        nargs="+",
        metavar="ARG",
        help="ID TEXT: append a note to a todo; with --open, TEXT: add a first note",
    )
    parser.add_argument(
        "--priority",
        choices=["high", "normal", "low"],
        help="priority, with --open or --update",
    )
    due = parser.add_mutually_exclusive_group()
    due.add_argument(
        "--due", metavar="DATE", help="YYYY-MM-DD or today, with --open or --update"
    )
    due.add_argument(
        "--clear-due", action="store_true", help="remove the due date, with --update"
    )
    parser.add_argument("--title", metavar="TEXT", help="new title, with --update")
    parser.add_argument(
        "--all", action="store_true", help="include completed todos, with --list"
    )
    return parser


def resolve_action(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """Return the action name and reject invalid combinations with exit 2."""
    action = next(
        (name for name in ACTIONS if getattr(args, name) not in (None, False)), None
    )

    if args.note is not None:
        if action not in (None, "open"):
            parser.error(f"argument --note: not allowed with argument --{action}")
        elif action is None and len(args.note) != 2:
            parser.error("--note takes an ID and a TEXT")
        elif action == "open" and len(args.note) != 1:
            parser.error("--note with --open takes one TEXT")
        action = action or "note"

    action = action or "list"

    for dest, allowed in MODIFIERS.items():
        if getattr(args, dest) not in (None, False) and action not in allowed:
            flag = "--" + dest.replace("_", "-")
            parser.error(f"argument {flag}: not allowed with argument --{action}")

    return action


def not_implemented(action: str):
    def handler(args: argparse.Namespace) -> int:
        raise TodoError(f"--{action} is not implemented yet")

    return handler


def parse_due(text: str, today: date) -> str:
    """Return a due date as YYYY-MM-DD, or raise a CLI input error (exit 2)."""
    if text == "today":
        return today.isoformat()
    if is_date(text):
        return text
    raise TodoError(f"invalid date '{text}', use YYYY-MM-DD or today", exit_code=2)


def parse_title(text: str) -> str:
    """Return the stripped title, or raise a CLI input error (exit 2)."""
    title = text.strip()
    if not title:
        raise TodoError("title must not be blank", exit_code=2)
    if "\n" in title or "\r" in title:
        raise TodoError("title must not contain a line break", exit_code=2)
    return title


def parse_note(text: str) -> str:
    """Return the note text as typed, or raise a CLI input error (exit 2)."""
    if not text.strip():
        raise TodoError("note must not be blank", exit_code=2)
    return text


def open_todo(args: argparse.Namespace) -> int:
    title = parse_title(args.open)
    due_date = None if args.due is None else parse_due(args.due, store.today())
    note = None if args.note is None else parse_note(args.note[0])

    def change(records: list[Todo]) -> tuple[list[Todo], str]:
        now = utc_now()
        todo = Todo(
            id=new_id(record.id for record in records),
            title=title,
            priority=args.priority or "normal",
            due_date=due_date,
            status="open",
            notes=[] if note is None else [Note(created_at=now, text=note)],
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        return [*records, todo], todo.id

    print(mutate_resolved(resolve_data_file(), change))
    return 0


def sort_key(todo: Todo) -> tuple:
    """Priority, then due date with undated last, then creation time and ID."""
    return (
        PRIORITIES.index(todo.priority),
        todo.due_date is None,
        todo.due_date or "",
        todo.created_at,
        todo.id,
    )


def format_row(todo: Todo) -> str:
    return (
        f"{todo.id}  {todo.status}  {todo.priority:<6}  "
        f"{todo.due_date or '-':<10}  {todo.title}"
    )


def print_rows(keep: Callable[[Todo], bool]) -> None:
    """Print the kept todos of the data file: open ones first, each group sorted."""
    todos = sorted(
        (todo for todo in load(resolve_data_file().path) if keep(todo)),
        key=lambda todo: (todo.status != "open", *sort_key(todo)),
    )
    sys.stdout.write("".join(format_row(todo) + "\n" for todo in todos))


def list_todos(args: argparse.Namespace) -> int:
    # The bead design: todo --all without --list shows open todos only.
    show_done = args.list and args.all
    print_rows(lambda todo: show_done or todo.status == "open")
    return 0


def list_today(args: argparse.Namespace) -> int:
    due = store.today().isoformat()
    print_rows(lambda todo: todo.status == "open" and todo.due_date == due)
    return 0


def format_todo(todo: Todo) -> str:
    """Return the --show layout: one field per line, labels padded to 11."""
    fields = [
        ("id", todo.id),
        ("title", todo.title),
        ("status", todo.status),
        ("priority", todo.priority),
        ("due", todo.due_date or "-"),
        ("created", todo.created_at),
        ("updated", todo.updated_at),
        ("completed", todo.completed_at or "-"),
    ]
    lines = [f"{label + ':':<11}{value}" for label, value in fields]
    if not todo.notes:
        lines.append(f"{'notes:':<11}-")
    else:
        lines.append("notes:")
        for note in todo.notes:
            lines.append(f"  {note.created_at}")
            lines.extend(f"    {line}" for line in note.text.split("\n"))
    return "\n".join(lines)


def find_todo(records: list[Todo], todo_id: str) -> Todo:
    """Return the todo with this ID, or raise a missing-ID error (exit 1)."""
    for todo in records:
        if todo.id == todo_id:
            return todo
    raise TodoError(f"no todo with ID '{todo_id}'")


def show_todo(args: argparse.Namespace) -> int:
    todo_id = normalize_id(args.show)
    print(format_todo(find_todo(load(resolve_data_file().path), todo_id)))
    return 0


def update_todo(args: argparse.Namespace) -> int:
    todo_id = normalize_id(args.update)
    changes = {}
    if args.title is not None:
        changes["title"] = parse_title(args.title)
    if args.priority is not None:
        changes["priority"] = args.priority
    if args.due is not None:
        changes["due_date"] = parse_due(args.due, date.today())
    if args.clear_due:
        changes["due_date"] = None
    if not changes:
        raise TodoError(
            "--update needs --title, --priority, --due or --clear-due", exit_code=2
        )

    def change(records: list[Todo]) -> tuple[list[Todo], str]:
        todo = next((record for record in records if record.id == todo_id), None)
        if todo is None:
            raise TodoError(f"todo {todo_id} not found")
        if todo.status != "open":
            raise TodoError(f"todo {todo_id} is done; only open todos can be updated")
        if all(getattr(todo, name) == value for name, value in changes.items()):
            return records, todo_id
        updated = replace(todo, **changes, updated_at=utc_now())
        return [updated if r is todo else r for r in records], todo_id

    print(mutate_resolved(resolve_data_file(), change))
    return 0


def handle_init(args: argparse.Namespace) -> int:
    print(init_repo_list())
    return 0


HANDLERS = {action: not_implemented(action) for action in (*ACTIONS, "note")}
HANDLERS["open"] = open_todo
HANDLERS["init"] = handle_init
HANDLERS["list"] = list_todos
HANDLERS["today"] = list_today
HANDLERS["show"] = show_todo
HANDLERS["update"] = update_todo


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    action = resolve_action(parser, args)
    try:
        return HANDLERS[action](args)
    except TodoError as error:
        print(f"todo: error: {error}", file=sys.stderr)
        return error.exit_code
    except OSError as error:
        # Any OS error, for example a storage failure: no traceback, exit 1.
        where = f"{error.filename}: " if error.filename else ""
        print(f"todo: error: {where}{error.strerror or error}", file=sys.stderr)
        return 1
