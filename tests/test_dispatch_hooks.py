from __future__ import annotations

from pathlib import Path

import pytest

from livery.attempts import (
    SCHEMA_VERSION,
    AttemptStatus,
    DispatchAttempt,
    FailureClass,
    attempt_id_for,
    attempts_dir,
    load_attempt,
    now_iso,
    write_attempt,
)
from livery.dispatch_hooks import (
    KNOWN_HOOKS,
    POST_RUN_HOOKS,
    PRE_RUN_HOOKS,
    get_hook_command,
    hooks_dir,
    run_hook,
    run_post_run_hook,
    run_pre_run_hook,
)


def _stub_attempt(workspace_root: Path) -> DispatchAttempt:
    return DispatchAttempt(
        schema_version=SCHEMA_VERSION,
        attempt_id=attempt_id_for("2026-05-07-001-x", "lead-dev"),
        ticket_id="2026-05-07-001-x",
        assignee="lead-dev",
        runtime="codex",
        model="gpt-5-codex",
        workspace_root=str(workspace_root),
        agent_cwd="/tmp/repo",
        worktree_path="/tmp/repo-lead-dev-tx",
        prompt_path="/tmp/livery-dispatch-x.txt",
        output_path="/tmp/livery-dispatch-x.out",
        command="codex exec ...",
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
    )


# -----------------------------------------------------------------------------
# Config plumbing
# -----------------------------------------------------------------------------


def test_get_hook_command_returns_configured_string():
    raw = {"dispatch_hooks": {"before_run": "echo hi"}}
    assert get_hook_command(raw, "before_run") == "echo hi"


def test_get_hook_command_strips_whitespace():
    raw = {"dispatch_hooks": {"before_run": "  echo hi  "}}
    assert get_hook_command(raw, "before_run") == "echo hi"


def test_get_hook_command_treats_empty_as_unset():
    raw = {"dispatch_hooks": {"before_run": "   "}}
    assert get_hook_command(raw, "before_run") is None


def test_get_hook_command_missing_table_returns_none():
    assert get_hook_command({}, "before_run") is None


def test_get_hook_command_unknown_hook_raises():
    with pytest.raises(ValueError) as ei:
        get_hook_command({}, "before_lunch")
    assert "Unknown hook" in str(ei.value)


def test_pre_post_hook_partition_is_disjoint_and_complete():
    """Every known hook is in exactly one of pre or post — protects future
    additions from forgetting which lifecycle phase a new hook belongs to."""
    assert set(PRE_RUN_HOOKS).isdisjoint(POST_RUN_HOOKS)
    assert set(PRE_RUN_HOOKS) | set(POST_RUN_HOOKS) == set(KNOWN_HOOKS)


# -----------------------------------------------------------------------------
# run_hook — pure mechanism
# -----------------------------------------------------------------------------


def test_run_hook_zero_exit_records_zero(tmp_path):
    attempt = _stub_attempt(tmp_path)
    write_attempt(attempt, tmp_path)
    outcome = run_hook(
        hook_name="before_run",
        command="exit 0",
        attempt=attempt,
        workspace_root=tmp_path,
    )
    assert outcome.exit_code == 0
    assert outcome.name == "before_run"
    assert Path(outcome.log_path).is_file()


def test_run_hook_nonzero_exit_records_nonzero(tmp_path):
    attempt = _stub_attempt(tmp_path)
    outcome = run_hook(
        hook_name="before_run",
        command="exit 7",
        attempt=attempt,
        workspace_root=tmp_path,
    )
    assert outcome.exit_code == 7


def test_run_hook_log_path_lives_under_workspace(tmp_path):
    """Sidecar log is always at <workspace>/.livery/dispatch/hooks/<id>-<name>.log"""
    attempt = _stub_attempt(tmp_path)
    outcome = run_hook(
        hook_name="before_run",
        command="echo hello",
        attempt=attempt,
        workspace_root=tmp_path,
    )
    expected = hooks_dir(tmp_path) / f"{attempt.attempt_id}-before_run.log"
    assert Path(outcome.log_path) == expected


def test_run_hook_captures_stdout_and_stderr(tmp_path):
    attempt = _stub_attempt(tmp_path)
    outcome = run_hook(
        hook_name="before_run",
        command="echo to-stdout; echo to-stderr 1>&2",
        attempt=attempt,
        workspace_root=tmp_path,
    )
    log = Path(outcome.log_path).read_text()
    assert "to-stdout" in log
    assert "to-stderr" in log


def test_run_hook_passes_livery_env_vars(tmp_path):
    attempt = _stub_attempt(tmp_path)
    write_attempt(attempt, tmp_path)
    outcome = run_hook(
        hook_name="before_run",
        command='echo TICKET=$LIVERY_TICKET_ID ASSIGNEE=$LIVERY_ASSIGNEE ATTEMPT=$LIVERY_ATTEMPT_ID',
        attempt=attempt,
        workspace_root=tmp_path,
    )
    log = Path(outcome.log_path).read_text()
    assert f"TICKET={attempt.ticket_id}" in log
    assert f"ASSIGNEE={attempt.assignee}" in log
    assert f"ATTEMPT={attempt.attempt_id}" in log


def test_run_hook_after_run_includes_exit_code(tmp_path):
    """LIVERY_EXIT_CODE is set only when caller passes exit_code (after_run)."""
    attempt = _stub_attempt(tmp_path)
    outcome = run_hook(
        hook_name="after_run",
        command='echo EXIT=$LIVERY_EXIT_CODE',
        attempt=attempt,
        workspace_root=tmp_path,
        exit_code=42,
    )
    log = Path(outcome.log_path).read_text()
    assert "EXIT=42" in log


def test_run_hook_timeout_recorded_as_124(tmp_path):
    attempt = _stub_attempt(tmp_path)
    outcome = run_hook(
        hook_name="before_run",
        command="sleep 3",
        attempt=attempt,
        workspace_root=tmp_path,
        timeout_seconds=1,
    )
    assert outcome.exit_code == 124
    assert "TIMEOUT" in Path(outcome.log_path).read_text()


def test_run_hook_unknown_name_raises(tmp_path):
    attempt = _stub_attempt(tmp_path)
    with pytest.raises(ValueError):
        run_hook(
            hook_name="nope",
            command="echo",
            attempt=attempt,
            workspace_root=tmp_path,
        )


# -----------------------------------------------------------------------------
# run_pre_run_hook — blocking-on-failure semantics
# -----------------------------------------------------------------------------


def test_pre_run_hook_success_returns_ok_and_no_status_change(tmp_path):
    attempt = _stub_attempt(tmp_path)
    write_attempt(attempt, tmp_path)
    outcome, ok = run_pre_run_hook(
        hook_name="before_run",
        command="exit 0",
        attempt=attempt,
        workspace_root=tmp_path,
    )
    assert ok is True
    assert outcome.exit_code == 0

    reloaded = load_attempt(attempts_dir(tmp_path) / f"{attempt.attempt_id}.json")
    assert reloaded.status == AttemptStatus.PREPARED  # unchanged
    assert reloaded.failure_class is None
    assert "before_run" in reloaded.hooks


def test_pre_run_hook_failure_marks_attempt_failed_with_hook_error(tmp_path):
    attempt = _stub_attempt(tmp_path)
    write_attempt(attempt, tmp_path)
    outcome, ok = run_pre_run_hook(
        hook_name="before_run",
        command="exit 5",
        attempt=attempt,
        workspace_root=tmp_path,
    )
    assert ok is False
    assert outcome.exit_code == 5

    reloaded = load_attempt(attempts_dir(tmp_path) / f"{attempt.attempt_id}.json")
    assert reloaded.status == AttemptStatus.FAILED
    assert reloaded.failure_class == FailureClass.HOOK_ERROR
    assert "before_run" in (reloaded.failure_detail or "")
    assert reloaded.finished_at is not None


def test_pre_run_hook_rejects_post_run_name(tmp_path):
    attempt = _stub_attempt(tmp_path)
    with pytest.raises(ValueError):
        run_pre_run_hook(
            hook_name="after_run",
            command="exit 0",
            attempt=attempt,
            workspace_root=tmp_path,
        )


# -----------------------------------------------------------------------------
# run_post_run_hook — advisory semantics
# -----------------------------------------------------------------------------


def test_post_run_hook_success_records_outcome_no_warning(tmp_path):
    attempt = _stub_attempt(tmp_path)
    attempt.status = AttemptStatus.SUCCEEDED  # runtime already finished
    write_attempt(attempt, tmp_path)

    outcome = run_post_run_hook(
        command="exit 0",
        attempt=attempt,
        workspace_root=tmp_path,
        exit_code=0,
    )
    assert outcome.exit_code == 0

    reloaded = load_attempt(attempts_dir(tmp_path) / f"{attempt.attempt_id}.json")
    assert "after_run" in reloaded.hooks
    assert reloaded.hook_warnings == []
    # Status untouched
    assert reloaded.status == AttemptStatus.SUCCEEDED


def test_post_run_hook_failure_records_warning_does_not_change_status(tmp_path):
    attempt = _stub_attempt(tmp_path)
    attempt.status = AttemptStatus.SUCCEEDED  # runtime already finished
    write_attempt(attempt, tmp_path)

    outcome = run_post_run_hook(
        command="exit 9",
        attempt=attempt,
        workspace_root=tmp_path,
        exit_code=0,
    )
    assert outcome.exit_code == 9

    reloaded = load_attempt(attempts_dir(tmp_path) / f"{attempt.attempt_id}.json")
    # Critical contract: status unchanged even though hook failed
    assert reloaded.status == AttemptStatus.SUCCEEDED
    assert reloaded.failure_class is None
    # Warning is recorded
    assert len(reloaded.hook_warnings) == 1
    assert "after_run" in reloaded.hook_warnings[0]
    assert "exited 9" in reloaded.hook_warnings[0]


def test_post_run_hook_does_not_overwrite_runtime_failure(tmp_path):
    """If the runtime itself failed, post-run hook running and succeeding
    must NOT clear the FAILED status."""
    attempt = _stub_attempt(tmp_path)
    attempt.status = AttemptStatus.FAILED
    attempt.failure_class = FailureClass.RUNTIME_ERROR
    write_attempt(attempt, tmp_path)

    run_post_run_hook(
        command="exit 0",
        attempt=attempt,
        workspace_root=tmp_path,
        exit_code=1,
    )
    reloaded = load_attempt(attempts_dir(tmp_path) / f"{attempt.attempt_id}.json")
    assert reloaded.status == AttemptStatus.FAILED
    assert reloaded.failure_class == FailureClass.RUNTIME_ERROR
