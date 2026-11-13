# Python Todo CLI Specification

Version 0.2

### Changes from 0.1

- The personal list moves to `~/.todo/todos.jsonl`.
- `--due` also accepts English date phrases such as `tomorrow`, `friday` and `next week`, through the `parsedatetime` package.
- Words without an action flag create a todo, for example `todo Buy milk`.
- `todo --version` prints the specification version.

## Scope

Build a Python CLI with a personal list and optional Git-tracked repository lists for capturing, prioritizing, updating, and completing todos. Install it as the `todo` command. Use `argparse` for parsing and JSONL for persistence.

## Commands

Use one action flag per invocation. `todo` alone is shorthand for `todo --list`. Words without an action flag are shorthand for `--open`, so `todo Renew club membership` is the same as `todo --open "Renew club membership"`.

| Command | Behavior |
| --- | --- |
| `todo --init` | Initialize a todo list at `.todo/todos.jsonl` under the current Git working-tree root. Repeating the command preserves existing todos. |
| `todo --open "Renew club membership"` | Create an open todo and print its ID. Accept optional `--priority`, `--due`, and `--note`. |
| `todo Renew club membership --priority high` | Create an open todo from the words, joined with single spaces, and print its ID. Accept the same options as `--open`. |
| `todo --list` | List open todos in priority order. |
| `todo --today` | List open todos due on the machine's current local calendar date. |
| `todo --show k7m2` | Show one open or completed todo, including every note in append order. |
| `todo --update k7m2 --title "Renew family membership" --priority high --due 2026-11-20` | Set one or more fields on an open todo. Omitted fields keep their values. |
| `todo --update k7m2 --clear-due` | Remove the due date. Mutually exclusive with `--due`. |
| `todo --note k7m2 "Check family pricing first"` | Append a timestamped note to an open or completed todo. Repeat the command to add more notes. |
| `todo --done k7m2` | Mark an open todo complete and record when it was completed. Repeating the command succeeds and preserves the original completion time. |
| `todo --list --all` | Include completed todos in the list. |
| `todo --delete k7m2` | Permanently remove an open or completed todo and its notes from the active data file. |
| `todo --help` | Show syntax and examples. |
| `todo --version` | Print `todo` and the specification version that the program meets, for example `todo 0.2`. |

### Version

The package version is the specification version with a patch number, for example `0.2.0` for version 0.2. `todo --version` reads the installed package version and prints its first two numbers. The version exists only in `pyproject.toml`.

### Input and output

- Require a nonblank title on creation. A todo starts with priority `normal`, no due date, and zero notes unless supplied.
- Title words come before any option. A word after an option that the option does not take is invalid input. Words together with an action flag are invalid input. The command names are `init`, `open`, `list`, `today`, `show`, `update`, `note`, `done`, `delete` and `help`. If the words are exactly one command name, return 2 and suggest the flag, for example "did you mean --list?". `todo list groceries` still creates a todo.
- Accept priorities `high`, `normal`, and `low`.
- Accept a due date as a valid calendar date in `YYYY-MM-DD` format or as an English date phrase. When the command runs, resolve the phrase against the machine's local date and time, and store the resulting `YYYY-MM-DD` date.
  - Read `YYYY-MM-DD` first, with the standard library. Pass any other value to the `parsedatetime` package, with the current local date and time as the base.
  - If `parsedatetime` finds no date in the phrase, the value is invalid input. A phrase with only a time of day, such as `3pm`, is invalid input. If the phrase also has a time, store only the date.
  - These phrases must work, in upper or lower case: `today`, `tomorrow`, a weekday name such as `monday` or `mon`, `next week` and `in 3 days`. A weekday name is the first date after today on that weekday, so `monday` on a Monday is seven days later. `next week` is seven days after today.
  - A phrase with spaces is one argument, so quote it: `--due "next week"`. Without quotes, the left-over word is invalid input.
- Each list row shows ID, status, priority, due date, and title. Sort by priority first, then earliest due date with undated items last, then creation time and ID. Past-due open items appear in `--list`. `--today` matches the exact date.
- Show the todo's fields, timestamps, and full note text with `--show`. Store notes as separate entries and support Unicode and multiline text. Reject blank titles and notes.
- Write successful results to stdout and errors to stderr. Return 0 on success, including an empty list, 2 for invalid CLI input, and 1 for a missing ID, disallowed state transition, or storage failure. Mutations print the affected ID after saving. Commands run without interactive prompts.

## IDs

Generate a stable four-character lowercase base-36 ID such as `k7m2`, using Python's `secrets` module. Four characters provide 1,679,616 possible IDs. Check for collisions against every current record, including completed todos, and retry under the write lock. If allocation cannot find an available ID after a bounded number of retries, return an error. Require the full ID for lookups and accept uppercase input by normalizing it. IDs survive edits and completion. Deletion frees the ID for possible future reuse.

## JSONL storage

Resolve the data file in this order.

1. An explicit `TODO_FILE` path, if set.
2. The `.todo/todos.jsonl` at the current Git working-tree root, if it exists.
3. The personal list at `~/.todo/todos.jsonl`.

The CLI does not read the version 0.1 path `~/.local/share/todo/todos.jsonl`. Move an existing file by hand. If the home directory is a Git working-tree root, steps 2 and 3 name the same file.

Each UTF-8 line is one complete todo record. Use UTC ISO 8601 timestamps for creation, updates, completion, and notes. A due date is a date without a time zone.

```jsonl
{"id":"k7m2","title":"Renew family membership","priority":"high","due_date":"2026-11-20","status":"open","notes":[{"created_at":"2026-11-12T14:05:00Z","text":"Check family pricing first"}],"created_at":"2026-11-12T14:00:00Z","updated_at":"2026-11-12T14:05:00Z","completed_at":null}
```

- Valid statuses are `open` and `done`. `completed_at` is null for open todos. A successful change updates `updated_at`. An unchanged update or repeated completion preserves timestamps.
- A missing file represents an empty list. Create parent directories on the first write. Report malformed JSON, invalid records, and duplicate IDs with their line numbers and leave the file untouched.
- Keep one current record per todo. For a mutation, acquire an exclusive lock on a separate sibling lock file, read and validate the current file, apply the change, and write the new snapshot to a temporary file on the same filesystem. Flush and sync the temporary file before replacing the data file atomically. Hold the lock through replacement. For the initial macOS and Linux implementation, use `fcntl.flock`.
- Appending a note extends that todo's notes array. Completing a todo retains its record. Deleting a todo removes its record from the next snapshot.

## Acceptance checks

1. Create a todo with only a title and verify the returned ID, normal priority, empty notes, and absence of a due date.
2. Update the title, priority, and due date together. Append two notes and verify their order and content after restarting the CLI. Clear the due date and verify the remaining fields.
3. Create todos due yesterday, today, tomorrow, and with no due date. Verify priority ordering, due-date tie-breaking, and exact-date filtering across the local midnight boundary.
4. Complete a todo and verify it disappears from `--list` and `--today`, remains visible via `--show` and `--list --all`, and keeps its original completion timestamp on repeated completion. Delete it and verify lookup fails.
5. Force an ID collision and verify allocation retries. Run two writers against the same file and verify both changes persist. Simulate a failed write before replacement and verify the original data remains readable.
6. Verify that invalid dates, empty notes, unknown IDs, and malformed records return errors without changing stored data.
7. With a fixed local date, verify `today`, `tomorrow`, each weekday name in full and short form, `next week` and `in 3 days`, in upper and lower case. On a Monday, verify that `monday` and `next week` give the date seven days later. On a Sunday, verify that `next week` gives the next Sunday. Verify that `3pm`, a misspelled phrase such as `nxet week`, and `--due next week` without quotes return 2 and store nothing.
8. Verify that `todo Buy milk` creates a todo titled "Buy milk", that `todo` alone lists, and that `todo list` returns 2 with a hint. Verify that words together with an action flag, and words after an option, return 2 and store nothing.
9. Set `HOME` to a temporary directory. Verify that the first write outside a repository creates `~/.todo/todos.jsonl`. Verify that the CLI does not read a file at `~/.local/share/todo/todos.jsonl`.
10. Verify that `todo --version` prints `todo 0.2` on stdout and returns 0, and that `--version` with any other argument returns 2.


## Git repository lists

- `todo --init` requires an existing Git working tree, including a newly created repository with no commits. Run it from the root or a subdirectory. Create the `.todo/` directory and an empty `.todo/todos.jsonl` under the root if missing, validate it if present, and print its path. Initialization always targets the repository, regardless of `TODO_FILE`. It leaves staging and committing to the user.
- Write repository lock and temporary files under the worktree's Git metadata directory to keep them out of Git status. Use a same-filesystem temporary file for atomic replacement. If the metadata directory is on a different filesystem, use a temporary file inside `.todo/` beside `todos.jsonl` and exclude its reserved filename pattern locally through Git's exclude file.
- Serialize records by ID, use a fixed key order, and end every record with a newline. Updating a single todo changes its line. Keep display sorting independent of storage order.
- Use normal Git commits, branches, and merges to share the file. Concurrent edits to the same todo or nearby lines may require manual resolution. Preserve intended notes and field changes when resolving conflicts. Duplicate IDs can arise when branches independently allocate the same short ID. Resolve by giving one record a new unused ID and updating any references to it. The CLI reports conflict markers and duplicate IDs as storage errors until resolved.
- Deletion removes the active record. Earlier Git commits can retain the todo and its notes.

### Git acceptance checks

1. Run `git init` in an empty directory, then run `todo --init` from a subdirectory. Verify `.todo/todos.jsonl` is created under the root’s `.todo/` directory and remains unstaged. Add a todo and rerun initialization to verify it survives.
2. Verify commands in the repository use its list and commands outside use the personal list. Verify `TODO_FILE` overrides both for normal commands.
3. Stage and commit the list, edit one todo, and verify its record is the only changed line. Verify lock and temporary files stay out of Git status.
4. Introduce conflict markers or duplicate IDs and verify the CLI reports the affected lines without rewriting the file.
