# Project

This section gives the facts about this project. The prompt above refers to it as "the Project section". The prompt also uses names that this section defines, for example "the gate", `$PRIMARY` and `$CODER`.

## What the project is

`todo` is a Python command-line todo list for one person. It keeps a personal list at `~/.local/share/todo/todos.jsonl` and optional repository lists at `.todo/todos.jsonl` that the user shares through normal Git commits. The user captures, prioritizes, updates, completes and deletes todos with one action flag per command, for example `todo --open`, `todo --list` and `todo --done`. `Todo-CLI-Spec-v0.1.md` in the repository root is the specification. When a bead and the specification disagree, the specification wins, and the agent escalates to `$HUMAN`.

The loop must not change `Todo-CLI-Spec-v0.1.md`. Only `$HUMAN` changes the specification. Tests must not write to the real personal list in `~/.local/share/todo/`: set `TODO_FILE` or `HOME` to a pytest temporary directory.

## Roles

```bash
CODER=Ralph
REVIEWER=Alice
HUMAN=robert.bastian
CODER_ENGINE=claude
CODER_MODEL=
REVIEWER_ENGINE=claude
REVIEWER_MODEL=
BEAD_PREFIX=todo
```

`$CODER` is the agent that implements beads. `$REVIEWER` is the agent that reviews them. Each name is the bead assignee that the agent's loop polls for. `$HUMAN` is the bd assignee for the human's queues: merge approval, acceptance and escalation. `BEAD_PREFIX` is the prefix of every bead ID in this project, and the watchers match bead IDs with it.

`CODER_ENGINE` is the CLI that the coder's loop runs on: `claude` or `fw`. The coder needs Claude Code, because its prompt uses the `/simplify` skill, and `fw` is a launcher that runs Claude Code on a Fireworks model. `CODER_MODEL` is the coder's model for its engine. `REVIEWER_ENGINE` is the CLI that the reviewer's loop runs on: `claude`, `codex` or `fw`. `REVIEWER_MODEL` is the reviewer's model for its engine. An empty model gives the default of the engine: `claude-opus-5-5` for the coder on `claude`, `claude-sonnet-5-5` for the reviewer on `claude`, and the plan's default model on `codex`. `fw` has no default, so an agent on `fw` must have a model, for example `deepseek-flash-latest[1m]`.

## Ship mode

```bash
SHIP_MODE=pr
GITHUB_ACCOUNT=rbastian
```

`SHIP_MODE` is `pr` or `local`. `pr` is the default. `$CODER` pushes each branch to `origin` and opens a pull request against `main`. `$REVIEWER` reviews the branch and comments on the pull request. `$HUMAN` approves and merges it. `$BASE` is `origin/main`.

`local` is only for a repository with one user and no shared remote. `$REVIEWER` merges the branch into `main` in the primary checkout, and no pull request exists. `$BASE` is `main`.

`GITHUB_ACCOUNT` is the GitHub account that `$CODER` and `$REVIEWER` use in `pr` mode, for every `gh` command and every push. It is usually `$HUMAN`'s own account. The agents do not use separate bot accounts. `run.sh` pins this account at start: it exports the account's token as `GH_TOKEN`, and it checks that an SSH push uses the same account. In `local` mode the field is empty.

### Remote actions in `pr` mode

This list is the only place that says what each role can do on the GitHub remote. An action that is not on the list is not allowed.

- `$CODER` can push its own `feat/` branches to `origin`. After a rebase, it pushes with `git push --force-with-lease`, never with a plain `--force`. It can open a pull request against `main` with `gh pr create`, and it can read pull requests with `gh pr view`.
- `$REVIEWER` can read pull requests with `gh pr view`, and it can post comments with `gh pr comment`. It does not push, approve, merge, close, edit or label a pull request on GitHub, and it does not delete branches. A GitHub account cannot approve its own pull request, so the review result is a comment, not `gh pr review --approve`.
- `$HUMAN` approves and merges pull requests, deletes remote branches, and changes anything else on the remote, for example the settings, the releases and the tags.
- No agent pushes to `main`, creates or deletes tags or releases, changes the repository settings, or closes a pull request that another author opened.

## Paths

```bash
PRIMARY=/Users/robert.bastian/github.com/todo
CODER_WORKTREE=/Users/robert.bastian/github.com/todo-ralph
REVIEWER_WORKTREE=/Users/robert.bastian/github.com/todo-alice
```

`PRIMARY` is the primary checkout, which belongs to `$HUMAN`. `CODER_WORKTREE` is the worktree of `$CODER`. `REVIEWER_WORKTREE` is the worktree of `$REVIEWER`.

There are no exceptions to the rule that agents do not write in the primary checkout.

## Tech stack

- Python 3.12 or later, run through `uv`. The runtime uses only the standard library: `argparse`, `json`, `secrets`, `fcntl`, `os`, `tempfile`, `datetime` and `subprocess` for Git. Do not add runtime dependencies. The CLI installs as the `todo` command through a `[project.scripts]` entry and a build backend in `pyproject.toml`.
- pytest for tests and ruff for format and lint. Tests run the CLI in a subprocess or through its `main(argv)` function, and they cover each acceptance check in the specification.
- Git, for the repository lists. The code calls the `git` CLI to find the working-tree root and the Git metadata directory. The target platforms are macOS and Linux, because the lock uses `fcntl.flock`.

## Key directories

- `src/`: the `todo` package (`src/todo/`), which holds the argparse CLI, the JSONL storage with locking and atomic replacement, ID allocation and the data-file resolution. The first bead that adds code creates this package.
- `tests/`: the pytest suite.
- `.loop/`: the loop scripts and prompts.

## Format commands and gate

Format commands:

```bash
uv run ruff format . && uv run ruff check --fix .
```

The gate:

```bash
uv run ruff format --check . && uv run ruff check . && uv run pytest -q
```

## External services

None. The CLI reads the `TODO_FILE` environment variable, which overrides the data file path. If a bead needs a service and it is unavailable, follow "When Things Go Wrong".

## Review method (for the reviewer)

The reviewer always runs the default method for its engine. List here only the checks that add to it, chosen by the files that the branch changes.

- For each change, compare the behavior with `Todo-CLI-Spec-v0.1.md`: exit codes (0, 1 and 2), stdout for results and stderr for errors, and the exact list sort order.
- For a storage change, make sure that every mutation holds the `fcntl.flock` lock through the atomic replace, flushes and syncs the temporary file, and leaves the data file untouched on any error.
- For a storage change, make sure that records keep a fixed key order, sorted by ID, with a newline at the end of every record.
- For a date or time change, make sure that timestamps are UTC ISO 8601 and that `today` and `--today` use the local calendar date.
- For a Git list change, make sure that lock and temporary files stay out of `git status`.
- Make sure that no runtime dependency was added to `pyproject.toml`.
