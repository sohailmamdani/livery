"""Append concise, concurrency-safe updates to Livery ticket threads.

Ticket markdown is the human-readable work record. Dispatch attempt JSON keeps
the full machine/audit state; this module mirrors meaningful lifecycle events
and agent-authored milestones into the ticket without copying raw logs or
runtime commands.
"""

from __future__ import annotations

import fcntl
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

import frontmatter

from .paths_safety import sanitize_path_component

if TYPE_CHECKING:
    from .attempts import DispatchAttempt


UPDATE_KINDS: tuple[str, ...] = (
    "note",
    "progress",
    "decision",
    "blocker",
    "dispatch",
    "result",
)

_THREAD_HEADING = re.compile(r"(?m)^## Thread\s*$")
_NEXT_H2 = re.compile(r"(?m)^## (?!Thread\s*$).+$")


@dataclass(frozen=True, slots=True)
class TicketUpdate:
    ticket_id: str
    path: Path
    actor: str
    kind: str
    timestamp: str
    message: str
    event_id: str | None
    appended: bool


def find_ticket_path(workspace_root: Path, query: str) -> Path:
    """Find one ticket by exact id/filename or unique filename fragment."""
    tickets_dir = workspace_root / "tickets"
    if not tickets_dir.is_dir():
        raise ValueError(f"ticket directory not found: {tickets_dir}")

    safe_query = sanitize_path_component(query, fallback="ticket")
    exact = tickets_dir / f"{safe_query}.md"
    if safe_query == query and exact.is_file():
        return exact

    matches = sorted(path for path in tickets_dir.glob("*.md") if query in path.name)
    if not matches:
        raise ValueError(f"No ticket matching '{query}'")
    if len(matches) > 1:
        labels = ", ".join(path.stem for path in matches)
        raise ValueError(f"Multiple tickets match '{query}': {labels}")
    return matches[0]


@contextmanager
def _ticket_lock(workspace_root: Path, ticket_id: str) -> Iterator[None]:
    """Serialize ticket rewrites across concurrent fan-out agents."""
    from .attempts import ensure_runtime_gitignore

    ensure_runtime_gitignore(workspace_root)
    lock_dir = workspace_root / ".livery" / "ticket-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{sanitize_path_component(ticket_id, fallback='ticket')}.lock"
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _event_marker(event_id: str | None) -> str | None:
    if not event_id:
        return None
    safe = re.sub(r"[^A-Za-z0-9_.:]", "-", event_id)
    safe = re.sub(r"-+", "-", safe).strip("-")
    if not safe:
        raise ValueError("event_id must contain at least one letter or number")
    return f"<!-- livery-event:{safe} -->"


def _append_to_thread(content: str, entry: str) -> str:
    """Append inside ``## Thread`` even when later H2 sections exist."""
    thread_match = _THREAD_HEADING.search(content)
    if thread_match is None:
        return content.rstrip() + f"\n\n## Thread\n\n{entry}\n"

    next_heading = _NEXT_H2.search(content, thread_match.end())
    if next_heading is None:
        return content.rstrip() + f"\n\n{entry}\n"

    before = content[: next_heading.start()].rstrip()
    after = content[next_heading.start() :].lstrip("\n")
    return f"{before}\n\n{entry}\n\n{after.rstrip()}\n"


def append_ticket_update(
    *,
    workspace_root: Path,
    ticket_path: Path,
    actor: str,
    kind: str,
    message: str,
    timestamp: str,
    event_id: str | None = None,
) -> TicketUpdate:
    """Append one update, atomically and idempotently when ``event_id`` is set."""
    actor = actor.strip()
    message = message.strip()
    kind = kind.strip().lower()
    if not actor:
        raise ValueError("ticket update actor cannot be blank")
    if not message:
        raise ValueError("ticket update message cannot be blank")
    if kind not in UPDATE_KINDS:
        raise ValueError(f"ticket update kind must be one of: {', '.join(UPDATE_KINDS)}")

    marker = _event_marker(event_id)
    with _ticket_lock(workspace_root, ticket_path.stem):
        raw = ticket_path.read_text()
        post = frontmatter.loads(raw)
        ticket_id = str(post.get("id") or ticket_path.stem)
        if marker and marker in raw:
            return TicketUpdate(
                ticket_id=ticket_id,
                path=ticket_path,
                actor=actor,
                kind=kind,
                timestamp=timestamp,
                message=message,
                event_id=event_id,
                appended=False,
            )

        heading = f"### {timestamp} — {actor} — {kind}"
        entry = f"{heading}\n{message}"
        if marker:
            entry += f"\n{marker}"
        post.content = _append_to_thread(post.content, entry)
        post["updated"] = timestamp
        rendered = frontmatter.dumps(post) + "\n"

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{ticket_path.name}.",
            suffix=".tmp",
            dir=ticket_path.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w") as tmp_file:
                tmp_file.write(rendered)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.chmod(tmp_name, stat.S_IMODE(ticket_path.stat().st_mode))
            os.replace(tmp_name, ticket_path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    return TicketUpdate(
        ticket_id=ticket_id,
        path=ticket_path,
        actor=actor,
        kind=kind,
        timestamp=timestamp,
        message=message,
        event_id=event_id,
        appended=True,
    )


def _summary_fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and value.strip():
            fields[key.strip().lower()] = value.strip()
    return fields


def _effective_terminal_status(attempt: DispatchAttempt) -> str | None:
    status = attempt.status.value
    fields = _summary_fields(attempt.summary_excerpt)
    reported = fields.get("status", "").lower()
    if reported == "blocked":
        return "blocked"
    if status == "succeeded" and not attempt.summary_excerpt:
        # Managed execution still has a post-processing step that turns this
        # into failure when the runtime omitted DISPATCH_SUMMARY.
        return None
    if status in {"succeeded", "failed", "blocked", "cancelled", "stale"}:
        return status
    return None


def _attempt_event(attempt: DispatchAttempt) -> tuple[str, str, str] | None:
    """Return ``(kind, message, event_suffix)`` for the attempt's state."""
    attempt_ref = f"`{attempt.attempt_id}`"
    if attempt.status.value == "prepared":
        runtime = f"`{attempt.runtime}`"
        if attempt.model:
            runtime += f" / `{attempt.model}`"
        location = (
            f"an isolated worktree (`{Path(attempt.worktree_path).name}`)"
            if attempt.worktree_path
            else "the agent's configured working directory"
        )
        return (
            "dispatch",
            f"Prepared dispatch {attempt_ref} for **{attempt.assignee}** using "
            f"{runtime} in {location}.",
            "prepared",
        )
    if attempt.status.value == "running":
        return (
            "dispatch",
            f"**{attempt.assignee}** started work in dispatch {attempt_ref}.",
            "running",
        )

    terminal = _effective_terminal_status(attempt)
    if terminal is None:
        return None
    fields = _summary_fields(attempt.summary_excerpt)
    summary = fields.get("summary")
    if terminal == "succeeded":
        lead = f"**{attempt.assignee}** completed dispatch {attempt_ref}"
        lead += f": {summary}" if summary else "."
        details: list[str] = []
        for label, key in (("Files", "files touched"), ("Tests", "tests run")):
            value = fields.get(key)
            if value and value.lower() != "none":
                details.append(f"- {label}: {value}")
        flags = next(
            (value for key, value in fields.items() if key.startswith("pushback / flags")),
            None,
        )
        if flags and flags.lower() != "none":
            details.append(f"- Flags: {flags}")
        message = "\n".join([lead, *details])
        return "result", message, "terminal:succeeded"
    if terminal == "blocked":
        reason = summary or attempt.failure_detail or "The agent reported a blocker."
        return (
            "blocker",
            f"**{attempt.assignee}** was blocked in dispatch {attempt_ref}: {reason}",
            "terminal:blocked",
        )
    if terminal == "cancelled":
        return (
            "result",
            f"Dispatch {attempt_ref} for **{attempt.assignee}** was cancelled.",
            "terminal:cancelled",
        )

    detail = attempt.failure_detail or (
        f"runtime exited with code {attempt.exit_code}"
        if attempt.exit_code is not None
        else "no failure detail was recorded"
    )
    return (
        "result",
        f"Dispatch {attempt_ref} for **{attempt.assignee}** failed: {detail.rstrip('.')}.",
        f"terminal:{terminal}",
    )


def sync_attempt_to_ticket(attempt: DispatchAttempt, workspace_root: Path) -> TicketUpdate | None:
    """Mirror one meaningful attempt state into its real ticket, if present."""
    try:
        ticket_path = find_ticket_path(workspace_root, attempt.ticket_id)
    except ValueError:
        # Schedules and Walkie-Talkies intentionally use pseudo ticket ids.
        return None

    event = _attempt_event(attempt)
    if event is None:
        return None
    kind, message, suffix = event
    return append_ticket_update(
        workspace_root=workspace_root,
        ticket_path=ticket_path,
        actor="livery",
        kind=kind,
        message=message,
        timestamp=attempt.finished_at or attempt.started_at,
        event_id=f"dispatch:{attempt.attempt_id}:{suffix}",
    )
