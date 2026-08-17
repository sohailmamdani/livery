"""Managed execution for a prepared dispatch.

Preparation and execution are separate in Livery.  This module is the shared
single-dispatch execution path used by scheduled work and other callers that
need Livery to own the full lifecycle rather than merely print a shell command.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .attempts import (
    AttemptStatus,
    FailureClass,
    load_attempt,
    mark_finished,
    mark_running,
    now_iso,
    write_attempt,
)
from .config import load as load_config
from .dispatch import DispatchPrep
from .dispatch_hooks import get_hook_command, run_post_run_hook, run_pre_run_hook
from .dispatch_view import _read_tail


@dataclass(slots=True)
class DispatchExecution:
    attempt_id: str
    attempt_path: Path
    output_path: Path
    exit_code: int
    pid: int | None
    launched: bool
    hook_warnings: list[str]
    status: AttemptStatus


def finalize_prepared_dispatch(
    prep: DispatchPrep,
    *,
    workspace_root: Path,
    exit_code: int,
) -> AttemptStatus:
    """Persist final runtime state, summary, and ticket-facing result."""
    if prep.attempt_path is None:
        raise ValueError("managed execution requires a durable attempt record")

    _, summary = _read_tail(prep.output_path)
    attempt = load_attempt(prep.attempt_path)
    mark_finished(
        attempt,
        exit_code=exit_code,
        workspace_root=workspace_root,
        summary_excerpt=summary,
    )
    attempt = load_attempt(prep.attempt_path)
    if exit_code == 0 and any(
        line.strip().lower() == "status: blocked" for line in summary
    ):
        attempt.status = AttemptStatus.BLOCKED
        attempt.failure_detail = "agent reported work blocked"
        write_attempt(attempt, workspace_root)
    elif exit_code == 0 and not summary:
        attempt.status = AttemptStatus.FAILED
        attempt.failure_class = FailureClass.RUNTIME_ERROR
        attempt.failure_detail = "runtime exited without DISPATCH_SUMMARY"
        write_attempt(attempt, workspace_root)
    return load_attempt(prep.attempt_path).status


def _mark_hook_mechanism_failed(
    prep: DispatchPrep,
    *,
    workspace_root: Path,
    detail: str,
) -> DispatchExecution:
    if prep.attempt_path is None:
        raise RuntimeError(detail)
    attempt = load_attempt(prep.attempt_path)
    attempt.status = AttemptStatus.FAILED
    attempt.failure_class = FailureClass.HOOK_ERROR
    attempt.failure_detail = detail
    attempt.finished_at = now_iso()
    attempt.exit_code = 1
    write_attempt(attempt, workspace_root)
    return DispatchExecution(
        attempt_id=attempt.attempt_id,
        attempt_path=prep.attempt_path,
        output_path=prep.output_path,
        exit_code=1,
        pid=None,
        launched=False,
        hook_warnings=list(attempt.hook_warnings),
        status=attempt.status,
    )


def execute_prepared_dispatch(
    prep: DispatchPrep,
    *,
    workspace_root: Path,
) -> DispatchExecution:
    """Run one prepared dispatch and persist every lifecycle transition."""
    if prep.attempt_path is None:
        raise ValueError("managed execution requires a durable attempt record")

    cfg = load_config(workspace_root)
    before_run_cmd = get_hook_command(cfg.raw, "before_run")
    after_run_cmd = get_hook_command(cfg.raw, "after_run")

    if before_run_cmd:
        try:
            attempt = load_attempt(prep.attempt_path)
            _, ok = run_pre_run_hook(
                hook_name="before_run",
                command=before_run_cmd,
                attempt=attempt,
                workspace_root=workspace_root,
            )
            if not ok:
                return DispatchExecution(
                    attempt_id=attempt.attempt_id,
                    attempt_path=prep.attempt_path,
                    output_path=prep.output_path,
                    exit_code=1,
                    pid=None,
                    launched=False,
                    hook_warnings=list(attempt.hook_warnings),
                    status=attempt.status,
                )
        except Exception as exc:
            return _mark_hook_mechanism_failed(
                prep,
                workspace_root=workspace_root,
                detail=f"before_run hook mechanism failed: {exc}",
            )

    process = subprocess.Popen(  # noqa: S602 - command is built by runtime adapters
        prep.command,
        shell=True,
    )
    attempt = load_attempt(prep.attempt_path)
    mark_running(attempt, pid=process.pid, workspace_root=workspace_root)

    try:
        exit_code = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        attempt = load_attempt(prep.attempt_path)
        attempt.status = AttemptStatus.CANCELLED
        attempt.failure_class = FailureClass.RUNTIME_ERROR
        attempt.failure_detail = "operator aborted with Ctrl+C"
        attempt.finished_at = now_iso()
        attempt.exit_code = 130
        write_attempt(attempt, workspace_root)
        raise

    finalize_prepared_dispatch(
        prep,
        workspace_root=workspace_root,
        exit_code=exit_code,
    )

    if after_run_cmd:
        try:
            attempt = load_attempt(prep.attempt_path)
            run_post_run_hook(
                command=after_run_cmd,
                attempt=attempt,
                workspace_root=workspace_root,
                exit_code=exit_code,
            )
        except Exception as exc:
            attempt = load_attempt(prep.attempt_path)
            warning = f"after_run hook mechanism failed: {exc}"
            attempt.hook_warnings.append(warning)
            write_attempt(attempt, workspace_root)

    final = load_attempt(prep.attempt_path)
    effective_exit_code = exit_code
    if final.status == AttemptStatus.FAILED and exit_code == 0:
        effective_exit_code = 1
    return DispatchExecution(
        attempt_id=final.attempt_id,
        attempt_path=prep.attempt_path,
        output_path=prep.output_path,
        exit_code=effective_exit_code,
        pid=process.pid,
        launched=True,
        hook_warnings=list(final.hook_warnings),
        status=final.status,
    )
