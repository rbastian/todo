"""The argparse command line for todo."""

import argparse
import sys

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

# The actions that each modifier can go with.
MODIFIERS = {
    "priority": ("--priority", {"open", "update"}),
    "due": ("--due", {"open", "update"}),
    "clear_due": ("--clear-due", {"update"}),
    "title": ("--title", {"update"}),
    "all": ("--all", {"list"}),
}


class TodoError(Exception):
    """An error that is not a CLI input error. main prints it and exits."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


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
    parser.add_argument(
        "--due", metavar="DATE", help="YYYY-MM-DD or today, with --open or --update"
    )
    parser.add_argument(
        "--clear-due", action="store_true", help="remove the due date, with --update"
    )
    parser.add_argument("--title", metavar="TEXT", help="new title, with --update")
    parser.add_argument(
        "--all", action="store_true", help="include completed todos, with --list"
    )
    return parser


def resolve_action(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """Return the action name and reject invalid combinations with exit 2."""
    chosen = [
        name
        for name in ("init", "open", "list", "today", "show", "update", "done", "delete")
        if getattr(args, name) not in (None, False)
    ]
    action = chosen[0] if chosen else None

    if args.note is not None:
        if action is None:
            if len(args.note) != 2:
                parser.error("--note takes an ID and a TEXT")
            action = "note"
        elif action == "open":
            if len(args.note) != 1:
                parser.error("--note with --open takes one TEXT")
        else:
            parser.error(f"argument --note: not allowed with argument --{action}")

    if action is None:
        action = "list"

    for dest, (flag, allowed) in MODIFIERS.items():
        if getattr(args, dest) not in (None, False) and action not in allowed:
            parser.error(f"argument {flag}: not allowed with argument --{action}")

    if args.due is not None and args.clear_due:
        parser.error("argument --clear-due: not allowed with argument --due")

    return action


def not_implemented(action: str):
    def handler(args: argparse.Namespace) -> int:
        raise TodoError(f"--{action} is not implemented yet")

    return handler


HANDLERS = {
    action: not_implemented(action)
    for action in (
        "init",
        "open",
        "list",
        "today",
        "show",
        "update",
        "note",
        "done",
        "delete",
    )
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    action = resolve_action(parser, args)
    try:
        return HANDLERS[action](args)
    except TodoError as error:
        print(f"todo: error: {error}", file=sys.stderr)
        return error.exit_code
