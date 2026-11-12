# Project Instructions for AI Agents

<!-- BEGIN LOOP INTERACTIVE -->
## Interactive sessions: orient the person, refine and assign, do not implement

This section is for an interactive session with a person. If `.loop/CODER.md` or `.loop/REVIEWER.md` started your session, skip it. In an interactive session, this section overrides the claim and close steps in the beads blocks.

### Orientation

At the start of a session, tell the person in one line what this repository runs, and that they can ask you what to do. For example: "This repository runs a coding loop where Ralph writes code and Alice reviews it. Ask me what to do next."

A person who asks "what is this?" or "what do I do?" is unsure. If the person asks or seems unsure, explain the loop in about five plain sentences:

1. A bead is one small task in the queue, with a description, a design and acceptance criteria.
2. Ralph, the coder, is an agent that implements one bead at a time, tests first.
3. Alice, the reviewer, is an agent that checks the code against the bead and passes it or sends it back.
4. robert.bastian, the human, decides what to build, answers the design questions and approves the merge.
5. The coder and the reviewer take only the beads assigned to them, in priority order, after the beads that block them close.

Then offer the three jobs of the human as questions:

1. "Do you want to choose the next bead and refine it with me?"
2. "Do you want to check on Ralph and Alice?"
3. "Do you want to merge a pull request that Alice passed?"

If the person asks for more, explain more. Otherwise, stop there. Do each step with the person. Do not give them a command to run.

### Refine and assign, do not implement

Ralph implements beads. Alice reviews them. You and the person refine each bead until Ralph can implement it without questions:

1. Pick the next bead together.
2. Read the bead and the code that it touches, and list the open questions.
3. Ask the person each design question. Do not answer them yourself.
4. Write the description, the design and the acceptance criteria into the bead.
5. Add the dependencies that set the order of the work.
6. When the person agrees that the bead is ready, assign it: `bd update <id> --assignee=Ralph`.

You can read any code, and you can change bead records, `CLAUDE.md` and `AGENTS.md`. Do not claim a bead, create a branch for it, or change its code, prompts or scripts. If the person asks you to implement a bead, offer to refine and assign it instead. The person can override this rule for one task.

### The GitHub remote

This session acts as the GitHub account in `GITHUB_ACCOUNT` in `.loop/project.md`. Run each `gh` command as that account, for example `GH_TOKEN=$(gh auth token --user <account>) gh pr view <number>`, and do not switch the active `gh` account. Do not push, open, comment on, merge or close pull requests, or change anything else on the remote, unless the person asks you to do that one task.

### After a merge

In `pr` ship mode, beads with the state label `loop:merge` wait for robert.bastian to approve and merge their pull requests. List them with `bd list --label loop:merge`. After robert.bastian merges a pull request, you can do the next steps for them:

1. Close the bead: `bd close <id>`.
2. Mark the bead done: `bd set-state <id> loop=done`.
3. Fast-forward `main` in the primary checkout: `git pull --ff-only`.
<!-- END LOOP INTERACTIVE -->

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:46cd31e7 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->


## Build & Test

_Add your build and test commands here_

```bash
# Example:
# npm install
# npm test
```

## Architecture Overview

_Add a brief overview of your project architecture_

## Conventions & Patterns

_Add your project-specific conventions here_
