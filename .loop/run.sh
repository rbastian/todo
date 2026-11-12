#!/bin/bash
# Reusable agent loop. Run from the target repo (or worktree).
# Usage: run.sh [AGENT_NAME] [PROMPT_FILE]
#   AGENT_NAME  — bd assignee filter (default: Ralph)
#   PROMPT_FILE — prompt file path, relative to cwd (default: CODER.md)
#
# Optional switches (default OFF — set to any non-empty value to enable):
#   LOOP_STREAM_LOG=1    Stream tool calls + assistant turns as JSONL.
#                        Tees claude output to $LOOP_LOG_DIR/<timestamp>.jsonl
#                        (default $LOOP_LOG_DIR=/tmp/loop-sessions) and maintains
#                        a "current.jsonl" symlink for tail -f. After each
#                        session, also appends a "Session cost:" note to the
#                        bead (needs uv, which runs watch.py --summary).
#   LOOP_SHOW_BEAD=1     For each in-progress bead, also run `bd show <id>` so
#                        notes/checkpoint/gate-output are visible between sessions.
#   LOOP_KEEP_AWAKE=1    Run each session under `caffeinate -i`, so the Mac does
#                        not idle-sleep during a session. Without caffeinate on
#                        PATH, the loop warns once and runs without it.
#   LOOP_SESSION_TIMEOUT Max wall-clock time per session (default: 2h): a whole
#                        number with an optional suffix s, m, h or d. 0 means no
#                        limit. Time while the Mac sleeps counts too.
#   LOOP_ENGINE          Which agent CLI runs each session: claude (default),
#                        codex, or fw (Claude Code on a Fireworks model, through
#                        the fw launcher). Any other value stops the loop at
#                        startup.
#   LOOP_MODEL           Model for each session. Defaults to claude-opus-5-5 on
#                        the claude engine; on codex it has no default, and an
#                        unset one means the default model of the ChatGPT plan.
#                        fw needs it, for example deepseek-flash-latest[1m].
#   LOOP_OVERLAY         Project overlay appended to the prompt (default:
#                        project.md beside PROMPT_FILE; "none" disables it).
#   LOOP_PRINT_PROMPT=1  Print the combined prompt and exit.
#
# GitHub account (pr mode only, when the overlay says SHIP_MODE=pr):
#   The overlay must name GITHUB_ACCOUNT, the account that the agents use.
#   At start, run.sh gets that account's token with gh auth token --user and
#   exports it as GH_TOKEN for every session, whichever gh account is active.
#   If origin is an SSH URL, it runs ssh -T git@<host> once and stops unless
#   the reply greets the same account. An HTTPS origin needs no SSH check.
#   Any failure stops the loop with exit 1 before the first session.
set -euo pipefail

# Exit on Ctrl-C during startup and pauses as well as during an agent session.
trap 'exit 130' INT

AGENT_NAME="${1:-Ralph}"
PROMPT_FILE="${2:-CODER.md}"
SESSION_TIMEOUT="${LOOP_SESSION_TIMEOUT:-2h}"
ENGINE="${LOOP_ENGINE:-claude}"

# The engine picks the command and the model default. Reviewing on a second
# model family is the point of the codex engine, so it gets no default model:
# an unset LOOP_MODEL there means whatever the ChatGPT plan runs by default.
case "$ENGINE" in
    claude) MODEL="${LOOP_MODEL:-claude-opus-5-5}" ;;
    codex) MODEL="${LOOP_MODEL:-}" ;;
    fw)
        MODEL="${LOOP_MODEL:-}"
        [ -n "$MODEL" ] || { echo "ERROR: LOOP_ENGINE=fw needs LOOP_MODEL, for example deepseek-flash-latest[1m]"; exit 1; }
        ;;
    *)
        echo "ERROR: LOOP_ENGINE must be claude, codex or fw (got: $ENGINE)"
        exit 1
        ;;
esac

# Seconds in a duration such as 90, 90s, 30m, 2h or 1d: the forms GNU timeout
# accepted, apart from fractions. Fails on anything else.
duration_seconds() {
    local num="${1%[smhd]}" mult=1
    case "$num" in '' | *[!0-9]*) return 1 ;; esac
    case "$1" in
        *m) mult=60 ;;
        *h) mult=3600 ;;
        *d) mult=86400 ;;
    esac
    echo $((10#$num * mult))
}

if ! SESSION_LIMIT=$(duration_seconds "$SESSION_TIMEOUT"); then
    echo "ERROR: LOOP_SESSION_TIMEOUT must be a whole number with an optional suffix s, m, h or d (got: $SESSION_TIMEOUT)"
    exit 1
fi

# The agent reads this to choose its engine-specific steps: on codex, the reviewer
# reviews with `codex exec review` instead of the /code-review skill.
export LOOP_ENGINE="$ENGINE"

# bd resolves its actor from $BEADS_ACTOR (then git user.name, then $USER).
# The actor must match the bead's assignee or `bd update --claim` refuses
# ("already claimed by Ralph"), and it attributes the audit trail correctly.
export BEADS_ACTOR="$AGENT_NAME"

if [ ! -f "$PROMPT_FILE" ]; then
    echo "ERROR: $PROMPT_FILE not found in $(pwd)"
    echo "Hint: cd into the target repo (or worktree) before running, or pass an absolute path as arg 2."
    exit 1
fi

# The overlay defaults to project.md beside the prompt file. LOOP_OVERLAY
# overrides the path; LOOP_OVERLAY=none runs the base prompt alone.
OVERLAY_FILE="${LOOP_OVERLAY:-$(dirname "$PROMPT_FILE")/project.md}"
if [ "$OVERLAY_FILE" = "none" ]; then
    OVERLAY_FILE=""
elif [ ! -f "$OVERLAY_FILE" ]; then
    echo "ERROR: project overlay $OVERLAY_FILE not found"
    echo "Hint: create it, point LOOP_OVERLAY at it, or set LOOP_OVERLAY=none."
    exit 1
fi

# LOOP_PRINT_PROMPT=1 prints the combined prompt and exits, to check an overlay.
if [ -n "${LOOP_PRINT_PROMPT:-}" ]; then
    cat "$PROMPT_FILE"
    if [ -n "$OVERLAY_FILE" ]; then printf '\n\n'; cat "$OVERLAY_FILE"; fi
    exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: jq is required (brew install jq)"
    exit 1
fi

# fw starts Claude Code against the Fireworks endpoint. Its --check makes
# sure that the key and the model are set up, calls no model and costs no
# tokens. Its output is discarded when it passes.
if [ "$ENGINE" = "fw" ]; then
    if ! command -v fw >/dev/null 2>&1; then
        echo "ERROR: LOOP_ENGINE=fw needs fw on PATH (usually ~/.local/bin/fw)"
        exit 1
    fi
    if ! FW_ERROR=$(fw --model "$MODEL" --check 2>&1 >/dev/null); then
        echo "ERROR: fw --model $MODEL --check failed:"
        echo "$FW_ERROR"
        exit 1
    fi
fi

KEEP_AWAKE=""
if [ -n "${LOOP_KEEP_AWAKE:-}" ]; then
    if command -v caffeinate >/dev/null 2>&1; then
        KEEP_AWAKE=1
    else
        echo "WARNING: caffeinate not found; LOOP_KEEP_AWAKE has no effect"
    fi
fi

# Count beads via JSON rather than scraping human-readable output, which
# breaks silently if bd rewords its messages. Prints -1 when bd itself fails
# (e.g. embedded Dolt is single-writer, so a concurrent interactive bd
# session can hold the lock) so callers can tell "no work" from "bd down".
bead_count() {
    local json
    json=$("$@" --json 2>/dev/null) || { echo -1; return 0; }
    jq 'length' <<<"$json" 2>/dev/null || echo -1
}

# A killed or failed sleep must not end the loop under set -e (lpe-qi4).
pause() { sleep "$1" || true; }

# Each session runs in its own process group, so that the watchdog and Ctrl-C
# stop the agent and every child it started, and nothing else. GNU timeout
# counted only awake time on macOS, and a session that started before the Mac
# slept ran 12h52m against a 2h limit (lpe-k08). The watchdog compares the
# wall clock with the start instead, so its first check after a sleep sees the
# real elapsed time.

# Send INT to a process group, then KILL if it still exists after $2 seconds.
stop_group() {
    local pgid="$1" grace="$2" waited=0
    kill -INT -- "-$pgid" 2>/dev/null || return 0
    while [ "$waited" -lt "$grace" ] && kill -0 -- "-$pgid" 2>/dev/null; do
        pause 1
        waited=$((waited + 1))
    done
    kill -KILL -- "-$pgid" 2>/dev/null || true
}

session_elapsed() { echo $(($(date +%s) - SESSION_START)); }

# Runs as its own job. Exits 124 when it stopped the session. From that point
# it ignores TERM, so run_session's TERM stops it only while it still waits.
watchdog() {
    local left
    while kill -0 -- "-$SESSION_PGID" 2>/dev/null; do
        left=$((SESSION_LIMIT - $(session_elapsed)))
        if [ "$left" -le 0 ]; then
            trap '' TERM
            stop_group "$SESSION_PGID" 60
            exit 124
        fi
        pause $((left < 5 ? left : 5))
    done
}

# Ctrl-C reaches run.sh but not the session's own process group, so pass it on.
interrupt_session() {
    trap - INT TERM HUP
    kill -KILL -- "-$WATCHDOG_PGID" 2>/dev/null || true
    stop_group "$SESSION_PGID" 10
    exit 130
}

# Runs AGENT_CMD under the watchdog, teeing to $SESSION_LOG when it is set.
# Sets SESSION_EXIT: the agent's exit code, or 124 when the limit passed.
run_session() {
    SESSION_START=$(date +%s)
    # set -m gives each background job its own process group, led by $!.
    set -m
    if [ -n "$SESSION_LOG" ]; then
        ("${AGENT_CMD[@]}" </dev/null | tee -a "$SESSION_LOG"; exit "${PIPESTATUS[0]}") &
    else
        "${AGENT_CMD[@]}" </dev/null &
    fi
    SESSION_PGID=$!
    WATCHDOG_PGID=""
    if [ "$SESSION_LIMIT" -gt 0 ]; then
        watchdog &
        WATCHDOG_PGID=$!
    fi
    set +m
    trap interrupt_session INT TERM HUP
    wait "$SESSION_PGID" 2>/dev/null
    SESSION_EXIT=$?
    trap 'exit 130' INT
    trap - TERM HUP
    [ -n "$WATCHDOG_PGID" ] || return 0
    # A watchdog that fired finishes its KILL grace period and exits 124.
    kill -TERM -- "-$WATCHDOG_PGID" 2>/dev/null
    wait "$WATCHDOG_PGID" 2>/dev/null
    [ "$?" -ne 124 ] || SESSION_EXIT=124
}

# watch.py sits beside this script, in the primary checkout's .loop/ folder.
HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Append the finished session's cost to its bead. The agent cannot do this:
# the cost arrives in the closing event, after the agent has exited. watch.py
# --summary reads the bead and the cost back out of the log. A resumed session
# may never claim, tag or review, so the bead falls back to the one bead the
# agent held when the session started. Every failure only warns: a missing
# cost note must never stop the loop.
record_cost() {
    local log="$1" held="$2" summary bead note ids attempt prefix watch_args
    if ! command -v uv >/dev/null 2>&1; then
        echo "WARNING: uv not found; no cost note for this session"
        return 0
    fi
    # Without a prefix, watch.py falls back to a pattern that matches any.
    prefix=$(overlay_value BEAD_PREFIX)
    watch_args=(--summary "$log" --agent "$AGENT_NAME" ${prefix:+--prefix "$prefix"})
    if ! summary=$(uv run --quiet "$HARNESS_DIR/watch.py" "${watch_args[@]}" 2>/dev/null); then
        echo "WARNING: could not read $log; no cost note for this session"
        return 0
    fi
    note=$(jq -r '.note // empty' <<<"$summary" 2>/dev/null) || note=""
    bead=$(jq -r '.bead // empty' <<<"$summary" 2>/dev/null) || bead=""
    if [ -z "$note" ]; then
        echo "WARNING: the session did not finish; no cost note"
        return 0
    fi
    if [ -z "$bead" ]; then
        read -ra ids <<<"$held"
        if [ "${#ids[@]}" -eq 1 ]; then
            bead="${ids[0]}"
        else
            echo "WARNING: no bead found for this session; no cost note: $note"
            return 0
        fi
    fi
    # bd is single-writer, so a human's interactive session can hold the lock.
    for attempt in 1 2 3; do
        if bd note "$bead" "$note" >/dev/null 2>&1; then
            echo "Cost note on $bead: $note"
            return 0
        fi
        if [ "$attempt" -lt 3 ]; then pause 10; fi
    done
    echo "WARNING: bd note failed on $bead; no cost note: $note"
    return 0
}

# A KEY=value line from the overlay, the same way worktrees.sh reads it.
# Prints nothing when there is no overlay or no such line.
overlay_value() {
    [ -n "$OVERLAY_FILE" ] || return 0
    sed -n "s/^$1=//p" "$OVERLAY_FILE" | head -1
}

# In local mode the reviewer merges into the primary checkout, and REVIEWER.md
# exits without a merge when the primary is not on main or has modified or
# staged tracked files outside .beads/. Starting a session then only burns
# money, so the loop waits. Prints the reason when busy; prints nothing and
# fails when the reviewer may run (also for the coder, pr mode, no overlay).
primary_busy_reason() {
    local primary branch
    [ "$(overlay_value SHIP_MODE)" = "local" ] || return 1
    [ "$AGENT_NAME" = "$(overlay_value REVIEWER)" ] || return 1
    primary=$(overlay_value PRIMARY)
    [ -n "$primary" ] || return 1
    branch=$(git -C "$primary" rev-parse --abbrev-ref HEAD 2>/dev/null) || branch="?"
    if [ "$branch" != "main" ]; then
        echo "on $branch"
    elif git -C "$primary" status --porcelain --untracked-files=no 2>/dev/null \
            | grep -v '\.beads/' | grep -q .; then
        echo "modified tracked files"
    else
        return 1
    fi
}

# In pr mode the agents act as one GitHub account: GITHUB_ACCOUNT in the
# overlay. The account that gh has active when the loop starts must not decide
# it, so pin that account's token as GH_TOKEN for every session. gh uses it,
# and so does git over HTTPS through gh's credential helper. An SSH push uses
# the SSH key instead, so check once that the key belongs to the same account.
# Every failure stops the loop before the first session. Never print the token.
pin_github_account() {
    local account url host port="" reply who=""
    [ "$(overlay_value SHIP_MODE)" = "pr" ] || return 0
    account=$(overlay_value GITHUB_ACCOUNT)
    if [ -z "$account" ]; then
        echo "ERROR: GITHUB_ACCOUNT is empty in $OVERLAY_FILE; pr mode needs the GitHub account that the agents use"
        exit 1
    fi
    if ! GH_TOKEN=$(gh auth token --user "$account" 2>/dev/null) || [ -z "$GH_TOKEN" ]; then
        echo "ERROR: gh has no token for the GitHub account $account"
        echo "Hint: run 'gh auth login' and log in as $account, then restart the loop."
        exit 1
    fi
    export GH_TOKEN
    url=$(git remote get-url origin 2>/dev/null) || url=""
    case "$url" in
        http://* | https://* | '') return 0 ;;
        ssh://*)
            host="${url#ssh://}"
            host="${host#*@}"
            host="${host%%/*}"
            ;;
        *@*:*) host="${url#*@}"; host="${host%%:*}" ;;
        *) return 0 ;;
    esac
    # Pass a port only when the URL names one: -p overrides ~/.ssh/config.
    case "$host" in
        *:*) port="${host##*:}"; host="${host%%:*}" ;;
    esac
    # ssh -T exits 1 even when GitHub greets the key, so read the reply.
    reply=$(ssh -T ${port:+-p "$port"} -o BatchMode=yes -o ConnectTimeout=10 "git@$host" 2>&1 </dev/null) || true
    if [[ "$reply" =~ Hi\ ([^!]*)! ]]; then who="${BASH_REMATCH[1]}"; fi
    if [ "$who" != "$account" ]; then
        echo "ERROR: git pushes to origin over SSH as '${who:-unknown}', but GITHUB_ACCOUNT is '$account'"
        echo "ssh -T git@$host replied: $reply"
        exit 1
    fi
}
pin_github_account

# The files that update.sh installs, as paths in the loop-engineering clone:
# FILES in skills/dev-env-setup/scripts/update.sh, plus interactive.template.md,
# which update.sh renders into CLAUDE.md and AGENTS.md. Only a commit that
# changes one of them counts as drift. A test keeps this list in step with
# update.sh.
HARNESS_PATHS="loop/run.sh loop/watch.py loop/prices.json loop/CODER.md loop/REVIEWER.md loop/interactive.template.md"

# One line when the loop-engineering clone has newer commits on the installed
# files than this harness. HARNESS_VERSION beside run.sh names the clone and
# the commit that bootstrap.sh or update.sh installed. A missing stamp or
# clone, or a commit that the clone lacks, prints nothing, and no error here
# stops the loop.
# The same function is in skills/dev-env-setup/scripts/lib.sh.
drift_line() {
    local commit clone count
    commit=$(sed -n 's/^commit=//p' "$1" 2>/dev/null | head -1)
    clone=$(sed -n 's/^clone=//p' "$1" 2>/dev/null | head -1)
    [ -n "$commit" ] && [ -d "$clone" ] || return 0
    count=$(git -C "$clone" rev-list --count "$commit..HEAD" -- $HARNESS_PATHS 2>/dev/null) || return 0
    case "$count" in 0) return 0 ;; 1) count="1 commit" ;; *) count="$count commits" ;; esac
    echo "harness is $count behind loop-engineering; run update.sh"
}

# Where a session runs, in the form of Session.label in watch.py:
# <harness>[@<provider>]/<model>. A test makes sure that the two agree.
harness_label() {
    local harness
    case "$1" in
        claude) harness=cc ;;
        fw) harness=cc@fw ;;
        *) harness="$1" ;;
    esac
    if [ -n "$2" ]; then
        echo "$harness/$2"
    elif [ "$1" = codex ]; then
        echo "$harness/codex default"
    else
        echo "$harness/?"
    fi
}

# coder or reviewer, from CODER and REVIEWER in the overlay.
agent_role() {
    case "$AGENT_NAME" in
        "$(overlay_value CODER)") echo coder ;;
        "$(overlay_value REVIEWER)") echo reviewer ;;
        *) echo "unknown role" ;;
    esac
}

# An absolute path, relative to the primary checkout when it is inside it.
show_path() {
    local path="$1" primary
    case "$path" in /*) ;; *) path="$(pwd)/${path#./}" ;; esac
    primary=$(overlay_value PRIMARY)
    if [ -n "$primary" ]; then
        case "$path" in "${primary%/}"/*) path="${path#"${primary%/}"/}" ;; esac
    fi
    echo "$path"
}

# The installed commit and whether the clone has newer commits on the
# installed files.
version_text() {
    local stamp="$HARNESS_DIR/HARNESS_VERSION" commit clone short count
    commit=$(sed -n 's/^commit=//p' "$stamp" 2>/dev/null | head -1)
    clone=$(sed -n 's/^clone=//p' "$stamp" 2>/dev/null | head -1)
    if [ -z "$commit" ]; then
        echo unknown
        return 0
    fi
    short=$(git -C "$clone" rev-parse --short "$commit" 2>/dev/null) || short="${commit:0:7}"
    # drift_line owns the warning text, so it runs only when there is drift.
    count=$(git -C "$clone" rev-list --count "$commit..HEAD" -- $HARNESS_PATHS 2>/dev/null) || count=""
    case "$count" in
        '') echo "$short" ;;
        0) echo "$short (up to date)" ;;
        *) echo "$short ($(drift_line "$stamp" 2>/dev/null))" ;;
    esac
}

ROLE=$(agent_role)
LABEL=$(harness_label "$ENGINE" "$MODEL")

# The configuration, once, after every startup check passed. Never a token.
print_startup_block() {
    local ship prompt logs timeout
    ship=$(overlay_value SHIP_MODE)
    case "$ship" in
        pr) ship="pr, as $(overlay_value GITHUB_ACCOUNT)" ;;
        '') ship=unknown ;;
    esac
    prompt="no overlay"
    [ -z "$OVERLAY_FILE" ] || prompt=$(show_path "$OVERLAY_FILE")
    prompt="$(show_path "$PROMPT_FILE") + $prompt"
    logs=off
    if [ -n "${LOOP_STREAM_LOG:-}" ]; then
        logs=$(show_path "${LOOP_LOG_DIR:-/tmp/loop-sessions}")
    fi
    timeout="$SESSION_TIMEOUT"
    [ "$SESSION_LIMIT" -gt 0 ] || timeout=none
    echo "=== $AGENT_NAME ($ROLE) ==="
    printf '%-9s%s\n' harness "$LABEL" ship "$ship" prompt "$prompt" \
        logs "$logs" timeout "$timeout" version "$(version_text)"
    echo ""
}
print_startup_block

# The busy reason printed last, so a wait prints its reason once, not every 30s.
LAST_BUSY=""

while true; do

    # Re-read $PROMPT_FILE each iteration so changes take effect without restart
    PROMPT=$(cat "$PROMPT_FILE")
    # Project overlay: the base prompt holds the loop protocol, the overlay
    # holds the project facts (context, paths, gate, review method). Re-read
    # each iteration like the base.
    if [ -n "$OVERLAY_FILE" ]; then
        PROMPT+=$'\n\n'"$(cat "$OVERLAY_FILE")"
    fi

    # Show both in_progress (agent's claimed work) and ready (new work) beads.
    # Agent resumes in_progress first per its prompt's Phase 0; both lists are
    # shown here for operator visibility.
    IN_PROGRESS_COUNT=$(bead_count bd list --assignee="$AGENT_NAME" --status=in_progress)
    READY_COUNT=$(bead_count bd ready --assignee="$AGENT_NAME")

    if [ "$IN_PROGRESS_COUNT" -lt 0 ] || [ "$READY_COUNT" -lt 0 ]; then
        echo "WARNING: bd unavailable (db locked or erroring); retrying in 30s"
        pause 30
        continue
    fi

    if [ "$IN_PROGRESS_COUNT" -eq 0 ] && [ "$READY_COUNT" -eq 0 ]; then
        echo "No beads for $AGENT_NAME. Zzzzzz..."
        pause 30
        continue
    fi

    # After the bead counts, so that "No beads" still prints with no work.
    if BUSY=$(primary_busy_reason); then
        if [ "$BUSY" != "$LAST_BUSY" ]; then
            echo "primary checkout busy: $BUSY; waiting to start $AGENT_NAME"
            LAST_BUSY="$BUSY"
        fi
        pause 30
        continue
    fi
    LAST_BUSY=""

    if [ "$IN_PROGRESS_COUNT" -gt 0 ]; then
        echo "In-progress beads (claimed, not finished):"
        bd list --assignee="$AGENT_NAME" --status=in_progress 2>&1 || true
        echo ""
        if [ -n "${LOOP_SHOW_BEAD:-}" ]; then
            for BEAD_ID in $(bd list --assignee="$AGENT_NAME" --status=in_progress --json 2>/dev/null \
                              | jq -r '.[].id // empty' 2>/dev/null | head -5); do
                echo "--- bd show $BEAD_ID ---"
                bd show "$BEAD_ID" 2>&1 | grep -v '^Warning:' || true
                echo ""
            done
        fi
    fi
    if [ "$READY_COUNT" -gt 0 ]; then
        echo "Ready beads (unclaimed, no blockers):"
        bd ready --assignee="$AGENT_NAME" 2>&1 || true
        echo ""
    fi

    # The beads this agent holds as the session starts: the fallback for a
    # session whose log names no bead (see record_cost).
    HELD=$(bd list --assignee="$AGENT_NAME" --status=in_progress --json 2>/dev/null \
               | jq -r '.[].id // empty' 2>/dev/null | tr '\n' ' ') || HELD=""

    echo ""
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') === $AGENT_NAME ($ROLE) on $LABEL: session starts ==="
    echo "=================================================="

    # Built here rather than at startup: $PROMPT is re-read each iteration.
    # One array for both call sites below. claude needs two flags to emit the
    # JSONL that codex exec --json emits on its own, so only it reads
    # $LOOP_STREAM_LOG here. fw runs Claude Code and passes everything after
    # -- to it, but it sets the model itself and rejects --model there.
    if [ "$ENGINE" = "codex" ]; then
        AGENT_CMD=(codex exec --json --dangerously-bypass-approvals-and-sandbox)
        if [ -n "$MODEL" ]; then
            AGENT_CMD+=(-m "$MODEL")
        fi
        AGENT_CMD+=("$PROMPT")
    else
        if [ "$ENGINE" = "fw" ]; then
            AGENT_CMD=(fw --model "$MODEL" -- -p "$PROMPT")
        else
            AGENT_CMD=(claude -p "$PROMPT" --model "$MODEL")
        fi
        AGENT_CMD+=(--dangerously-skip-permissions)
        if [ -n "${LOOP_STREAM_LOG:-}" ]; then
            AGENT_CMD+=(--verbose --output-format stream-json)
        fi
    fi
    if [ -n "$KEEP_AWAKE" ]; then
        AGENT_CMD=(caffeinate -i "${AGENT_CMD[@]}")
    fi

    # Capture the session's exit code explicitly: under set -e a bare failing
    # command would kill the whole loop, turning one bad session (API blip,
    # rate limit, timeout) into a dead daemon.
    set +e
    SESSION_LOG=""
    if [ -n "${LOOP_STREAM_LOG:-}" ]; then
        LOG_DIR="${LOOP_LOG_DIR:-/tmp/loop-sessions}"
        mkdir -p "$LOG_DIR"
        SESSION_LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S).jsonl"
        ln -sf "$SESSION_LOG" "$LOG_DIR/current.jsonl"
        echo "Stream log: $SESSION_LOG (tail -f $LOG_DIR/current.jsonl)"
        # watch.py reads this first line for the engine, the model and the
        # start time: codex exec names no model in its own JSON even when -m
        # was passed, and its events carry no timestamps at all. jq builds it
        # so the JSON stays valid whatever the values hold, and the session is
        # APPENDED below — a plain `tee` would truncate this line away.
        jq -nc --arg engine "$ENGINE" --arg model "$MODEL" \
            --arg started "$(date +%Y-%m-%dT%H:%M:%S%z)" \
            '{type:"loop.session",engine:$engine,model:$model,started:$started}' \
            >"$SESSION_LOG"
    fi
    run_session
    set -e

    if [ "$SESSION_EXIT" -eq 124 ]; then
        echo "$ENGINE session timed out after $SESSION_TIMEOUT"
    else
        echo "$ENGINE exited with $SESSION_EXIT"
    fi
    if [ -n "$SESSION_LOG" ]; then
        record_cost "$SESSION_LOG" "$HELD"
    fi
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') === Session ended ==="
    echo ""

    pause 30
done
