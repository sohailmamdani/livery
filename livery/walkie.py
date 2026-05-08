"""Walkie-Talkie protocol — async turn-based debate between two AI sessions
via a shared markdown file.

Two AIs (e.g. the workspace's CoS in Claude Code and a peer running in
Codex) converge on a hard decision by appending turns to the same file.
Each turn has a structured header (`## Turn N — <peer> — <ts>`); the
plan is "locked" once both peers have appended a `SIGNED: <peer> @ <ts>`
line.

The protocol's value is in the *rules* (append-only, read-the-whole-file,
push back hard, sign to converge). This module provides:

  - file scaffolding (`new_walkie`) — drops a markdown file with the
    rules baked in so neither AI can drift off-protocol
  - parsing (`parse_walkie`) — counts turns, extracts signers, detects
    convergence — so the CLI can show status and a future automation
    can detect "both signed" without an LLM in the loop
  - listing (`list_walkies`)

The protocol body itself lives in `WALKIE_PROTOCOL_RULES`. Both AIs read
it from the file every time they take a turn — single source of truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

# Stored verbatim in every walkie file's frontmatter so the framework
# (and humans) can identify these without filename heuristics.
FRONTMATTER_MARKER = "walkie-talkie"

WALKIE_DIR_NAME = "walkie-talkie"

# `## Turn 3 — claude — 2026-05-07T12:34:56Z`
TURN_HEADER_RE = re.compile(
    r"^##\s+Turn\s+(?P<n>\d+)\s+—\s+(?P<peer>[^\s—][^—\n]*?)\s+—\s+(?P<ts>\S+)\s*$",
    re.MULTILINE,
)

# `SIGNED: claude @ 2026-05-07T12:34:56Z`
SIGNED_RE = re.compile(
    r"^SIGNED:\s+(?P<peer>\S+)\s+@\s+(?P<ts>\S+)\s*$",
    re.MULTILINE,
)


WALKIE_PROTOCOL_RULES = """\
<!-- LIVERY-WALKIE-TALKIE PROTOCOL — DO NOT EDIT BELOW THIS LINE -->

## Protocol — read before every turn

This file is a turn-based, append-only debate between two AI sessions.
The protocol is strict on purpose: ambiguity here costs the convergence
that walkie-talkie exists to provide.

1. **Append, never prepend.** New turns go at the *bottom* of this file,
   above the protocol section. If you find yourself prepending, stop.
2. **Read the entire file first.** Don't reply to one turn out of
   context — read everything since the file started, including any
   protocol notes your peer left.
3. **Turn header.** Exactly: `## Turn N — <your-id> — <ISO8601-UTC ts>`,
   where N is the next integer in sequence (last turn's N + 1) and
   `<your-id>` is the peer name you've been using. Mismatched headers
   will confuse parsing — copy the format precisely.
4. **One turn at a time.** After you append, stop and wait for your
   peer's next turn. Don't double-append.
5. **Push back when you disagree.** Walkie-talkie exists to converge on
   the *correct* answer, not to manufacture consensus. If you think
   your peer is wrong, say so directly with reasoning. Don't hedge.
6. **Don't reply to settled material.** If a point is resolved, move on.
   Restating your peer's points back at them wastes both sides' turns.
7. **Sign to converge.** When you believe the proposed plan is correct
   AND your peer has expressed equivalent agreement, append a line
   *inside your turn*:

       SIGNED: <your-id> @ <ISO8601-UTC ts>

   The walkie is "locked" once **both peers** have signed. After that,
   open a new walkie for any follow-up debate — don't reopen this one.

## Watching for new turns

Both peers should poll this file periodically when actively engaged.
Reasonable cadence: ~60s while both sides are working, faster (~15s)
right after you append (you're expecting a quick reply), slower (~5m)
when idle. Polling means re-reading the file and checking whether the
turn count has incremented past the last one you saw.
"""


@dataclass(slots=True)
class Turn:
    n: int
    peer: str
    timestamp: str  # ISO8601 string as it appears in the header
    body: str       # turn body text (between this header and the next, trimmed)


@dataclass(slots=True)
class Signature:
    peer: str
    timestamp: str


@dataclass(slots=True)
class WalkieFile:
    path: Path
    topic: str
    started: str | None
    turns: list[Turn] = field(default_factory=list)
    signatures: list[Signature] = field(default_factory=list)

    @property
    def peers(self) -> set[str]:
        """Distinct peer names that have taken at least one turn."""
        return {t.peer for t in self.turns}

    @property
    def signed_peers(self) -> set[str]:
        return {s.peer for s in self.signatures}

    @property
    def is_locked(self) -> bool:
        """A walkie is locked when at least two distinct peers have signed.

        Two-peer threshold is a *minimum* — a 3-way walkie would need
        three signatures, but the protocol is currently bilateral.
        """
        return len(self.signed_peers) >= 2 and self.signed_peers >= self.peers

    @property
    def next_turn_n(self) -> int:
        return (self.turns[-1].n if self.turns else 0) + 1


def walkie_dir(workspace_root: Path) -> Path:
    return workspace_root / WALKIE_DIR_NAME


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "walkie"


def new_walkie(
    *,
    workspace_root: Path,
    topic: str,
    opener: str | None = None,
    initiator: str | None = None,
    when: datetime | None = None,
) -> Path:
    """Scaffold a new walkie-talkie file under <workspace>/walkie-talkie/.

    Returns the path. Refuses to overwrite an existing file (raises
    FileExistsError) — walkies are append-only history; if you want a
    fresh one, pick a new topic.

    If `opener` is given AND `initiator` is given, the file is seeded
    with Turn 1 by the initiator. Otherwise the file is left at "no
    turns yet" — the caller is expected to take Turn 1 themselves.
    """
    slug = _slugify(topic)
    target_dir = walkie_dir(workspace_root)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{slug}.md"
    if path.exists():
        raise FileExistsError(
            f"walkie already exists: {path}. Pick a different topic or "
            f"continue the existing one."
        )

    started = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fm = {
        "livery": FRONTMATTER_MARKER,
        "topic": topic,
        "started": started,
    }

    # `frontmatter.dumps` re-orders keys; for stability and readability
    # we hand-write the YAML block. Keys are simple scalars, no escaping
    # concerns beyond the topic — quote it defensively.
    yaml_block = (
        "---\n"
        f"livery: {FRONTMATTER_MARKER}\n"
        f"topic: {_yaml_str(topic)}\n"
        f"started: {started}\n"
        "---\n"
    )

    body_parts = [
        yaml_block,
        f"\n# Walkie-Talkie: {topic}\n\n",
        "> Two AI sessions converging on a decision by debating in this\n"
        "> file. The protocol is at the bottom — read it before you\n"
        "> append.\n\n",
    ]

    if opener and initiator:
        body_parts.append(
            f"## Turn 1 — {initiator} — {started}\n\n{opener.rstrip()}\n\n"
        )

    body_parts.append(WALKIE_PROTOCOL_RULES)

    path.write_text("".join(body_parts))
    return path


def _yaml_str(s: str) -> str:
    """Conservative YAML scalar quoting. If the string contains characters
    that could confuse YAML, double-quote it; otherwise leave bare."""
    if any(c in s for c in ":#\n\"'\\[]{}|>&!*%@`,") or s.strip() != s:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def parse_walkie(path: Path) -> WalkieFile:
    """Read a walkie file from disk, return its parsed structure.

    Tolerant: a file that doesn't have the frontmatter marker still
    parses (turns are still detectable from headers), but `topic` falls
    back to the filename stem.
    """
    text = path.read_text()
    try:
        post = frontmatter.loads(text)
        meta = post.metadata
        body = post.content
    except Exception:
        meta = {}
        body = text

    topic = str(meta.get("topic") or path.stem)
    started = meta.get("started")
    started_str = str(started) if started else None

    # Find all turn headers + their byte offsets so we can slice bodies.
    matches = list(TURN_HEADER_RE.finditer(body))
    turns: list[Turn] = []
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        turn_body = body[body_start:body_end].strip()
        # Strip the trailing protocol section if it bled into the last turn
        # (the protocol HTML comment is the marker).
        if "<!-- LIVERY-WALKIE-TALKIE PROTOCOL" in turn_body:
            turn_body = turn_body.split("<!-- LIVERY-WALKIE-TALKIE PROTOCOL")[0].rstrip()
        turns.append(Turn(
            n=int(m.group("n")),
            peer=m.group("peer").strip(),
            timestamp=m.group("ts"),
            body=turn_body,
        ))

    signatures = [
        Signature(peer=m.group("peer"), timestamp=m.group("ts"))
        for m in SIGNED_RE.finditer(body)
    ]

    return WalkieFile(
        path=path,
        topic=topic,
        started=started_str,
        turns=turns,
        signatures=signatures,
    )


def list_walkies(workspace_root: Path) -> list[WalkieFile]:
    """All walkies in the workspace, parsed. Sorted by `started`
    descending (most recent first); files without a started timestamp
    sort last."""
    target_dir = walkie_dir(workspace_root)
    if not target_dir.is_dir():
        return []
    files = [parse_walkie(p) for p in target_dir.glob("*.md")]
    files.sort(key=lambda w: (w.started is None, w.started or ""), reverse=True)
    # `reverse=True` puts None first; flip so None goes last
    return sorted(files, key=lambda w: (w.started is None, -(_started_sort_key(w.started))))


def _started_sort_key(ts: str | None) -> int:
    """Best-effort numeric sort key from an ISO8601 string; missing → 0."""
    if not ts:
        return 0
    digits = re.sub(r"\D", "", ts)
    return int(digits) if digits else 0
