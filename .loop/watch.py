#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["rich"]
# ///
"""Human-readable live view of a Ralph or an Alice session.

`run.sh` with LOOP_STREAM_LOG=1 writes the session's events to
$LOOP_LOG_DIR/<timestamp>.jsonl and repoints a `current.jsonl` symlink at it
each iteration. Two vocabularies reach this file — Claude Code's stream-json
and `codex exec --json`, since Alice may review on either engine — and run.sh
names the coder in a `loop.session` line at the top of every log. That
stream is complete but unreadable at a glance; this turns it into one labelled
line per tool call:

    03:46:20  PHASE 0   read spec        agents-bqz.3
    03:47:02  PHASE 1   write tests      tests/test_transitions.py
    03:52:15  PHASE 2   lint             ruff check --fix .

The agent being watched comes from LOOP_LOG_DIR, which run.sh gives each
loop separately so their `current.jsonl` symlinks cannot collide, so following
Alice needs nothing but her directory.

Usage:
    uv run .loop/watch.py            # follow the live session
    uv run .loop/watch.py --tui      # the same, with a pinned header and footer
    uv run .loop/watch.py --replay   # print a finished log and exit
    uv run .loop/watch.py --file X   # a specific session log
    uv run .loop/watch.py --summary X   # one finished log as JSON (run.sh)
    uv run .loop/watch.py --prefix lpe  # match only this project's bead IDs
    LOOP_LOG_DIR=/tmp/alice-sessions uv run .loop/watch.py   # Alice
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path
from typing import ClassVar

LOG_DIR = Path(os.environ.get("LOOP_LOG_DIR", "/tmp/loop-sessions"))
# The loop being watched. Resolved once, by main, from --agent or LOG_DIR.
AGENT = "Ralph"
# The type of run.sh's first log line. Logs from before lpe-wh5 use the old one.
SESSION_KINDS = ("loop.session", "ralph.session")
POLL_SECONDS = 0.4
STATUS_SECONDS = 120
# How often the pinned footer repaints with nothing new to show. A finished
# session's age has to keep counting while the watcher waits, and it is shown
# in whole minutes, so half a minute is close enough to read as live.
REPAINT_SECONDS = 30
RULE = "\u2500" * 74
# The body line that opens a session, whichever engine wrote it.
SESSION_MARK = "\u2500\u2500 session \u2500\u2500"
# The part of a bead ID after its prefix: a hash of 3 to 8 characters (bd
# makes hashes longer as a database grows), and child numbers to any depth, as
# in lpe-3or.1.2.
HASH = r"-[a-z0-9]{3,8}(?:\.\d+)*"
# The prefix when --prefix does not name it. bd accepts prefixes such as my-app,
# my_app and app2, so the prefix takes as much as it can and the hash is the
# last segment. A hyphenated word before an ID can still be read as part of
# it, which is why the warning below asks for --prefix (lpe-7fn).
ANY_PREFIX = r"[a-z][a-z0-9_-]*"
PREFIX_WARNING = (
    "watch: no --prefix given, so bead names can be wrong;"
    " pass --prefix with the project's bead prefix"
)


def use_prefix(prefix: str | None) -> None:
    """Build the three patterns that name a bead, for one prefix or for any.

    main calls this once with --prefix. The import below sets the default, so
    the patterns exist for a caller that never runs main.
    """
    global CLAIM_RE, REVIEW_RE, TAG_RE
    bead = (re.escape(prefix) if prefix else ANY_PREFIX) + HASH
    # Ralph's claim, read from the ID argument of `bd update`. A wide hash also
    # matches hyphenated words such as re-review, so a search over the whole
    # command can return a word that comes before the ID (lpe-708).
    CLAIM_RE = re.compile(rf"bd update\s+({bead})\b")
    # Alice's first write on every review (.loop/REVIEWER.md). Matched down to
    # the marker text and the bead's position as `bd note`'s argument: she also
    # writes notes that quote OTHER beads, and those must not move the header.
    # A round that the human resumes after `Review not run` is `restarted`.
    REVIEW_RE = re.compile(
        rf"""bd note\s+({bead})\s+["']?Review round \d+ (?:re)?started"""
    )
    # Ralph's commit tag, read from between the brackets rather than from
    # anywhere in the command. A commit subject carries ordinary hyphenated
    # words — mid-log, set-up, add-on — and a bead pattern matches those too,
    # so a search over the whole command returns the first one of them
    # instead of the tag (agents-8vr).
    TAG_RE = re.compile(rf"\[({bead})\]")


use_prefix(None)
# A git commit that starts a command: at its start, or after `&&`, `;` or a
# line break. One that follows a quote or a paren is text inside a string — a
# heredoc edit of the watcher's own tests is full of them (agents-ejc).
COMMIT_START_RE = re.compile(r"(?:^|&&|;|\n)\s*git commit\b")
# The event each engine ends a session with: Claude's `result`, Codex's
# `turn.completed`. Both carry the usage the footer reports.
CLOSING_KINDS = ("result", "turn.completed")
# --replay reads a log that finished long ago. Codex events carry no clock of
# their own, so their arrival time belongs to the replay rather than to the
# session, and a plausible wrong time is worse than a blank.
REPLAYING = False


def agent_name(log_dir: Path) -> str:
    """The agent a log directory belongs to: /tmp/alice-sessions -> "Alice".

    One watcher serves both loops, and the directory is the only thing that
    already tells them apart — run.sh gives each loop its own LOOP_LOG_DIR
    precisely so their `current.jsonl` symlinks cannot collide. Anything that
    is not named for a loop is Ralph's, since his is the default.
    """
    loop = log_dir.name.removesuffix("-sessions")
    # removesuffix hands back the whole name when the suffix is absent, so this
    # catches both "not named for a loop" and a directory called just
    # `-sessions`. Either way it is Ralph's: his is run.sh's default. So is
    # /tmp/loop-sessions, run.sh's default folder.
    if loop in ("", "loop", log_dir.name):
        return "Ralph"
    return loop.capitalize()


def bead_in(command: str) -> str | None:
    """The bead a command names, where it names one beyond doubt.

    Three commands qualify, one per agent-shaped moment: Ralph's claim, the tag
    on his git commit, and Alice's review-round note. Everything else that mentions a
    bead — `bd show`, a filed bug, a finding that cites another issue — is a
    guess, and a wrong bead in the header is worse than none.

    The claim is read before the tag because Ralph chains it with the writes
    that follow, and one of those may quote an unrelated bead in brackets. The
    bead he claimed is the session's, whatever else the chain goes on to name.
    """
    if found := REVIEW_RE.search(command):
        return found.group(1)
    if "--claim" in command and (found := CLAIM_RE.search(command)):
        return found.group(1)
    if (start := COMMIT_START_RE.search(command)) and (
        found := TAG_RE.search(command, start.start())
    ):
        return found.group(1)
    return None


# What each session log this watcher has followed cost, keyed by log name.
# Module level rather than on Session, which `follow` recreates for every log
# (agents-3hd), because the total has to outlive the rollover. A mapping
# rather than a running sum because one log can report its result several
# times — background tasks re-report a running total — and the last report
# must replace the session's figure instead of adding to it.
COSTS: dict = {}

# The engines with no dollar figure to trust, and what each one says in its
# place: on the session's footer, in its bead note, and in the watcher total.
# Codex on a ChatGPT plan reports no per-session figure. fw runs Claude Code on
# a Fireworks model, and Claude Code has no price for one (it warns
# unrecognized_model), so its total_cost_usd is wrong.
UNPRICED = {
    "codex": {
        "footer": "no cost (ChatGPT plan)",
        "note": "no cost reported (ChatGPT plan)",
        "total": "Codex sessions (no cost)",
    },
    "fw": {
        "footer": "cost unknown (fw)",
        "note": "cost unknown (fw)",
        "total": "fw sessions (cost unknown)",
    },
}

# The UNPRICED engines that watch.py prices itself from prices.json: the
# provider of their entries, and its name in the cost note. A session of one
# of these whose every model has a price goes into COSTS like a Claude one.
ESTIMATED = {"fw": ("fireworks", "Fireworks")}

# Per-million-token prices by model ID, for the engines whose own figure is
# wrong or missing. It sits beside watch.py, so update.sh installs both.
PRICES_PATH = Path(__file__).with_name("prices.json")
# prices.json once read, or None before the first lookup. A file that is
# missing or unreadable reads as {}, so it warns once and prices nothing.
PRICES: dict | None = None
# One trailing bracketed suffix on a model ID, such as [1m] for a long context.
MODEL_SUFFIX = re.compile(r"\[[^\[\]]*\]$")


def load_prices() -> dict:
    global PRICES
    if PRICES is None:
        try:
            prices = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
            if not isinstance(prices, dict):
                raise TypeError("not a JSON object")
        except (OSError, ValueError, TypeError) as error:
            print(
                f"watch: cannot read {PRICES_PATH} ({error}); every estimated"
                " cost is unknown",
                file=sys.stderr,
            )
            prices = {}
        PRICES = prices
    return PRICES


def estimate(
    provider: str, model: str, *, inputs=0, cached=0, written=0, output=0
) -> tuple[float, str] | None:
    """What one model's tokens cost at `provider`'s prices, and the price date.

    The counts are separate: `inputs` holds no cache reads, as Anthropic and
    Fireworks' Anthropic endpoint count it, so each token is billed once at
    its own rate. A cache write bills at cache_write, or at the input rate
    when the entry has none. None when the model, or its base ID without a
    bracketed suffix, has no entry for the provider, or the entry is
    malformed.
    """
    prices = load_prices()
    entry = prices.get(model)
    if entry is None:
        entry = prices.get(MODEL_SUFFIX.sub("", model))
    if not isinstance(entry, dict) or entry.get("provider") != provider:
        return None
    try:
        rates = [
            (inputs, entry["input"]),
            (cached, entry["cached_input"]),
            (written, entry.get("cache_write", entry["input"])),
            (output, entry["output"]),
        ]
        dollars = sum(count * rate for count, rate in rates) / 1_000_000
    except (KeyError, TypeError):
        return None
    return dollars, str(entry.get("checked", "?"))


# The sessions of an UNPRICED engine this watcher has followed: log name to
# engine. Those without a figure in COSTS are counted apart rather than stored
# there at 0, as a zero would divide the dollar total over sessions that never
# contributed to it. A mapping for the same reason COSTS is one: one log must
# count once however many times its lines are read.
UNPRICED_SESSIONS: dict = {}


def watcher_cost() -> str:
    """What the watcher has spent since it started, for the totals line.

    The account signs in through claude.ai rather than with an API key, so
    total_cost_usd is Claude Code's API-equivalent estimate and not a bill.
    Everything built on it is labelled `est.` for that reason. The sessions of
    an UNPRICED engine with no estimate are counted beside the figure, not
    inside it.
    """
    total = f"watcher ${sum(COSTS.values()):.2f} est. over {len(COSTS)} sessions"
    counts = Counter(
        engine for name, engine in UNPRICED_SESSIONS.items() if name not in COSTS
    )
    for engine, labels in UNPRICED.items():
        if counts[engine]:
            total += f" · {counts[engine]} {labels['total']}"
    return total


def age_since(moment: datetime, now: datetime | None = None) -> str:
    """How long ago `moment` was: 7m, 1h 4m, 2d 3h.

    Whole minutes under an hour; a clock that has gone backwards reads 0m
    rather than negative.
    """
    now = now or datetime.now(moment.tzinfo)
    minutes = max(int((now - moment).total_seconds()), 0) // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


class Session:
    """Running totals for ONE session log.

    Everything here comes off the stream itself rather than from bd or the
    clock, so --replay reports the same numbers the live view did.

    One log, one instance: `follow` builds a new one for each log it opens,
    because run.sh repoints `current.jsonl` at a new log every iteration and
    `track` lets the first bead in a log win. A single instance carried across
    that hand-over pinned the header to the first bead the watcher ever saw.
    The agent is the exception and lives in AGENT: a log directory belongs to
    one loop for the watcher's life.

    The counters hold to that rule. The two footer RENDERINGS reach past it on
    purpose: `totals` appends the watcher's running cost, which spans logs,
    and `phase_line` reads the clock to age a finished session. Both are
    about the watcher rather than the log, so neither is stored here.
    """

    def __init__(self, name: str = "") -> None:
        self.name = name
        self.status_at = time.monotonic()
        self.bead = None
        self.title = ""
        self.looked_up = None
        self.model = ""
        # The date of the prices behind an estimated cost, for an engine that
        # watch.py prices itself (ESTIMATED).
        self.prices_of = None
        # Which agent CLI wrote this log, from its `loop.session` line. A log
        # without one was written before run.sh had engines, so it is Claude's.
        self.engine = "claude"
        self.first = None
        self.last = None
        self.turns = 0
        self.tools = 0
        self.output = 0
        self.thinking = 0
        self.context = 0
        self.cached = 0
        self.phase = ""
        self.announced = False
        # The moment of the last closing event read, and the kind of the last
        # event of any sort. `finished` is the pair read together.
        self.result_at = None
        self.last_kind = ""

    # The harness of each engine, for `label`: the agent CLI that wrote the
    # log, then @<provider> when the requests go to a provider that is not the
    # CLI's own. cc is Claude Code and fw is Fireworks, short for the
    # half-width watcher panes. A later engine adds one entry here.
    HARNESS: ClassVar[dict[str, str]] = {
        "claude": "cc",
        "codex": "codex",
        "fw": "cc@fw",
    }

    @property
    def label(self) -> str:
        """Where the session runs, <harness>[@<provider>]/<model>.

        For example cc/claude-opus-5-5 or cc@fw/deepseek-flash-latest[1m]. An
        engine missing from HARNESS shows its own name as the harness.
        """
        return f"{self.HARNESS.get(self.engine, self.engine)}/{self.model_label}"

    @property
    def model_label(self) -> str:
        """The model for the header.

        Codex names no model in its own JSON, so run.sh passes what it gave on
        the command line — and an empty one there is a fact rather than a gap:
        no -m was passed, so Codex used the plan's default. A Claude log with
        no model yet is still a watcher that has not read the init event.
        """
        if self.model:
            return self.model
        return "codex default" if self.engine == "codex" else "?"

    @property
    def cost(self) -> float | None:
        """What this session cost, or None before its result event.

        Derived from COSTS rather than stored, so the figure on the session's
        own line and the figure inside the watcher total cannot drift apart.
        """
        return COSTS.get(self.name)

    @property
    def finished(self) -> datetime | None:
        """When the session ended, or None while it is still running.

        A closing event does not always close a log: a background task posts a
        result carrying a running total and the agent then works on for another
        hundred lines. So the session is over only while the last event read
        is still that closing event — one rule, rather than a write in `close`
        that every other event has to remember to undo.

        Also None on a Codex replay, which knows the session ended but not
        when: it reads as a session still running.
        """
        return self.result_at if self.last_kind in CLOSING_KINDS else None

    @property
    def timeless(self) -> bool:
        """True when neither the event nor the watcher can date a line.

        Codex events carry no clock of their own. Live, the watcher's own is
        the right answer — the line really did arrive now. A replay reads the
        log long afterwards, so its clock is the replay's and not the
        session's, and a plausible wrong time is worse than a blank.
        """
        return self.engine == "codex" and REPLAYING

    def clock(self) -> datetime | None:
        """Our own clock, for the events that carry none of their own."""
        return None if self.timeless else datetime.now().astimezone()

    def elapsed(self) -> str:
        if not (self.first and self.last):
            return "?"
        seconds = max(int((self.last - self.first).total_seconds()), 0)
        return (
            f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
            if seconds >= 3600
            else f"{seconds // 60}m{seconds % 60:02d}s"
        )

    def totals(self) -> str:
        """The counters line. Cost comes last on purpose: the pinned footer is
        cut to the pane's columns, and a narrow pane should lose the cost
        rather than the elapsed time and the turns."""
        if self.cost is not None:
            cost = f"session ${self.cost:.2f} est."
        elif self.engine in UNPRICED:
            cost = UNPRICED[self.engine]["footer"]
        else:
            cost = "session cost at end"
        ctx = f"{self.context / 1000:.0f}k ctx"
        # Codex reports its cached input beside the rest; the Claude path
        # sums cache reads into `context` instead, so it has no share to
        # break out and reads exactly as it did before.
        if self.cached:
            ctx += f" ({self.cached / 1000:.0f}k cached)"
        return (
            f"{self.elapsed()} \u00b7 {self.turns} turns \u00b7 {self.tools} tools"
            f" \u00b7 {self.output / 1000:.1f}k out \u00b7 {self.thinking / 1000:.1f}k think"
            f" \u00b7 {ctx}"
            f" \u00b7 {cost} \u00b7 {watcher_cost()}"
        )

    def finished_at(self) -> str:
        """When the session ended, in local time. Only call it once it has."""
        return f"finished {self.finished.astimezone().strftime('%H:%M')}"

    def phase_line(self) -> str:
        """The live footer's last line: the phase, or that the session ended.

        A watcher left running showed Alice's October 2 review as `working`
        two days after it finished, because nothing in the stream marked a
        session as over. The age is what the watcher is waiting through, so
        the renderings that outlive the wait \u2014 the plain footer, --replay \u2014
        print `finished_at` alone instead.
        """
        if not self.finished:
            return self.phase or "working"
        return (
            f"{self.finished_at()} \u00b7 {age_since(self.finished)} ago"
            " \u00b7 waiting for the next session"
        )

    def lookup_title(self) -> None:
        """Resolve the bead's summary once per bead; the header repaints often.

        Tried once whether or not it works, and silent when it does not: `bd`
        may be absent and it sits on a database another agent can be holding.
        The attach-time header calls this from outside feed's never-raise
        guard, so a failure here would otherwise take the watcher down before
        it had printed anything at all.
        """
        if not self.bead or self.looked_up == self.bead:
            return
        self.looked_up = self.bead
        try:
            shown = subprocess.run(
                ["bd", "show", self.bead],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            ).stdout.splitlines()
        except Exception:  # noqa: BLE001 - observability must never raise
            return
        if shown:
            self.title = shown[0].split("\u00b7", 1)[-1].split("[")[0].strip()

    def header_lines(self, width: int | None = None) -> list[str]:
        """The two header rows, shared by the plain header and the --tui pane.

        The label ends the second row and stays whole: in a pane narrower
        than the row, the session name is shortened first.
        """
        title = f" {AGENT} \u00b7 {self.bead or 'no bead yet'}"
        if self.title:
            title += f" \u00b7 {self.title}"
        started = self.first.astimezone().strftime("%H:%M:%S") if self.first else "?"
        tail = f" \u00b7 started {started} \u00b7 {self.label}"
        head = f" session {self.name}"
        if width is not None and len(head) + len(tail) > width:
            # Shorten the part before the label, and keep the label whole.
            head = head[: max(width - len(tail) - 1, 0)] + "\u2026"
        return [title, head + tail]

    def header(self) -> None:
        """The one-shot plain-stream header (non-TUI mode)."""
        self.lookup_title()
        print(RULE)
        for row in self.header_lines():
            print(row)
        print(RULE, flush=True)
        self.announced = True

    def footer(self) -> None:
        if not self.turns:
            return
        print(RULE)
        print(f" {self.bead or 'no bead'} \u00b7 {self.totals()}")
        if self.finished:
            print(f" {self.finished_at()}")
        elif self.phase:
            print(f" last: {self.phase}")
        print(RULE, flush=True)


SESSION = Session()

# The --tui style of the @<provider> part of the label, so that a session
# routed to another provider stands out.
PROVIDER_STYLE = "bold magenta"


def header_rows(width: int | None = None) -> list:
    """The header rows as Rich Text for --tui, with the provider styled."""
    from rich.text import Text

    rows = [
        Text(row, no_wrap=True, overflow="ellipsis")
        for row in SESSION.header_lines(width)
    ]
    label = SESSION.label
    harness = label.split("/", 1)[0]
    if "@" in harness:
        # The label ends the row whole, so its place is counted from the end.
        base = len(rows[1].plain) - len(label)
        rows[1].stylize(PROVIDER_STYLE, base + harness.index("@"), base + len(harness))
    return rows


class Pinned:
    """Header, body and footer drawn by rich.Live over a Layout (--tui only).

    Decision (Robert, 2026-10-14): the first version confined the terminal's
    own scroll region to the body and hand-drew the header and footer around
    it. Its resize-signal handler repainted from inside a signal, re-entered a
    half-finished write and crashed both watchers (RuntimeError: reentrant
    call inside BufferedWriter). Rich owns the screen instead and handles
    resizes itself. The cost is terminal scrollback and mouse selection of the
    body; the whole session stays in the .jsonl log and --replay prints it.

    Plain mode stays stdlib and never imports rich: it is imported here, on
    the --tui path only. Inactive unless stdout is a tty: as a pipeline stage
    in run.sh, or piped to a file, escape codes would be noise.
    """

    HEADER_ROWS = 4
    FOOTER_ROWS = 3
    # Body lines kept in memory. The screen shows the newest that fit.
    BODY_LINES = 500

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled and sys.stdout.isatty()
        self.body: deque = deque(maxlen=self.BODY_LINES)
        self.live = None
        self.layout = None
        self.size = None
        self.dirty = False
        self.painted_at = time.monotonic()

    def start(self, console=None) -> None:
        if not self.enabled:
            return
        from rich.console import Console, Group
        from rich.layout import Layout
        from rich.live import Live
        from rich.rule import Rule
        from rich.text import Text

        def line(text: str, style: str | None = None) -> Text:
            styled = Text(text, no_wrap=True, overflow="ellipsis")
            if style:
                # Clock and phase stay plain, so the columns still scan alike.
                styled.stylize(style, LABEL_COLUMN)
            return styled

        def header(console, options):
            yield Group(Rule(), *header_rows(options.max_width), Rule())

        def footer(console, options):
            yield Group(
                Rule(),
                line(f" {SESSION.totals()}"),
                line(f" {SESSION.phase_line()}"),
            )

        def body(console, options):
            # Newest lines that fit: the Layout hands the region's height down.
            rows = options.height or console.height
            for text, style in list(self.body)[-rows:]:
                yield line(text, style)

        class Pane:
            """Rich draws this at every refresh, so the region reads SESSION
            as it is then rather than as it was at start."""

            def __init__(self, render) -> None:
                self.render = render

            def __rich_console__(self, console, options):
                return self.render(console, options)

        self.layout = Layout()
        self.layout.split_column(
            Layout(Pane(header), name="header", size=self.HEADER_ROWS),
            Layout(Pane(body), name="body"),
            Layout(Pane(footer), name="footer", size=self.FOOTER_ROWS),
        )
        self.live = Live(
            self.layout,
            console=console or Console(),
            screen=True,
            auto_refresh=False,
        )
        self.live.start()
        self.size = self.live.console.size

    def stop(self) -> None:
        """Restore the terminal: leave the alternate screen, show the cursor."""
        if self.live is not None:
            self.live.stop()
            self.live = None

    def add(self, text: str, style: str | None = None) -> None:
        """Append one body line, or print it where there is no screen.

        `style` is a rich style for the line from its label onwards. Plain
        mode ignores it: escape codes there would be noise.
        """
        if self.live is None:
            print(text, flush=True)
            return
        self.body.append((text, style))
        # Painted by the next tick: a catch-up or a burst of events adds many
        # lines at once, and only the last screen would ever be seen.
        self.dirty = True

    def tick(self) -> None:
        """Repaint if lines arrived, the terminal resized, or REPAINT_SECONDS passed.

        One line of the footer moves on its own: the age of a finished
        session. Nothing else does \u2014 every other value is folded from an
        event, and an event marks the screen dirty through `add`. The
        REPAINT_SECONDS case is the fallback for a watcher with nothing to
        read. A resize repaints at once, since Rich only re-measures when it
        draws.
        """
        if self.live is None:
            return
        stale = time.monotonic() - self.painted_at >= REPAINT_SECONDS
        if self.dirty or stale or self.live.console.size != self.size:
            self.draw()

    def draw(self) -> None:
        """Repaint the screen from SESSION's current state."""
        if self.live is None:
            return
        self.painted_at = time.monotonic()
        self.dirty = False
        self.size = self.live.console.size
        self.live.refresh()


PINNED = Pinned(enabled=False)

# (phase, label, pattern) against a Bash command string, first match wins.
# Phases mirror .loop/CODER.md; ordering matters because a single command can
# chain several steps (`bd update --claim && git checkout -b ...`), and the
# earlier, more specific rule is the one worth reporting.
BASH_RULES = [
    ("0", "guard", r"rev-parse --show-toplevel"),
    ("0", "resume check", r"bd list .*in_progress"),
    ("1", "find work", r"bd ready"),
    ("1", "read spec", r"bd show"),
    ("1", "claim + branch", r"--claim"),
    ("1", "branch", r"checkout -b"),
    ("3", "MERGE", r"git -C .*merge --no-ff"),
    ("3", "merge abort", r"merge --abort"),
    ("3", "rebase", r"git rebase"),
    ("3", "check primary", r"git -C .*(status|rev-parse --abbrev-ref)"),
    ("3", "close bead", r"bd close"),
    ("3", "cleanup branch", r"branch -[dD]"),
    ("2", "format", r"^(?:uv run )?ruff format"),
    ("2", "lint", r"^(?:uv run )?ruff check"),
    ("2", "run tests", r"^(?:uv run )?pytest\b"),
    ("1", "checkpoint", r'commit -m ["\']?wip'),
    ("3", "commit", r"git commit"),
    ("-", "note", r"bd note"),
    ("-", "READ", r"^(sed -n|cat|head|tail|wc -l)\b"),
    ("-", "search", r"^(grep|rg)\b"),
    ("-", "explore", r"^(ls|find|tree)\b"),
    ("-", "file a bead", r"bd create"),
    ("-", "diff", r"git (diff|log|status|branch)"),
    ("-", "shell", r"."),
]


PATH_RE = re.compile(
    r"(?:[\w.\-]+/)+[\w.\-]+\.\w+|\b\w[\w.\-]*\.(?:py|md|toml|yaml|yml|json|sh|sql)\b"
)
RANGE_RE = re.compile(r"'(\d+),(\d+)p'|-n\s*(\d+)")


def _targets(command: str) -> str:
    """The file paths a command mentions, deduplicated in order.

    What distinguishes one read from the next is the file, not the flags, so
    this is what a reader needs on the line.
    """
    seen = dict.fromkeys(PATH_RE.findall(command))
    return ", ".join(seen)


def _read_detail(command: str) -> str:
    """Files being read, with a line range when there is one."""
    files = _targets(command) or command[:60]
    if match := RANGE_RE.search(command):
        low, high, single = match.groups()
        return f"{files}:{low}-{high}" if low else f"{files}:{single}"
    return files


def classify(block: dict) -> tuple[str, str, str]:
    """(phase, label, detail) for one tool_use block."""
    name = block.get("name", "?")
    args = block.get("input") or {}

    if path := args.get("file_path"):
        rel = path.split("/agents-ralph/")[-1].split("/agents/")[-1]
        verb = "edit" if name == "Edit" else "write"
        if rel.startswith("tests/"):
            return "1", f"{verb} tests", rel
        if rel.startswith(("src/", "scripts/")):
            return "1", f"{verb} code", rel
        return "-", f"{verb} file", rel

    if skill := args.get("skill"):
        return (
            ("2", "REVIEW", f"/{skill}")
            if skill == "simplify"
            else ("-", "skill", f"/{skill}")
        )

    command = " ".join((args.get("command") or "").split())
    if not command:
        return "-", name.lower(), ""
    return classify_command(command)


def classify_command(command: str) -> tuple[str, str, str]:
    """(phase, label, detail) for a shell command, whichever engine ran it.

    Split out of `classify` because a Codex `command_execution` item is a bare
    command with none of a Claude tool_use block around it, and the phase map
    is the part both engines share.
    """
    # Strip a leading env assignment so `PRIMARY=... sed -n ...` classifies as
    # the read it is rather than falling through to the catch-all.
    bare = re.sub(r"^(\w+=\S+;?\s*)+", "", command)
    # Each rule sees both the whole line and its individual commands, so an
    # anchored pattern still matches `cd x && uv run pytest -q` while an
    # unanchored word inside a grep argument no longer masquerades as one.
    segments = [bare, *(part.strip() for part in re.split(r"&&|\|\||;|\|", bare))]
    for phase, label, pattern in BASH_RULES:
        if any(re.search(pattern, segment) for segment in segments):
            detail = (
                _read_detail(bare)
                if label in ("READ", "search", "explore")
                else bare[:96]
            )
            return phase, label, detail
    return "-", "shell", bare[:96]


_last: list = [None, 0, ""]

# Where the label starts in a body line: the eight-wide clock, two spaces, the
# nine-wide phase marker and one space, as `_write` lays them out.
LABEL_COLUMN = 8 + 2 + 9 + 1

# The agent's own voice reads apart from its tool calls (--tui only): what it
# says to the reader, and that it is thinking.
LINE_STYLES = {
    "says": "italic bright_cyan",
    "thinking": "italic #ffbf00",
}

# A skill call, and Codex's `codex exec review`, are the protocol's own steps:
# the default review method and the /simplify pass (--tui only). Styled by
# where the line comes from, not by its label, so /simplify keeps REVIEW.
# Bold rather than italic, because italic marks the agent's own words.
SKILL_STYLE = "bold #af87ff"
CODEX_REVIEW_RE = re.compile(r"\bcodex exec review\b")


def _write(
    stamp: str | None,
    phase: str,
    label: str,
    detail: str,
    repeats: int,
    style: str | None = None,
) -> None:
    marker = f"PHASE {phase}" if phase != "-" else "       "
    tail = f"  \u00d7{repeats}" if repeats > 1 else ""
    # A line whose event carried no clock, on a replay that cannot supply one,
    # keeps the column and leaves it blank: HH:MM:SS is eight wide.
    PINNED.add(
        f"{stamp or '':8}  {marker:9} {label:16} {detail}{tail}",
        style,
    )


def emit(
    stamp: str | None,
    phase: str,
    label: str,
    detail: str,
    style: str | None = None,
) -> None:
    """Print one line, collapsing an identical repeat into a xN counter.

    Distinct work must look distinct and repeated work must look repeated —
    otherwise a reader cannot tell a busy session from a stuck one. `style`
    overrides the label's own --tui style.
    """
    key = (phase, label, detail, style or LINE_STYLES.get(label))
    if key == _last[0]:
        _last[1] += 1
        return
    flush_pending()
    _last[0], _last[1], _last[2] = key, 1, stamp


def flush_pending() -> None:
    if _last[0] is not None:
        phase, label, detail, style = _last[0]
        _write(_last[2], phase, label, detail, _last[1], style)
        _last[0] = None


def stamp_of(event: dict) -> str | None:
    """The event's own clock where it has one, else ours — or none at all.

    Assistant events carry an ISO `timestamp` and the session line a `started`;
    system events mostly carry neither, and Codex events never do. Preferring
    the event's makes --replay show when things actually happened rather than
    when they were read back. Where the event has none, `Session.clock` says
    whether ours is worth printing in its place.
    """
    raw = event.get("timestamp") or event.get("started")
    if not raw:
        return None if SESSION.timeless else time.strftime("%H:%M:%S")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return time.strftime("%H:%M:%S")
    return parsed.astimezone().strftime("%H:%M:%S")


def status_tick() -> None:
    """A compact totals line every STATUS_SECONDS, so a long bead still shows
    progress between tool calls."""
    if time.monotonic() - SESSION.status_at < STATUS_SECONDS:
        return
    SESSION.status_at = time.monotonic()
    flush_pending()
    PINNED.add(f"       \u22ef {SESSION.totals()} \u00b7 {SESSION.phase_line()}")


def close(event: dict) -> None:
    """Fold a session's closing result event into SESSION and into COSTS.

    This is the only event that reports what the session cost and the only
    one that can say the session is over — only can, because the log may go
    on past it; `Session.finished` is what decides. Three readings come off
    it:

    * total_cost_usd, stored against the log in COSTS, which explains why it
      is written there rather than added.
    * modelUsage outputTokens, which corrects the footer's `k out`. The
      assistant events report output that is hundreds against the result's
      tens of thousands, so the counter was wrong until this arrived.
    The finish time is not read here: `track` takes it from the last
    timestamped event for both engines' closing events.
    """
    usage = {
        model: tokens
        for model, tokens in (event.get("modelUsage") or {}).items()
        if isinstance(tokens, dict)
    }
    if SESSION.engine in ESTIMATED:
        # An fw session runs Claude Code, but its figure is wrong: see UNPRICED.
        estimate_session(ESTIMATED[SESSION.engine][0], usage)
    elif SESSION.engine not in UNPRICED:
        cost = event.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            COSTS[SESSION.name] = float(cost)
    output = sum(tokens.get("outputTokens") or 0 for tokens in usage.values())
    if output:
        SESSION.output = output


def estimate_session(provider: str, usage: dict) -> None:
    """Price the session from its modelUsage, or leave it without a figure.

    Every model in it must have a price: a figure that leaves out a subagent's
    model would read as the whole session's. The note gives the oldest price
    date of the models it used. A later closing event replaces the figure, as
    it does in COSTS for Claude.
    """
    priced = [
        estimate(
            provider,
            model,
            inputs=tokens.get("inputTokens") or 0,
            cached=tokens.get("cacheReadInputTokens") or 0,
            written=tokens.get("cacheCreationInputTokens") or 0,
            output=tokens.get("outputTokens") or 0,
        )
        for model, tokens in usage.items()
    ]
    if priced and None not in priced:
        COSTS[SESSION.name] = sum(dollars for dollars, _ in priced)
        SESSION.prices_of = min(checked for _, checked in priced)
    else:
        COSTS.pop(SESSION.name, None)


def start_line(event: dict) -> None:
    """Fold run.sh's `loop.session` line, the first line of every new log.

    It carries the three things no engine's own stream carries for both of
    them: which engine wrote the log, the model run.sh passed on the command
    line (Codex's JSON names none, even when -m was given), and the wall-clock
    start (Codex's events carry no timestamps at all). A log with no such line
    was written before run.sh had engines, and reads as Claude. A log written
    before lpe-wh5 calls the line `ralph.session`, and reads the same.
    """
    SESSION.engine = event.get("engine") or "claude"
    SESSION.model = event.get("model") or ""
    if SESSION.engine in UNPRICED:
        UNPRICED_SESSIONS[SESSION.name] = SESSION.engine
    raw = event.get("started")
    if not raw:
        return
    try:
        SESSION.first = SESSION.first or datetime.fromisoformat(raw)
    except ValueError:
        pass


def close_codex(event: dict) -> None:
    """Fold a Codex `turn.completed`: the end of the session, and its usage.

    The Claude counterpart of `close`, minus the money — a ChatGPT plan
    reports no per-session figure, so nothing goes into COSTS and the session
    is counted in UNPRICED_SESSIONS instead.

    `codex exec` runs one turn per session, but the usage is that turn's
    rather than the thread's, so output and reasoning accumulate. Input is a
    level, like the Claude context, and takes the high-water mark.
    """
    usage = event.get("usage") or {}
    SESSION.output += usage.get("output_tokens") or 0
    SESSION.thinking += usage.get("reasoning_output_tokens") or 0
    SESSION.context = max(SESSION.context, usage.get("input_tokens") or 0)
    SESSION.cached = usage.get("cached_input_tokens") or 0


def note_tool(command: str) -> None:
    """Count one tool call and, if it is the first to name a bead, take it.

    Both engines fold their tool calls through here. First one wins: Ralph
    opens with his claim and Alice with her `Review round <n> started` note,
    and both go on to mention other beads that must not move the header.
    """
    SESSION.tools += 1
    if not SESSION.bead:
        SESSION.bead = bead_in(command)


def track(event: dict, kind: str) -> None:
    """Fold one event into SESSION's totals.

    Every piece of session state is updated here and nowhere else, so a watcher
    that attaches partway through can replay the log through this alone and
    reach the same header the printing path would have produced. Both
    vocabularies are folded here, for the same reason.
    """
    SESSION.last_kind = kind or ""
    if raw := event.get("timestamp"):
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            moment = None
        if moment:
            SESSION.first = SESSION.first or moment
            SESSION.last = moment
    # Nothing above ever moves `last` on a Codex log, so without this the
    # footer's elapsed time would stick at `?` for the whole run.
    if SESSION.engine == "codex" and (moment := SESSION.clock()):
        SESSION.last = moment
    # When the session ended, for both engines. Neither closing event carries
    # a clock of its own, so it takes the last event that did — written on
    # every one of them, so a session that resumed after a mid-log result
    # reports the later finish rather than the one that result saw. The
    # fallback is the same clock the body lines use, so a Codex replay reports
    # no finish rather than the replay's own: that read as a session that
    # ended before the start its session line gave.
    if kind in CLOSING_KINDS:
        SESSION.result_at = SESSION.last or SESSION.clock()
    if kind in SESSION_KINDS:
        start_line(event)
        return
    if kind == "turn.started":
        SESSION.turns += 1
        return
    if kind == "turn.completed":
        close_codex(event)
        return
    if kind == "item.started":
        item = event.get("item") or {}
        if item.get("type") == "command_execution":
            note_tool(item.get("command") or "")
        return
    if kind == "system" and event.get("subtype") == "init":
        SESSION.model = event.get("model", "")
    if kind == "system" and event.get("subtype") == "thinking_tokens":
        SESSION.thinking += event.get("estimated_tokens_delta") or 0
    if kind == "result":
        close(event)
    if kind != "assistant":
        return
    SESSION.turns += 1
    for block in event.get("message", {}).get("content") or []:
        if block.get("type") == "tool_use":
            note_tool((block.get("input") or {}).get("command") or "")
    usage = event.get("message", {}).get("usage") or {}
    SESSION.output += usage.get("output_tokens") or 0
    # Context is a level, not a total: the newest turn's prompt size is the
    # number that says how close this session is to compaction.
    SESSION.context = max(
        SESSION.context,
        sum(
            usage.get(key) or 0
            for key in (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        ),
    )


def tool_line(
    stamp: str | None,
    phase: str,
    label: str,
    detail: str,
    style: str | None = None,
) -> None:
    """Print one classified tool call, and move the header and phase with it.

    Shared by both engines: a Claude tool_use block and a Codex
    `command_execution` item differ only in how the command is reached.
    """
    if phase != "-":
        SESSION.phase = f"PHASE {phase} {label}"
    if PINNED.enabled:
        # Pinned mode keeps the header on screen; the inline one would be a
        # body line, which is exactly what scrolls away.
        SESSION.lookup_title()
    elif (
        not SESSION.announced and SESSION.first and (SESSION.bead or SESSION.tools >= 6)
    ):
        SESSION.header()
    emit(stamp, phase, label, detail, style)


def say(stamp: str | None, text: str) -> None:
    """Print the agent's own narration, often the clearest signal of all."""
    if collapsed := " ".join(text.split()):
        emit(stamp, "-", "says", collapsed[:96])


def think(stamp: str | None, text: str) -> None:
    """Print a thinking block: its text where the log keeps it, else a mark.

    Claude Code's stream often carries the block with its text redacted, so
    the line then says only that the agent thought before its next step.
    Consecutive marks collapse into one line with a xN counter.
    """
    emit(stamp, "-", "thinking", " ".join(text.split())[:96] or "…")


def codex_item(stamp: str | None, kind: str, item: dict) -> None:
    """Print one Codex item event: a command, its failure, or a message.

    Only the three shapes the engine switch recorded are read. Anything else —
    a reasoning item, a file change, a shape a later codex-cli adds — is
    ignored rather than guessed at.
    """
    shape = item.get("type")
    if shape == "command_execution":
        command = " ".join((item.get("command") or "").split())
        if kind == "item.started":
            style = SKILL_STYLE if CODEX_REVIEW_RE.search(command) else None
            tool_line(stamp, *classify_command(command), style)
        elif item.get("exit_code"):
            emit(stamp, "-", "FAILED", f"exit {item['exit_code']}  {command[:80]}")
    elif shape == "agent_message" and kind == "item.completed":
        say(stamp, item.get("text") or "")


def handle(event: dict) -> None:
    stamp = stamp_of(event)
    kind = event.get("type")
    track(event, kind)

    if kind in SESSION_KINDS:
        # A Claude log announces itself with its init event, as it always has.
        # A Codex log has no such event, so its session line is the marker.
        if SESSION.engine == "codex":
            emit(stamp, "-", SESSION_MARK, SESSION.label)
        return
    if kind in ("item.started", "item.completed"):
        codex_item(stamp, kind, event.get("item") or {})
        return
    if kind in CLOSING_KINDS:
        # The footer has just gained the final usage and turned over to
        # `finished`, and no body line is coming to repaint it.
        PINNED.draw()
        return
    if kind == "rate_limit_event":
        info = event.get("rate_limit_info") or {}
        windows = info.get("unifiedWindows") or {}
        used = ", ".join(
            f"{name} {round(w.get('utilization', 0) * 100)}%"
            for name, w in windows.items()
        )
        emit(stamp, "-", "rate limit", f"{info.get('status', '?')} — {used}")
        return
    if kind == "system" and event.get("subtype") == "init":
        emit(stamp, "-", SESSION_MARK, SESSION.label)
        return
    if kind != "assistant":
        return

    for block in event.get("message", {}).get("content") or []:
        if block.get("type") == "tool_use":
            skill = (block.get("input") or {}).get("skill")
            tool_line(stamp, *classify(block), SKILL_STYLE if skill else None)
        elif block.get("type") == "text":
            say(stamp, block.get("text") or "")
        elif block.get("type") == "thinking":
            think(stamp, block.get("thinking") or "")


def tally(event: dict) -> None:
    """`handle` with the printing left out, for the catch-up."""
    track(event, event.get("type"))


def feed(line: str, act=handle) -> None:
    """Fold one JSON line into SESSION, swallowing anything it throws.

    A partial trailing line is normal (the file is appended to while we read
    it), and a classification bug must never propagate: when this runs as a
    pipeline stage, an exception here breaks the pipe and kills the session it
    is only supposed to be watching. That is why the guard lives here, once,
    and every path that reads a line comes through it — `act` is `handle` for
    the live stream and `tally` for the catch-up.
    """
    try:
        act(json.loads(line))
    except Exception:  # noqa: BLE001,S110 - observability must never raise
        pass


def catch_up(fp) -> str:
    """Fold a log's existing lines into SESSION without printing any of them.

    A watcher is opened long after the session started, so the init event
    carrying the model and the claim carrying the bead are already far above
    the live edge. Reading them through `track` alone gives the header its
    three values and the session its real start time, while what gets PRINTED
    still begins at the edge, which is why the reader opened it.

    Returns the unfinished trailing line for `drain` to carry: the file is
    appended to while this reads it, so seeking to the end afterwards would
    skip whatever arrived in between.

    Read a line at a time rather than through `drain`, whose single `fp.read()`
    is sized for the delta between two polls: a session log is megabytes by the
    time anyone attaches, and holding all of it at once costs this long-lived
    process several times the memory for no gain.
    """
    tail = ""
    for line in fp:
        if not line.endswith("\n"):
            tail = line  # the live edge, mid-write; `drain` finishes it
            break
        feed(line, tally)
    return tail


def announce() -> None:
    """Paint the header as soon as the catch-up has something to put in it.

    Waiting for the next tool call to trigger it would leave the header
    reporting the attach for as long as the session stays quiet, which is the
    bug. Pinned mode repaints unconditionally: its block is still showing the
    log before this one, and holding the old bead there until the new session
    happens to speak is the same staleness by another route. It also needs the
    title resolved explicitly, where plain mode's one-shot header resolves it
    on the way through.
    """
    if PINNED.enabled:
        SESSION.lookup_title()
        PINNED.draw()
    elif SESSION.first and not SESSION.announced:
        SESSION.header()


def drain(fp, buffer: str) -> str:
    """Process whole lines from fp, returning the unfinished tail.

    A partial trailing line is normal — the file is being appended to while we
    read it — so an undecodable tail is buffered and retried rather than
    reported as an error.
    """
    buffer += fp.read()
    while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)
        if line.strip():
            feed(line)
    return buffer


def pump(fp) -> None:
    """Drain fp until EOF. Same code for a live pipe and a finished file."""
    while line := fp.readline():
        if line.strip():
            feed(line)


def replay(path: Path) -> None:
    global SESSION, REPLAYING
    REPLAYING = True
    SESSION = Session(path.name)
    with open(path, encoding="utf-8", errors="replace") as fp:
        pump(fp)
    flush_pending()
    PINNED.stop()
    SESSION.footer()


def cost_note(summary: dict) -> str:
    """The bead note run.sh appends after a session: who ran, on what, for how much.

    Worded so it can never be read as a status marker: CODER.md and REVIEWER.md
    route on the last marker in a bead's notes, and this note lands after the
    agent's own. Codex on a ChatGPT plan reports tokens and no dollar figure,
    and says so rather than printing $0.00. Claude Code has no price for the
    Fireworks model of an fw session, so its figure is left out (UNPRICED).
    """
    engine = summary["engine"]
    if engine in ESTIMATED and summary["cost"] is not None:
        cost = (
            f"${summary['cost']:.2f} est."
            f" ({ESTIMATED[engine][1]} prices of {summary['prices_of']})"
        )
    elif engine in UNPRICED:
        cost = UNPRICED[engine]["note"]
        if engine in ESTIMATED:
            cost += ", model not in prices.json"
    elif summary["cost"] is None:
        cost = "no cost reported"
    else:
        cost = f"${summary['cost']:.2f} est."
    ctx = f"{summary['context'] / 1000:.0f}k ctx"
    if summary["cached"]:
        ctx += f" ({summary['cached'] / 1000:.0f}k cached)"
    parts = [summary["agent"], summary["label"], cost]
    if summary["elapsed"] != "?":
        parts.append(f"{summary['elapsed']} wall")
    parts += [
        f"{summary['turns']} turns",
        f"{summary['output'] / 1000:.1f}k out",
        f"{summary['thinking'] / 1000:.1f}k think",
        ctx,
    ]
    return "Session cost: " + ", ".join(parts)


def summarize(path: Path) -> dict:
    """What one finished session log says about its session, for run.sh.

    The agent cannot record its own cost: the figure arrives in the closing
    event, after the agent has exited. So run.sh calls this once the session
    is over and writes the note itself. The log is folded through `tally`
    alone, the path the catch-up takes, so the bead, model, cost and tokens
    are the ones the watcher reported.

    `note` is None when the log never reached its closing event — a crash or
    a timeout. A partial figure on a bead reads as the whole session's, which
    is worse than no note. `bead` can be None while `note` is not: a resumed
    session may never claim, tag or review, and run.sh knows which bead the
    agent held when the session started.
    """
    global SESSION, REPLAYING
    REPLAYING = True
    SESSION = Session(path.name)
    with open(path, encoding="utf-8", errors="replace") as fp:
        for line in fp:
            if line.strip():
                feed(line, tally)
    summary = {
        "bead": SESSION.bead,
        "agent": AGENT,
        "engine": SESSION.engine,
        "model": SESSION.model_label,
        "label": SESSION.label,
        "cost": SESSION.cost,
        "prices_of": SESSION.prices_of,
        "elapsed": SESSION.elapsed(),
        "turns": SESSION.turns,
        "output": SESSION.output,
        "thinking": SESSION.thinking,
        "context": SESSION.context,
        "cached": SESSION.cached,
        "closed": SESSION.last_kind in CLOSING_KINDS,
    }
    summary["note"] = cost_note(summary) if summary["closed"] else None
    return summary


def follow(path_of, *, from_start: bool = False) -> None:
    """Tail whatever path_of() returns, restarting when it moves.

    Prints from the end by default, like `tail -f`: a session log is hundreds
    of kilobytes by the time anyone thinks to watch it, and replaying all of it
    to reach the live edge buries the thing you opened this to see. The lines
    above the edge are still COUNTED, by `catch_up`, or the header would report
    the attach rather than the session. --from-start prints them too.

    Each target gets its own SESSION, so a watcher left running through a
    `current.jsonl` rollover reports the log it is watching now.
    """
    global SESSION
    while True:
        target = path_of()
        if target is None:
            # No log yet: still paint, or --tui shows a blank screen until one.
            PINNED.tick()
            time.sleep(POLL_SECONDS)
            continue
        PINNED.add(f"\u2500\u2500 watching {target.name} \u2500\u2500")
        flush_pending()  # the previous log's held line, under its own clock
        SESSION = Session(target.name)
        with open(target, encoding="utf-8", errors="replace") as fp:
            buffer = "" if from_start else catch_up(fp)
            announce()
            while path_of() == target:
                buffer = drain(fp, buffer)
                status_tick()
                PINNED.tick()
                time.sleep(POLL_SECONDS)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent", help="the agent being watched; default: read from the log directory"
    )
    parser.add_argument(
        "--file", type=Path, help="a specific session log, or - for stdin"
    )
    parser.add_argument(
        "--replay", action="store_true", help="print the newest finished log and exit"
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="pin the header and footer with a Rich full-screen view",
    )
    parser.add_argument(
        "--from-start",
        action="store_true",
        help="when following, replay the log before tailing it",
    )
    parser.add_argument(
        "--prefix",
        help="the project's bead prefix, for example lpe; default: any prefix",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        metavar="LOG",
        help="print one finished log's bead, cost and tokens as JSON and exit",
    )
    args = parser.parse_args(argv)

    global AGENT
    AGENT = args.agent or agent_name(LOG_DIR)
    use_prefix(args.prefix)
    if args.summary:
        # stdout carries the JSON that run.sh parses, so the warning goes to
        # stderr.
        if not args.prefix:
            print(PREFIX_WARNING, file=sys.stderr)
        print(json.dumps(summarize(args.summary)))
        return 0
    # A replay or stdin run prints a whole log and exits, and a full-screen
    # view would wipe it on the way out. Those stay plain.
    PINNED.enabled = args.tui and not args.replay and args.file != Path("-")
    if PINNED.enabled and not sys.stdout.isatty():
        print(
            "watch: --tui needs a terminal; stdout is redirected, streaming plain",
            file=sys.stderr,
        )
        PINNED.enabled = False
    PINNED.start()
    if not args.prefix:
        PINNED.add(PREFIX_WARNING)
    try:
        return run(args)
    finally:
        PINNED.stop()


def run(args) -> int:
    if args.file == Path("-"):
        pump(sys.stdin)
        return 0
    if args.file:
        replay(args.file) if args.replay else follow(
            lambda: args.file, from_start=args.from_start
        )
        return 0
    if args.replay:
        logs = sorted(LOG_DIR.glob("2*.jsonl"))
        if not logs:
            print(f"no session logs in {LOG_DIR}", file=sys.stderr)
            return 1
        replay(logs[-1])
        return 0

    link = LOG_DIR / "current.jsonl"
    PINNED.add(f"following {link} — ctrl-c to stop")
    # Resolve each poll: run.sh repoints the symlink on every new session.
    follow(
        lambda: link.resolve() if link.exists() else None,
        from_start=args.from_start,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        PINNED.stop()
        flush_pending()
        SESSION.footer()
    except BrokenPipeError:
        # Downstream closed (piped to head, or run.sh's session ended). Drop
        # stdout before the interpreter tries to flush it and reports again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
