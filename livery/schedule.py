"""Portable schedule definitions and scheduled-dispatch execution."""

from __future__ import annotations

import json
import os
import re
import shutil
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import frontmatter

from .attempts import (
    SCHEMA_VERSION,
    AttemptStatus,
    DispatchAttempt,
    attempt_id_for,
    ensure_runtime_gitignore,
    now_iso,
    write_attempt,
)
from .dispatch import (
    DISPATCH_SUMMARY_BLOCK,
    WORKER_DISCOVERY_HINT,
    DispatchPrep,
    build_runtime_command,
    ensure_worktree,
)
from .dispatch_hooks import get_hook_command, run_pre_run_hook

SCHEDULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MISSED_GRACE_SECONDS = 5 * 60
DEFAULT_TASK_PLACEHOLDER = "Describe the scheduled task here."


@dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    id: str
    assignee: str
    kind: str
    task: str
    path: Path
    time: str | None = None
    days: tuple[str, ...] = ()
    interval_seconds: int | None = None
    at: str | None = None
    overlap: str = "skip"
    missed_run: str = "once"
    worktree: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "assignee": self.assignee,
            "trigger": {
                "kind": self.kind,
                "time": self.time,
                "days": list(self.days),
                "interval_seconds": self.interval_seconds,
                "at": self.at,
            },
            "overlap": self.overlap,
            "missed_run": self.missed_run,
            "worktree": self.worktree,
            "task": self.task,
            "path": str(self.path),
        }


@dataclass(slots=True)
class ScheduleRunResult:
    schedule_id: str
    outcome: str
    occurrence: str
    attempt_id: str | None = None
    attempt_path: str | None = None
    output_path: str | None = None
    exit_code: int = 0
    detail: str | None = None


def schedules_dir(workspace_root: Path) -> Path:
    return workspace_root / "schedules"


def schedule_runtime_dir(workspace_root: Path) -> Path:
    return workspace_root / ".livery" / "schedules"


def ensure_schedule_runtime_dir(workspace_root: Path) -> Path:
    ensure_runtime_gitignore(workspace_root)
    path = schedule_runtime_dir(workspace_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def schedule_state_path(workspace_root: Path, schedule_id: str) -> Path:
    return schedule_runtime_dir(workspace_root) / "state" / f"{schedule_id}.json"


def _validate_id(schedule_id: str) -> str:
    if not SCHEDULE_ID_RE.fullmatch(schedule_id):
        raise ValueError(
            "schedule id must be 1-63 lowercase letters, digits, or hyphens "
            "and must start with a letter or digit"
        )
    return schedule_id


def parse_time(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        raise ValueError(f"invalid time {value!r}; expected HH:MM")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"invalid time {value!r}; expected HH:MM")
    return hour, minute


def parse_duration(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", value.lower())
    if not match:
        raise ValueError("interval must look like 30m, 2h, or 1d")
    amount = int(match.group(1))
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    seconds = amount * multiplier
    if seconds < 60:
        raise ValueError("minimum schedule interval is 60 seconds")
    return seconds


def parse_at(value: str) -> str:
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--at must be an RFC 3339 timestamp with a timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError("--at must include a timezone offset or Z")
    return parsed.isoformat()


def _normalized_days(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError("calendar trigger days must be a list")
    days = tuple(str(day).lower()[:3] for day in raw)
    if not days or any(day not in DAY_NAMES for day in days):
        raise ValueError(f"calendar trigger days must use: {', '.join(DAY_NAMES)}")
    if len(set(days)) != len(days):
        raise ValueError("calendar trigger days cannot contain duplicates")
    return days


def load_schedule(path: Path) -> ScheduleDefinition:
    post = frontmatter.load(path)
    schedule_id = _validate_id(str(post.get("id") or path.stem))
    assignee = str(post.get("assignee") or "").strip()
    if not assignee or assignee == "cos":
        raise ValueError(f"schedule {schedule_id} requires a hired-agent assignee")

    trigger = post.get("trigger")
    if not isinstance(trigger, dict):
        raise ValueError(f"schedule {schedule_id} requires a trigger table")
    kind = str(trigger.get("kind") or "")
    if kind not in {"calendar", "interval", "once"}:
        raise ValueError(
            f"schedule {schedule_id} has unsupported trigger kind {kind!r}"
        )

    time_value: str | None = None
    days: tuple[str, ...] = ()
    interval_seconds: int | None = None
    at: str | None = None
    if kind == "calendar":
        time_value = str(trigger.get("time") or "")
        parse_time(time_value)
        days = _normalized_days(trigger.get("days"))
        if not days:
            days = DAY_NAMES
    elif kind == "interval":
        try:
            interval_seconds = int(trigger.get("interval_seconds"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"schedule {schedule_id} requires interval_seconds"
            ) from exc
        if interval_seconds < 60:
            raise ValueError("minimum schedule interval is 60 seconds")
    else:
        at = parse_at(str(trigger.get("at") or ""))

    overlap = str(post.get("overlap") or "skip")
    if overlap not in {"skip", "allow"}:
        raise ValueError("overlap must be 'skip' or 'allow'")
    missed_run = str(post.get("missed_run") or "once")
    if missed_run not in {"once", "skip"}:
        raise ValueError("missed_run must be 'once' or 'skip'")
    if kind == "interval" and missed_run != "skip":
        raise ValueError("interval schedules require missed_run: skip")

    task = post.content.strip()
    if not task:
        raise ValueError(f"schedule {schedule_id} has an empty task body")

    return ScheduleDefinition(
        id=schedule_id,
        assignee=assignee,
        kind=kind,
        task=task,
        path=path,
        time=time_value,
        days=days,
        interval_seconds=interval_seconds,
        at=at,
        overlap=overlap,
        missed_run=missed_run,
        worktree=bool(post.get("worktree", False)),
    )


def find_schedule(workspace_root: Path, query: str) -> ScheduleDefinition:
    if SCHEDULE_ID_RE.fullmatch(query):
        exact = schedules_dir(workspace_root) / f"{query}.md"
        if exact.is_file():
            return load_schedule(exact)
    matches = [p for p in schedules_dir(workspace_root).glob("*.md") if query in p.stem]
    if not matches:
        raise ValueError(f"schedule not found: {query}")
    if len(matches) > 1:
        names = ", ".join(sorted(p.stem for p in matches))
        raise ValueError(f"ambiguous schedule {query!r}; matches: {names}")
    return load_schedule(matches[0])


def list_schedules(workspace_root: Path) -> list[ScheduleDefinition]:
    directory = schedules_dir(workspace_root)
    if not directory.is_dir():
        return []
    return [load_schedule(path) for path in sorted(directory.glob("*.md"))]


def create_schedule(
    *,
    workspace_root: Path,
    schedule_id: str,
    assignee: str,
    task: str,
    kind: str,
    time_value: str | None = None,
    days: tuple[str, ...] = (),
    interval_seconds: int | None = None,
    at: str | None = None,
    overlap: str = "skip",
    missed_run: str = "once",
    worktree: bool = False,
) -> Path:
    schedule_id = _validate_id(schedule_id)
    if not assignee.strip() or assignee == "cos":
        raise ValueError("schedule requires a hired-agent assignee")
    if not task.strip():
        raise ValueError(f"schedule {schedule_id} has an empty task body")
    if overlap not in {"skip", "allow"}:
        raise ValueError("overlap must be 'skip' or 'allow'")
    if missed_run not in {"once", "skip"}:
        raise ValueError("missed_run must be 'once' or 'skip'")
    directory = schedules_dir(workspace_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{schedule_id}.md"
    if path.exists():
        raise FileExistsError(f"schedule already exists: {path}")

    trigger: dict[str, object] = {"kind": kind}
    if kind == "calendar":
        if time_value is None:
            raise ValueError("calendar schedule requires a time")
        parse_time(time_value)
        normalized_days = _normalized_days(days)
        trigger.update(time=time_value, days=list(normalized_days))
    elif kind == "interval":
        if interval_seconds is None or interval_seconds < 60:
            raise ValueError("interval schedule requires at least 60 seconds")
        trigger["interval_seconds"] = interval_seconds
        # Native interval timers resume from their next tick; neither launchd
        # nor systemd replays every elapsed interval. Keep the tracked policy
        # truthful rather than promising catch-up that cannot be portable.
        missed_run = "skip"
    elif kind == "once":
        if at is None:
            raise ValueError("one-shot schedule requires --at")
        trigger["at"] = parse_at(at)
    else:
        raise ValueError(f"unsupported schedule kind: {kind}")

    metadata = {
        "id": schedule_id,
        "assignee": assignee,
        "trigger": trigger,
        "overlap": overlap,
        "missed_run": missed_run,
        "worktree": worktree,
    }
    path.write_text(
        frontmatter.dumps(frontmatter.Post(task.strip() + "\n", **metadata)) + "\n"
    )
    load_schedule(path)
    return path


def compose_schedule_prompt(
    definition: ScheduleDefinition, agents_md: str, run_id: str
) -> str:
    return "\n".join(
        [
            f'You are acting as the "{definition.assignee}" agent in a scheduled Livery run.',
            "",
            "---BEGIN AGENTS.md---",
            "",
            agents_md.rstrip(),
            "",
            "---END AGENTS.md---",
            "",
            WORKER_DISCOVERY_HINT.rstrip(),
            "",
            DISPATCH_SUMMARY_BLOCK.format(ticket_id=run_id).rstrip(),
            "",
            f"Schedule: {definition.id}",
            "This is unattended scheduled work. Do not ask the operator interactive questions.",
            "If a required dependency or credential is unavailable, report Status: blocked.",
            "",
            "---BEGIN SCHEDULED TASK---",
            "",
            definition.task.rstrip(),
            "",
            "---END SCHEDULED TASK---",
            "",
            "Proceed.",
            "",
        ]
    )


def prepare_schedule_dispatch(
    *,
    workspace_root: Path,
    definition: ScheduleDefinition,
    occurrence: str,
    trigger: str = "schedule",
) -> DispatchPrep:
    agent_dir = workspace_root / "agents" / definition.assignee
    agent_md_path = agent_dir / "agent.md"
    agents_md_path = agent_dir / "AGENTS.md"
    if not agent_md_path.is_file():
        raise ValueError(
            f"agent '{definition.assignee}' not found: missing {agent_md_path}"
        )
    if not agents_md_path.is_file():
        raise ValueError(
            f"agent '{definition.assignee}' missing system prompt: {agents_md_path}"
        )

    agent_post = frontmatter.load(agent_md_path)
    runtime = str(agent_post.get("runtime") or "codex")
    model = str(agent_post.get("model")) if agent_post.get("model") else None
    effort = str(agent_post.get("effort")) if agent_post.get("effort") else None
    cwd_raw = agent_post.get("cwd")
    if not cwd_raw:
        raise ValueError(f"agent '{definition.assignee}' has no cwd in agent.md")
    agent_cwd = str(cwd_raw)

    run_id = f"schedule-{definition.id}"
    attempt_id = attempt_id_for(run_id, definition.assignee)
    actual_cwd = agent_cwd
    worktree_path: Path | None = None
    if definition.worktree:
        worktree_path, _ = ensure_worktree(
            repo=Path(agent_cwd),
            ticket_id=attempt_id,
            agent_id=definition.assignee,
        )
        actual_cwd = str(worktree_path)

    run_dir = (
        ensure_schedule_runtime_dir(workspace_root)
        / "runs"
        / definition.id
        / attempt_id
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    prompt_path = run_dir / "prompt.txt"
    output_path = run_dir / "output.log"
    prompt_path.write_text(
        compose_schedule_prompt(definition, agents_md_path.read_text(), run_id)
    )
    command = build_runtime_command(
        runtime=runtime,
        model=model,
        effort=effort,
        cwd=actual_cwd,
        prompt_path=prompt_path,
        output_path=output_path,
    )
    attempt = DispatchAttempt(
        schema_version=SCHEMA_VERSION,
        attempt_id=attempt_id,
        ticket_id=run_id,
        assignee=definition.assignee,
        runtime=runtime,
        model=model,
        workspace_root=str(workspace_root),
        agent_cwd=agent_cwd,
        worktree_path=str(worktree_path) if worktree_path else None,
        prompt_path=str(prompt_path),
        output_path=str(output_path),
        command=command,
        pid=None,
        started_at=now_iso(),
        finished_at=None,
        exit_code=None,
        status=AttemptStatus.PREPARED,
        failure_class=None,
        failure_detail=None,
        summary_excerpt=[],
        hooks={},
        hook_warnings=[],
        schedule_id=definition.id,
        scheduled_for=occurrence,
        trigger=trigger,
    )
    attempt_path = write_attempt(attempt, workspace_root)

    if worktree_path is not None:
        from .config import load as load_config

        hook_cmd = get_hook_command(
            load_config(workspace_root).raw, "after_worktree_create"
        )
        if hook_cmd:
            _, ok = run_pre_run_hook(
                hook_name="after_worktree_create",
                command=hook_cmd,
                attempt=attempt,
                workspace_root=workspace_root,
            )
            if not ok:
                raise RuntimeError(
                    f"after_worktree_create hook failed for {attempt_id}; "
                    f"see {attempt.hooks['after_worktree_create'].log_path}"
                )

    return DispatchPrep(
        ticket_id=run_id,
        assignee=definition.assignee,
        runtime=runtime,
        model=model,
        effort=effort,
        cwd=actual_cwd,
        prompt_path=prompt_path,
        output_path=output_path,
        command=command,
        attempt_id=attempt_id,
        attempt_path=attempt_path,
    )


def _read_state(workspace_root: Path, schedule_id: str) -> dict[str, object]:
    path = schedule_state_path(workspace_root, schedule_id)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_state(
    workspace_root: Path, schedule_id: str, state: dict[str, object]
) -> None:
    ensure_schedule_runtime_dir(workspace_root)
    path = schedule_state_path(workspace_root, schedule_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def schedule_lock(workspace_root: Path, schedule_id: str) -> Iterator[bool]:
    lock_dir = (
        ensure_schedule_runtime_dir(workspace_root) / "locks" / f"{schedule_id}.lock"
    )
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    try:
        try:
            lock_dir.mkdir()
            (lock_dir / "pid").write_text(f"{os.getpid()}\n")
            acquired = True
        except FileExistsError:
            try:
                existing_pid = int((lock_dir / "pid").read_text().strip())
            except (OSError, ValueError):
                existing_pid = -1
            if existing_pid < 1 or not _pid_is_alive(existing_pid):
                shutil.rmtree(lock_dir)
                lock_dir.mkdir()
                (lock_dir / "pid").write_text(f"{os.getpid()}\n")
                acquired = True
        yield acquired
    finally:
        if acquired:
            shutil.rmtree(lock_dir, ignore_errors=True)


def _latest_calendar_occurrence(
    definition: ScheduleDefinition, now: datetime
) -> datetime:
    hour, minute = parse_time(definition.time or "")
    allowed = {DAY_NAMES.index(day) for day in definition.days}
    for offset in range(8):
        day = now - timedelta(days=offset)
        candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.weekday() in allowed and candidate <= now:
            return candidate
    raise RuntimeError("could not determine a calendar occurrence")


def occurrence_for(
    definition: ScheduleDefinition, *, now: datetime, manual: bool
) -> tuple[str, float]:
    if now.tzinfo is None:
        now = now.astimezone()
    if manual:
        return f"manual:{now.isoformat()}", 0.0
    if definition.kind == "calendar":
        occurrence = _latest_calendar_occurrence(definition, now)
        return occurrence.isoformat(), (now - occurrence).total_seconds()
    if definition.kind == "once":
        occurrence = datetime.fromisoformat(
            (definition.at or "").replace("Z", "+00:00")
        )
        return occurrence.isoformat(), (
            now.astimezone(timezone.utc) - occurrence.astimezone(timezone.utc)
        ).total_seconds()
    return now.isoformat(), 0.0


def run_schedule(
    *,
    workspace_root: Path,
    definition: ScheduleDefinition,
    manual: bool = False,
    now: datetime | None = None,
) -> ScheduleRunResult:
    from .dispatch_runner import execute_prepared_dispatch

    current = now or datetime.now().astimezone()
    occurrence, delay = occurrence_for(definition, now=current, manual=manual)
    state = _read_state(workspace_root, definition.id)

    if not manual and state.get("last_occurrence") == occurrence:
        return ScheduleRunResult(definition.id, "already-ran", occurrence)
    if not manual and definition.kind == "once" and delay < 0:
        return ScheduleRunResult(definition.id, "not-due", occurrence)
    if (
        not manual
        and definition.missed_run == "skip"
        and definition.kind in {"calendar", "once"}
        and delay > MISSED_GRACE_SECONDS
    ):
        state.update(last_occurrence=occurrence, last_skipped_at=now_iso())
        state["skipped_count"] = int(state.get("skipped_count", 0)) + 1
        _write_state(workspace_root, definition.id, state)
        return ScheduleRunResult(
            definition.id,
            "missed-skipped",
            occurrence,
            detail=f"occurrence was {int(delay)} seconds late",
        )

    lock_context = (
        schedule_lock(workspace_root, definition.id)
        if definition.overlap == "skip"
        else nullcontext(True)
    )
    with lock_context as acquired:
        if not acquired:
            state["overlap_skipped_count"] = (
                int(state.get("overlap_skipped_count", 0)) + 1
            )
            state["last_overlap_skipped_at"] = now_iso()
            _write_state(workspace_root, definition.id, state)
            return ScheduleRunResult(definition.id, "overlap-skipped", occurrence)

        prep = prepare_schedule_dispatch(
            workspace_root=workspace_root,
            definition=definition,
            occurrence=occurrence,
            trigger="manual" if manual else "schedule",
        )
        state.update(
            last_occurrence=occurrence,
            last_started_at=now_iso(),
            last_attempt_id=prep.attempt_id,
            last_attempt_path=str(prep.attempt_path),
            last_output_path=str(prep.output_path),
        )
        _write_state(workspace_root, definition.id, state)
        execution = execute_prepared_dispatch(prep, workspace_root=workspace_root)
        state.update(
            last_finished_at=now_iso(),
            last_exit_code=execution.exit_code,
        )
        state["run_count"] = int(state.get("run_count", 0)) + 1
        _write_state(workspace_root, definition.id, state)
        return ScheduleRunResult(
            schedule_id=definition.id,
            outcome=execution.status.value,
            occurrence=occurrence,
            attempt_id=execution.attempt_id,
            attempt_path=str(execution.attempt_path),
            output_path=str(execution.output_path),
            exit_code=execution.exit_code,
        )


def schedule_state(workspace_root: Path, schedule_id: str) -> dict[str, object]:
    return _read_state(workspace_root, schedule_id)


def initialize_installation_state(
    workspace_root: Path,
    definition: ScheduleDefinition,
    *,
    now: datetime | None = None,
) -> None:
    """Baseline a fresh host installation without replaying pre-install history."""
    state = _read_state(workspace_root, definition.id)
    if state.get("installed_at"):
        return
    current = now or datetime.now().astimezone()
    state["installed_at"] = current.isoformat()
    if definition.kind == "calendar":
        occurrence, _ = occurrence_for(definition, now=current, manual=False)
        state["last_occurrence"] = occurrence
    _write_state(workspace_root, definition.id, state)


def mark_schedule_uninstalled(workspace_root: Path, schedule_id: str) -> None:
    state = _read_state(workspace_root, schedule_id)
    state.pop("installed_at", None)
    state["uninstalled_at"] = datetime.now().astimezone().isoformat()
    _write_state(workspace_root, schedule_id, state)
