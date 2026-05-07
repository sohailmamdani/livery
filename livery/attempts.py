"""Durable per-dispatch attempt records.

Every `prepare_dispatch` call writes a small JSON file under the
workspace at `<workspace>/.livery/dispatch/attempts/<attempt-id>.json`
recording who was dispatched, when, where, with what runtime, and
ultimately how it ended. This is the metadata layer underneath the
existing prompt/output files in `/tmp` — those become a cache; the
attempt JSON is the truth.

Why: `dispatch status`, mid-flight cancellation, the `dispatch
continue` command, and the lifecycle hook system all need to find
"the attempts for this ticket" or "the running attempts" without
scanning every output file in /tmp. Filename-as-index does the heavy
lifting (ticket-id is the prefix, so `glob("<ticket-id>-*.json")`
returns just that ticket's attempts).

Schema is versioned (`schema_version: 1`) from day one so future
changes don't require duck-typing; readers compare schema_version
and either handle the version or skip the record.

Atomic writes via `<file>.tmp` + `os.rename` — POSIX guarantees the
rename is atomic on a single filesystem, so concurrent readers never
see a half-written record.
"""

from __future__ import annotations

import json
import os
import secrets
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .paths_safety import sanitize_path_component


SCHEMA_VERSION = 1
"""Bump when the on-disk attempt JSON shape changes incompatibly. Readers
compare this to refuse records they don't understand instead of crashing."""


class AttemptStatus(str, Enum):
    """Lifecycle states for a dispatch attempt.

    String-valued so the JSON form is self-describing — readers don't
    need the Python enum to interpret a `status: "succeeded"` value.
    """

    PREPARED = "prepared"
    """Attempt was created (`prepare_dispatch` ran) but no runtime has been
    launched yet. `dispatch prep` mode leaves attempts in this state until
    something else updates them."""

    RUNNING = "running"
    """Livery launched the runtime subprocess (e.g. via `fan-out --run`).
    PID should be set."""

    SUCCEEDED = "succeeded"
    """Runtime exited cleanly and produced a DISPATCH_SUMMARY block."""

    FAILED = "failed"
    """Runtime exited non-zero or hit a workspace/agent/hook error.
    `failure_class` and `failure_detail` should be set."""

    BLOCKED = "blocked"
    """Runtime decided the work couldn't proceed (e.g. missing credentials,
    upstream blocker). Distinguished from FAILED — agent reported it
    deliberately, not a crash."""

    STALE = "stale"
    """No DISPATCH_SUMMARY and no recent file activity for a long time.
    Inferred at read time by `dispatch status` — never written here.
    Most likely indicates a crashed or stuck subprocess."""

    CANCELLED = "cancelled"
    """Operator cancelled mid-flight (future `ticket close` cancellation
    path)."""

    UNKNOWN = "unknown"
    """Default for records we can't classify. Defensive — never written
    on the happy path."""


class FailureClass(str, Enum):
    """Categorizes why an attempt landed in FAILED.

    Schema-level taxonomy only — populated for the cases dispatch already
    knows how to classify, plus hooks. Broader typed-exception cleanup
    across the codebase comes in a later release; for now everything that
    isn't one of these stays as a plain exception.
    """

    TICKET_ERROR = "ticket_error"
    """Something wrong with the ticket itself (e.g. invalid frontmatter,
    missing assignee). Detected before any subprocess launches."""

    AGENT_CONFIG_ERROR = "agent_config_error"
    """The agent referenced by the ticket doesn't exist, has no `cwd`,
    or has invalid frontmatter."""

    WORKSPACE_ERROR = "workspace_error"
    """Filesystem-level failure: worktree creation failed, path
    containment check rejected the generated path, etc."""

    RUNTIME_ERROR = "runtime_error"
    """Runtime-level failure: command construction failed, the launched
    subprocess exited non-zero, etc."""

    HOOK_ERROR = "hook_error"
    """A pre-run hook (`after_worktree_create` or `before_run`) exited
    non-zero, blocking the runtime from launching. Post-run hook
    failures don't trigger this — they go into `hook_warnings`
    without changing the runtime's success/failure status."""

    NOTIFICATION_ERROR = "notification_error"
    """Out-of-band notification failed (e.g. Telegram unreachable).
    Today these are non-fatal; reserved for future."""


@dataclass(slots=True)
class HookOutcome:
    """One hook execution's outcome — recorded in the attempt JSON's
    `hooks` block. Output text doesn't go inline; see `log_path` for the
    sidecar file holding stdout/stderr."""

    name: str
    """Hook id, e.g. `after_worktree_create`, `before_run`, `after_run`."""

    exit_code: int | None
    """Hook process exit code. None if the hook wasn't configured / didn't
    run for this attempt."""

    duration_seconds: float | None
    """Wall-clock time the hook took. None if it didn't run."""

    log_path: str | None
    """Absolute path to the sidecar log file capturing the hook's stdout
    and stderr. None if the hook didn't run or produced no output."""

    started_at: str | None
    """ISO timestamp (UTC, with `Z` suffix) at hook launch."""


@dataclass(slots=True)
class DispatchAttempt:
    """The on-disk record of a single dispatch attempt.

    JSON-serialized to `<workspace>/.livery/dispatch/attempts/<attempt_id>.json`.
    The dataclass shape mirrors the JSON shape one-to-one so serialization
    is just `asdict` plus enum-to-str.
    """

    schema_version: int
    attempt_id: str
    ticket_id: str
    assignee: str
    runtime: str
    model: str | None
    workspace_root: str
    agent_cwd: str
    worktree_path: str | None
    prompt_path: str
    output_path: str
    command: str
    pid: int | None
    started_at: str
    finished_at: str | None
    exit_code: int | None
    status: AttemptStatus
    failure_class: FailureClass | None
    failure_detail: str | None
    summary_excerpt: list[str]
    hooks: dict[str, HookOutcome | None]
    hook_warnings: list[str]

    def to_json_dict(self) -> dict[str, Any]:
        """Convert to the JSON-friendly dict written to disk."""
        d = asdict(self)
        # Enum → string value (dataclass asdict doesn't unwrap enums)
        d["status"] = self.status.value
        d["failure_class"] = self.failure_class.value if self.failure_class else None
        # hooks: each value is either a HookOutcome dict (already converted by asdict) or None
        return d

    @classmethod
    def from_json_dict(cls, d: dict[str, Any]) -> "DispatchAttempt":
        """Inverse of `to_json_dict`. Tolerant of unknown enum values
        (returns AttemptStatus.UNKNOWN)."""
        try:
            status = AttemptStatus(d.get("status", "unknown"))
        except ValueError:
            status = AttemptStatus.UNKNOWN

        failure_class_raw = d.get("failure_class")
        try:
            failure_class = FailureClass(failure_class_raw) if failure_class_raw else None
        except ValueError:
            failure_class = None

        hooks_raw = d.get("hooks") or {}
        hooks: dict[str, HookOutcome | None] = {}
        for name, outcome in hooks_raw.items():
            if outcome is None:
                hooks[name] = None
            else:
                hooks[name] = HookOutcome(**outcome)

        return cls(
            schema_version=int(d.get("schema_version", 1)),
            attempt_id=str(d["attempt_id"]),
            ticket_id=str(d["ticket_id"]),
            assignee=str(d["assignee"]),
            runtime=str(d["runtime"]),
            model=d.get("model"),
            workspace_root=str(d["workspace_root"]),
            agent_cwd=str(d["agent_cwd"]),
            worktree_path=d.get("worktree_path"),
            prompt_path=str(d["prompt_path"]),
            output_path=str(d["output_path"]),
            command=str(d["command"]),
            pid=d.get("pid"),
            started_at=str(d["started_at"]),
            finished_at=d.get("finished_at"),
            exit_code=d.get("exit_code"),
            status=status,
            failure_class=failure_class,
            failure_detail=d.get("failure_detail"),
            summary_excerpt=list(d.get("summary_excerpt") or []),
            hooks=hooks,
            hook_warnings=list(d.get("hook_warnings") or []),
        )


# -----------------------------------------------------------------------------
# Filesystem layout + id generation
# -----------------------------------------------------------------------------


def attempts_dir(workspace_root: Path) -> Path:
    """Where attempt JSON files live: `<workspace>/.livery/dispatch/attempts/`."""
    return workspace_root / ".livery" / "dispatch" / "attempts"


def ensure_attempts_dir(workspace_root: Path) -> Path:
    """Create the attempts directory plus the `.livery/.gitignore` if missing.

    The .gitignore tells git not to track runtime state (the attempts
    themselves, plus future hook log files). Idempotent — safe to call
    on every dispatch."""
    livery_dir = workspace_root / ".livery"
    livery_dir.mkdir(parents=True, exist_ok=True)

    gitignore = livery_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# Livery runtime state. Not for git.\ndispatch/\nlogs/\n")

    target = attempts_dir(workspace_root)
    target.mkdir(parents=True, exist_ok=True)
    return target


def attempt_id_for(ticket_id: str, assignee: str, *, when: datetime | None = None) -> str:
    """Generate a fresh attempt id of the form
    `<ticket-id>-<assignee>-<YYYYMMDDTHHMMSSZ>-<4hex>`.

    Components:
    - ticket_id is included as a prefix so ticket-scoped lookup is a glob,
      not an open-and-parse loop over every attempt file.
    - assignee disambiguates fan-out attempts on the same ticket.
    - timestamp orders attempts chronologically within a ticket.
    - 4 random hex chars break ties when two attempts land in the same second.

    Both ticket_id and assignee are passed through `sanitize_path_component`
    defensively — the attempt id is part of a filename.
    """
    safe_ticket = sanitize_path_component(ticket_id, fallback="ticket")
    safe_assignee = sanitize_path_component(assignee, fallback="agent")
    ts = (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(2)  # 4 hex chars
    return f"{safe_ticket}-{safe_assignee}-{ts}-{suffix}"


# -----------------------------------------------------------------------------
# Atomic write + read
# -----------------------------------------------------------------------------


def write_attempt(attempt: DispatchAttempt, workspace_root: Path) -> Path:
    """Write `attempt` to its JSON file atomically. Returns the file path.

    Atomic via `<file>.tmp` + `os.rename` — POSIX rename is atomic on a
    single filesystem, so concurrent readers never see a half-written
    record. If the writer crashes mid-write, only the .tmp is leftover
    (cleaned up on next write to the same path).
    """
    target_dir = ensure_attempts_dir(workspace_root)
    final_path = target_dir / f"{attempt.attempt_id}.json"
    tmp_path = final_path.with_suffix(".json.tmp")

    body = json.dumps(attempt.to_json_dict(), indent=2, sort_keys=True) + "\n"
    tmp_path.write_text(body)
    os.replace(tmp_path, final_path)  # atomic on POSIX; works across versions
    return final_path


def load_attempt(path: Path) -> DispatchAttempt:
    """Read an attempt JSON file. Raises ValueError if the schema_version
    is one we don't understand."""
    raw = json.loads(path.read_text())
    schema = raw.get("schema_version", 1)
    if schema > SCHEMA_VERSION:
        raise ValueError(
            f"{path} has schema_version={schema}; this Livery only understands "
            f"up to {SCHEMA_VERSION}. Upgrade Livery."
        )
    return DispatchAttempt.from_json_dict(raw)


def list_attempts(workspace_root: Path) -> list[DispatchAttempt]:
    """All attempts in the workspace, sorted by attempt_id (which is
    chronological because of the timestamp segment).

    Records that fail to load (corrupt, future schema) are skipped with
    no error — the caller gets the records they CAN read."""
    target_dir = attempts_dir(workspace_root)
    if not target_dir.is_dir():
        return []
    out: list[DispatchAttempt] = []
    for path in sorted(target_dir.glob("*.json")):
        try:
            out.append(load_attempt(path))
        except (ValueError, json.JSONDecodeError, KeyError):
            continue
    return out


def find_attempts_for_ticket(workspace_root: Path, ticket_id: str) -> list[DispatchAttempt]:
    """Attempts whose attempt_id starts with `<ticket_id>-`. The filename
    glob makes this O(matching files), not O(all files)."""
    safe_ticket = sanitize_path_component(ticket_id, fallback="ticket")
    target_dir = attempts_dir(workspace_root)
    if not target_dir.is_dir():
        return []
    out: list[DispatchAttempt] = []
    for path in sorted(target_dir.glob(f"{safe_ticket}-*.json")):
        try:
            out.append(load_attempt(path))
        except (ValueError, json.JSONDecodeError, KeyError):
            continue
    return out


def find_workspace_root_from_toml(maybe_workspace_root: Path) -> Path | None:
    """Walk up from a path until we find a `livery.toml`. Returns the
    directory containing it, or None if no workspace marker is found.

    Helper for callers that have an arbitrary path and need to locate
    its enclosing workspace (e.g. `dispatch status` invoked from a
    subdirectory)."""
    current = maybe_workspace_root.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "livery.toml").is_file():
            return candidate
        # Legacy marker: pyproject.toml + livery/
        if (candidate / "pyproject.toml").is_file() and (candidate / "livery").is_dir():
            return candidate
    return None


# -----------------------------------------------------------------------------
# Lifecycle update helpers
# -----------------------------------------------------------------------------


def now_iso() -> str:
    """Current UTC time in the format used throughout attempt records."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mark_running(
    attempt: DispatchAttempt,
    *,
    pid: int,
    workspace_root: Path,
) -> DispatchAttempt:
    """Transition an attempt from PREPARED to RUNNING. Persists. Returns
    the updated record."""
    attempt.status = AttemptStatus.RUNNING
    attempt.pid = pid
    write_attempt(attempt, workspace_root)
    return attempt


def mark_finished(
    attempt: DispatchAttempt,
    *,
    exit_code: int,
    workspace_root: Path,
    summary_excerpt: list[str] | None = None,
) -> DispatchAttempt:
    """Transition a RUNNING attempt to terminal state based on `exit_code`.

    Exit 0 → SUCCEEDED. Non-zero → FAILED with `runtime_error` failure
    class. Populates `finished_at` and `summary_excerpt` if provided.
    """
    attempt.exit_code = exit_code
    attempt.finished_at = now_iso()
    if summary_excerpt is not None:
        attempt.summary_excerpt = summary_excerpt
    if exit_code == 0:
        attempt.status = AttemptStatus.SUCCEEDED
        attempt.failure_class = None
        attempt.failure_detail = None
    else:
        attempt.status = AttemptStatus.FAILED
        attempt.failure_class = FailureClass.RUNTIME_ERROR
        attempt.failure_detail = f"runtime exited non-zero ({exit_code})"
    write_attempt(attempt, workspace_root)
    return attempt
