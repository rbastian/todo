# Python Todo CLI Specification

Version 0.1

## Scope

Build a Python CLI with a personal list and optional Git-tracked repository lists for capturing, prioritizing, updating, and completing todos. Install it as the `todo` command. Use `argparse` for parsing and JSONL for persistence.

## Commands

Use one action flag per invocation. `todo` alone is shorthand for `todo --list`.

| Command | Behavior |
| --- | --- |
| `todo --init` | Initialize a todo list at `.todo/todos.jsonl` under the current Git working-tree root. Repeating the command preserves existing todos. |
| `todo --open "Renew club membership"` | Create an open todo and print its ID. Accept optional `--priority`, `--due`, and `--note`. |
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

### Input and output

- Require a nonblank title on creation. A todo starts with priority `normal`, no due date, and zero notes unless supplied.
- Accept priorities `high`, `normal`, and `low`. Accept due dates as valid calendar dates in `YYYY-MM-DD` format or the literal `today`, resolved at command execution.
- Each list row shows ID, status, priority, due date, and title. Sort by priority first, then earliest due date with undated items last, then creation time and ID. Past-due open items appear in `--list`. `--today` matches the exact date.
- Show the todo's fields, timestamps, and full note text with `--show`. Store notes as separate entries and support Unicode and multiline text. Reject blank titles and notes.
- Write successful results to stdout and errors to stderr. Return 0 on success, including an empty list, 2 for invalid CLI input, and 1 for a missing ID, disallowed state transition, or storage failure. Mutations print the affected ID after saving. Commands run without interactive prompts.

## IDs

Generate a stable four-character lowercase base-36 ID such as `k7m2`, using Python's `secrets` module. Four characters provide 1,679,616 possible IDs. Check for collisions against every current record, including completed todos, and retry under the write lock. If allocation cannot find an available ID after a bounded number of retries, return an error. Require the full ID for lookups and accept uppercase input by normalizing it. IDs survive edits and completion. Deletion frees the ID for possible future reuse.

## JSONL storage

Resolve the data file in this order.

1. An explicit `TODO_FILE` path, if set.
2. The `.todo/todos.jsonl` at the current Git working-tree root, if it exists.
3. The personal list at `~/.local/share/todo/todos.jsonl`.

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
