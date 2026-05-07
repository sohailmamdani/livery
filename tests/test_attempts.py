from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from livery.attempts import (
    SCHEMA_VERSION,
    AttemptStatus,
    DispatchAttempt,
    FailureClass,
    HookOutcome,
    attempt_id_for,
    attempts_dir,
    ensure_attempts_dir,
    find_attempts_for_ticket,
    list_attempts,
    load_attempt,
    mark_finished,
    mark_running,
    now_iso,
    write_attempt,
)


def _stub_attempt(
    *,
    ticket_id: str = "2026-05-07-001-x",
    assignee: str = "lead-dev",
    workspace_root: Path | None = None,
    status: AttemptStatus = AttemptStatus.PREPARED,
) -> DispatchAttempt:
    """Minimal-valid DispatchAttempt for tests."""
    return DispatchAttempt(
        schema_version=SCHEMA_VERSION,
        attempt_id=attempt_id_for(ticket_id, assignee),
        ticket_id=ticket_id,
        assignee=assignee,
        runtime="codex",
        model="gpt-5-codex",
        workspace_root=str(workspace_root or Path("/tmp/ws")),
        agent_cwd="/tmp/repo",
        worktree_path=None,
        prompt_path="/tmp/livery-dispatch-x.txt",
        output_path="/tmp/livery-dispatch-x.out",
        command="codex exec ...",
        pid=None,
        started_at=now_iso(),
        finished_at=None,
        exit_code=None,
        status=status,
        failure_class=None,
        failure_detail=None,
        summary_excerpt=[],
        hooks={},
        hook_warnings=[],
    )


# -----------------------------------------------------------------------------
# attempt_id_for
# -----------------------------------------------------------------------------


def test_attempt_id_starts_with_ticket_id():
    """Filename glob (`<ticket-id>-*.json`) is the index — ticket id MUST be the prefix."""
    aid = attempt_id_for("2026-05-07-001-x", "lead-dev")
    assert aid.startswith("2026-05-07-001-x-lead-dev-")


def test_attempt_id_includes_timestamp_and_hex():
    """Timestamp-then-hex tail makes ids sortable + collision-resistant within a ticket."""
    aid = attempt_id_for("t", "a")
    parts = aid.split("-")
    # Last segment is 4 hex chars
    assert len(parts[-1]) == 4
    assert all(c in "0123456789abcdef" for c in parts[-1])
    # Second-to-last is the timestamp, format YYYYMMDDTHHMMSSZ
    ts = parts[-2]
    assert len(ts) == 16
    assert ts.endswith("Z")
    assert "T" in ts


def test_attempt_id_sanitizes_dangerous_inputs():
    """Path-traversal payloads in ticket_id or assignee can't escape — no
    path separators, no leading-dot tokens. Literal `..` substrings inside
    a single filename component are harmless (they can't traverse without
    a `/`)."""
    aid = attempt_id_for("../../../etc/passwd", "../evil")
    assert "/" not in aid
    assert "\\" not in aid
    # The result is a single filename component, not a path
    assert not aid.startswith(".")


def test_attempt_id_two_calls_same_second_collide_via_hex():
    """Two attempts in the same second have different hex tails."""
    when = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    a = attempt_id_for("t", "a", when=when)
    b = attempt_id_for("t", "a", when=when)
    assert a != b


# -----------------------------------------------------------------------------
# write_attempt + load_attempt + ensure_attempts_dir
# -----------------------------------------------------------------------------


def test_ensure_attempts_dir_creates_layout(tmp_path):
    """First call creates `.livery/dispatch/attempts/` AND a `.livery/.gitignore`."""
    target = ensure_attempts_dir(tmp_path)
    assert target == tmp_path / ".livery" / "dispatch" / "attempts"
    assert target.is_dir()
    gitignore = tmp_path / ".livery" / ".gitignore"
    assert gitignore.is_file()
    assert "dispatch/" in gitignore.read_text()


def test_ensure_attempts_dir_idempotent(tmp_path):
    """Re-calling doesn't clobber an existing .gitignore."""
    ensure_attempts_dir(tmp_path)
    gitignore = tmp_path / ".livery" / ".gitignore"
    gitignore.write_text("# user customized\ncustom-line\ndispatch/\nlogs/\n")
    ensure_attempts_dir(tmp_path)
    # Still has user's customization
    assert "custom-line" in gitignore.read_text()


def test_write_and_load_attempt_roundtrip(tmp_path):
    attempt = _stub_attempt(workspace_root=tmp_path)
    path = write_attempt(attempt, tmp_path)
    assert path.exists()
    assert path.suffix == ".json"
    loaded = load_attempt(path)
    assert loaded.attempt_id == attempt.attempt_id
    assert loaded.ticket_id == attempt.ticket_id
    assert loaded.status == attempt.status
    assert loaded.runtime == attempt.runtime


def test_write_attempt_is_atomic(tmp_path):
    """No `.tmp` should be left around after a successful write."""
    attempt = _stub_attempt(workspace_root=tmp_path)
    write_attempt(attempt, tmp_path)
    target_dir = attempts_dir(tmp_path)
    leftovers = list(target_dir.glob("*.tmp"))
    assert leftovers == []


def test_load_attempt_rejects_future_schema(tmp_path):
    """A record from a newer Livery refuses to load — better than silently
    dropping fields."""
    attempt = _stub_attempt(workspace_root=tmp_path)
    path = write_attempt(attempt, tmp_path)
    raw = json.loads(path.read_text())
    raw["schema_version"] = SCHEMA_VERSION + 99
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_attempt(path)


def test_load_attempt_handles_unknown_status_as_unknown(tmp_path):
    """Forward-compat: a status string we don't know should map to AttemptStatus.UNKNOWN."""
    attempt = _stub_attempt(workspace_root=tmp_path)
    path = write_attempt(attempt, tmp_path)
    raw = json.loads(path.read_text())
    raw["status"] = "some-future-status"
    path.write_text(json.dumps(raw))
    loaded = load_attempt(path)
    assert loaded.status == AttemptStatus.UNKNOWN


def test_to_json_dict_contains_string_enum_values(tmp_path):
    attempt = _stub_attempt()
    attempt.status = AttemptStatus.SUCCEEDED
    attempt.failure_class = FailureClass.RUNTIME_ERROR
    d = attempt.to_json_dict()
    assert d["status"] == "succeeded"
    assert d["failure_class"] == "runtime_error"


# -----------------------------------------------------------------------------
# find_attempts_for_ticket + list_attempts
# -----------------------------------------------------------------------------


def test_find_attempts_for_ticket_uses_glob(tmp_path):
    """Ticket-scoped lookup returns only that ticket's attempts."""
    a1 = _stub_attempt(ticket_id="2026-05-07-001-x", assignee="lead-dev", workspace_root=tmp_path)
    a2 = _stub_attempt(ticket_id="2026-05-07-001-x", assignee="qa", workspace_root=tmp_path)
    other = _stub_attempt(ticket_id="2026-05-07-002-y", assignee="lead-dev", workspace_root=tmp_path)
    write_attempt(a1, tmp_path)
    write_attempt(a2, tmp_path)
    write_attempt(other, tmp_path)

    found = find_attempts_for_ticket(tmp_path, "2026-05-07-001-x")
    found_ids = {a.attempt_id for a in found}
    assert a1.attempt_id in found_ids
    assert a2.attempt_id in found_ids
    assert other.attempt_id not in found_ids


def test_find_attempts_for_ticket_empty_when_none(tmp_path):
    assert find_attempts_for_ticket(tmp_path, "no-such-ticket") == []


def test_list_attempts_returns_chronological(tmp_path):
    """list_attempts sorts by attempt_id, which is chronological by construction."""
    when_early = datetime(2026, 5, 7, 10, 0, 0, tzinfo=timezone.utc)
    when_late = datetime(2026, 5, 7, 11, 0, 0, tzinfo=timezone.utc)
    early = _stub_attempt(workspace_root=tmp_path)
    early.attempt_id = attempt_id_for("t", "a", when=when_early)
    late = _stub_attempt(workspace_root=tmp_path)
    late.attempt_id = attempt_id_for("t", "a", when=when_late)
    write_attempt(late, tmp_path)
    write_attempt(early, tmp_path)

    out = list_attempts(tmp_path)
    assert [a.attempt_id for a in out] == sorted([early.attempt_id, late.attempt_id])


def test_list_attempts_skips_corrupt_files(tmp_path):
    """Garbage JSON files get skipped silently — caller gets the records they CAN read."""
    good = _stub_attempt(workspace_root=tmp_path)
    write_attempt(good, tmp_path)

    bad = attempts_dir(tmp_path) / "garbage.json"
    bad.write_text("{ not valid json")

    out = list_attempts(tmp_path)
    assert [a.attempt_id for a in out] == [good.attempt_id]


# -----------------------------------------------------------------------------
# Lifecycle helpers: mark_running / mark_finished
# -----------------------------------------------------------------------------


def test_mark_running_persists_pid_and_status(tmp_path):
    attempt = _stub_attempt(workspace_root=tmp_path)
    write_attempt(attempt, tmp_path)
    mark_running(attempt, pid=12345, workspace_root=tmp_path)

    reloaded = load_attempt(attempts_dir(tmp_path) / f"{attempt.attempt_id}.json")
    assert reloaded.status == AttemptStatus.RUNNING
    assert reloaded.pid == 12345


def test_mark_finished_zero_exit_succeeds(tmp_path):
    attempt = _stub_attempt(workspace_root=tmp_path, status=AttemptStatus.RUNNING)
    write_attempt(attempt, tmp_path)
    mark_finished(attempt, exit_code=0, workspace_root=tmp_path)

    reloaded = load_attempt(attempts_dir(tmp_path) / f"{attempt.attempt_id}.json")
    assert reloaded.status == AttemptStatus.SUCCEEDED
    assert reloaded.exit_code == 0
    assert reloaded.failure_class is None
    assert reloaded.finished_at is not None


def test_mark_finished_nonzero_exit_fails_with_runtime_error(tmp_path):
    attempt = _stub_attempt(workspace_root=tmp_path, status=AttemptStatus.RUNNING)
    write_attempt(attempt, tmp_path)
    mark_finished(attempt, exit_code=1, workspace_root=tmp_path)

    reloaded = load_attempt(attempts_dir(tmp_path) / f"{attempt.attempt_id}.json")
    assert reloaded.status == AttemptStatus.FAILED
    assert reloaded.exit_code == 1
    assert reloaded.failure_class == FailureClass.RUNTIME_ERROR
    assert "non-zero" in (reloaded.failure_detail or "")


def test_mark_finished_records_summary_excerpt(tmp_path):
    attempt = _stub_attempt(workspace_root=tmp_path, status=AttemptStatus.RUNNING)
    write_attempt(attempt, tmp_path)
    mark_finished(
        attempt,
        exit_code=0,
        workspace_root=tmp_path,
        summary_excerpt=["Status: done", "Summary: did the thing"],
    )
    reloaded = load_attempt(attempts_dir(tmp_path) / f"{attempt.attempt_id}.json")
    assert "Status: done" in reloaded.summary_excerpt


# -----------------------------------------------------------------------------
# HookOutcome roundtrip
# -----------------------------------------------------------------------------


def test_hook_outcome_serializes_through_attempt(tmp_path):
    attempt = _stub_attempt(workspace_root=tmp_path)
    attempt.hooks["before_run"] = HookOutcome(
        name="before_run",
        exit_code=0,
        duration_seconds=0.42,
        log_path="/tmp/hook.log",
        started_at=now_iso(),
    )
    write_attempt(attempt, tmp_path)
    reloaded = load_attempt(attempts_dir(tmp_path) / f"{attempt.attempt_id}.json")
    assert reloaded.hooks["before_run"].exit_code == 0
    assert reloaded.hooks["before_run"].log_path == "/tmp/hook.log"
