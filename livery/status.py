"""`livery status` — at-a-glance dashboard for a Livery workspace.

Where `livery ticket list` is the raw scriptable cut, `livery status` is
the human-readable "state of the company" view: counts by assignee, stale
tickets, blocked tickets, recent closes, and runtime health — all on one
screen, with grouping and emphasis.

Pure-data functions live here; rendering (ANSI color, TTY detection,
column alignment) lives in the CLI command that wraps these.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import frontmatter

from .doctor import run_doctor


DEFAULT_STALE_DAYS = 7
DEFAULT_RECENT_CLOSED_LIMIT = 5

# Statuses that count as terminal — i.e. the ticket is no longer in the
# active queue. Anything outside this set is treated as still-open by
# `livery status`, which is why bare strings like "cancelled" used to
# leak into the open bucket.
#
# `done` is the canonical close state set by `livery ticket close`.
# `closed` is a synonym some users prefer.
# `cancelled`, `abandoned`, `wontfix` cover the "decided not to do this"
# cases.
#
# Users who introduce a custom terminal status will need to PR a new
# entry here (and a doc note in docs/config.md). Worth that friction —
# silently treating unknown statuses as "open" turned out to be the wrong
# default.
TERMINAL_STATUSES: frozenset[str] = frozenset({
    "done",
    "closed",
    "cancelled",
    "abandoned",
    "wontfix",
})


@dataclass(slots=True)
class TicketSummary:
    id: str
    title: str
    assignee: str
    status: str
    created: datetime | None
    updated: datetime | None
    blocked_on: str | None  # frontmatter field; orthogonal to status

    @property
    def is_blocked(self) -> bool:
        return self.status == "blocked" or bool(self.blocked_on)

    @property
    def age_days(self) -> int | None:
        if self.created is None:
            return None
        return (datetime.now(timezone.utc) - self.created).days


@dataclass(slots=True)
class StatusReport:
    workspace_name: str
    workspace_root: Path
    last_commit: tuple[datetime, str] | None  # (when, short subject) or None if no git history
    open_by_assignee: dict[str, int]
    oldest_open_per_assignee: dict[str, int]  # days
    stale_tickets: list[TicketSummary]  # open and >= stale_days old
    blocked_tickets: list[TicketSummary]
    recently_closed: list[TicketSummary]  # closed within recent_days
    runtimes_ok: int
    runtimes_total: int
    stale_days: int


def _parse_iso(value: object) -> datetime | None:
    """Lenient ISO parse — frontmatter dates may be strings or `date` objects."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value)
    # Accept "2026-04-21T00:00:00Z" and bare "2026-04-21"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(s + "T00:00:00+00:00")
        except ValueError:
            return None


def _load_tickets(root: Path) -> list[TicketSummary]:
    out: list[TicketSummary] = []
    tdir = root / "tickets"
    if not tdir.is_dir():
        return out
    for path in sorted(tdir.glob("*.md")):
        try:
            post = frontmatter.load(path)
        except Exception:
            continue
        out.append(TicketSummary(
            id=str(post.get("id") or path.stem),
            title=str(post.get("title") or ""),
            assignee=str(post.get("assignee") or "-"),
            status=str(post.get("status") or "open"),
            created=_parse_iso(post.get("created")),
            updated=_parse_iso(post.get("updated")),
            blocked_on=(str(post.get("blocked_on")) if post.get("blocked_on") else None),
        ))
    return out


def _last_commit(root: Path) -> tuple[datetime, str] | None:
    """Return (commit_time, short_subject) for HEAD, or None if not a git repo / no history."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--pretty=format:%aI%x09%s"],
            capture_output=True, text=True, check=False, timeout=2,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        iso, subject = result.stdout.split("\t", 1)
    except ValueError:
        return None
    when = _parse_iso(iso)
    if when is None:
        return None
    return when, subject.strip()


def _open_oldest_per_assignee(open_tickets: Iterable[TicketSummary]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in open_tickets:
        if t.age_days is None:
            continue
        out[t.assignee] = max(out.get(t.assignee, 0), t.age_days)
    return out


def compute_status(
    root: Path,
    stale_days: int = DEFAULT_STALE_DAYS,
    recent_closed_limit: int | None = DEFAULT_RECENT_CLOSED_LIMIT,
    workspace_name: str | None = None,
    include_runtime_health: bool = True,
) -> StatusReport:
    """Build a StatusReport for the workspace at `root`. Pure: no UI."""
    tickets = _load_tickets(root)
    open_tickets = [t for t in tickets if t.status not in TERMINAL_STATUSES]
    closed = [t for t in tickets if t.status in TERMINAL_STATUSES]

    # Open count by assignee
    open_by_assignee: dict[str, int] = {}
    for t in open_tickets:
        open_by_assignee[t.assignee] = open_by_assignee.get(t.assignee, 0) + 1

    oldest = _open_oldest_per_assignee(open_tickets)

    stale = sorted(
        [t for t in open_tickets if (t.age_days or 0) >= stale_days and not t.is_blocked],
        key=lambda t: (-(t.age_days or 0), t.id),
    )
    blocked = sorted(
        [t for t in open_tickets if t.is_blocked],
        key=lambda t: (-(t.age_days or 0), t.id),
    )

    closed_sorted = sorted(closed, key=lambda t: (t.updated or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    if recent_closed_limit is not None:
        recently_closed = closed_sorted[:recent_closed_limit]
    else:
        recently_closed = closed_sorted

    if include_runtime_health:
        runtime_report = run_doctor(workspace_root=None)
        runtimes_ok = sum(1 for r in runtime_report.runtimes if r.ok)
        runtimes_total = len(runtime_report.runtimes)
    else:
        runtimes_ok = 0
        runtimes_total = 0

    return StatusReport(
        workspace_name=workspace_name or root.name,
        workspace_root=root,
        last_commit=_last_commit(root),
        open_by_assignee=dict(sorted(open_by_assignee.items(), key=lambda kv: -kv[1])),
        oldest_open_per_assignee=oldest,
        stale_tickets=stale,
        blocked_tickets=blocked,
        recently_closed=recently_closed,
        runtimes_ok=runtimes_ok,
        runtimes_total=runtimes_total,
        stale_days=stale_days,
    )
