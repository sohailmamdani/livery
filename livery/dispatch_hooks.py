"""Dispatch lifecycle hooks: shell out to user-defined commands at well-
known points in the dispatch attempt lifecycle. Configured per-workspace
in the `[dispatch_hooks]` table of `livery.toml`.

Hook names (one command each, plain shell string):

  - `after_worktree_create` — fires inside `prepare_dispatch` immediately
    after `git worktree add` succeeds. Skipped when no worktree is made.
  - `before_run` — fires in `livery dispatch ... --run` immediately before
    the runtime subprocess launches.
  - `after_run` — fires after the runtime subprocess exits (regardless
    of success).

Failure semantics (signed plan):

  - **Pre-run** hooks (`after_worktree_create`, `before_run`) are
    blocking. A non-zero exit marks the attempt FAILED with
    `failure_class=hook_error`; the next lifecycle step (worktree use,
    runtime launch) does not happen.
  - **Post-run** hooks (`after_run`) are advisory. A non-zero exit is
    recorded in `attempt.hook_warnings`; the attempt's primary status
    (set by the runtime's exit code) is not downgraded.

Each hook invocation captures stdout+stderr to a sidecar log:
`<workspace>/.livery/dispatch/hooks/<attempt_id>-<hook_name>.log`.

Env vars passed to every hook:

  LIVERY_TICKET_ID, LIVERY_ASSIGNEE, LIVERY_RUNTIME, LIVERY_MODEL,
  LIVERY_CWD, LIVERY_ATTEMPT_ID, LIVERY_ATTEMPT_PATH, LIVERY_PROMPT_PATH,
  LIVERY_OUTPUT_PATH

`after_run` additionally gets `LIVERY_EXIT_CODE`.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .attempts import (
    AttemptStatus,
    DispatchAttempt,
    FailureClass,
    HookOutcome,
    attempts_dir,
    now_iso,
    write_attempt,
)

HOOKS_DIR_NAME = "hooks"
KNOWN_HOOKS: tuple[str, ...] = (
    "after_worktree_create",
    "before_run",
    "after_run",
)
PRE_RUN_HOOKS: tuple[str, ...] = ("after_worktree_create", "before_run")
POST_RUN_HOOKS: tuple[str, ...] = ("after_run",)

DEFAULT_HOOK_TIMEOUT_SECONDS = 60


def hooks_dir(workspace_root: Path) -> Path:
    return workspace_root / ".livery" / "dispatch" / HOOKS_DIR_NAME


def get_hook_command(workspace_config_raw: dict, hook_name: str) -> str | None:
    """Return the shell command configured for `hook_name`, or None.

    Reads the `[dispatch_hooks]` table from livery.toml's parsed dict.
    Unknown hook names raise — typo-catching, since hooks are silent
    when missing and we don't want users to think a misnamed key is
    "configured but never firing".
    """
    if hook_name not in KNOWN_HOOKS:
        raise ValueError(
            f"Unknown hook name {hook_name!r}. Known: {', '.join(KNOWN_HOOKS)}"
        )
    table = workspace_config_raw.get("dispatch_hooks") or {}
    cmd = table.get(hook_name)
    if not cmd or not isinstance(cmd, str):
        return None
    cmd = cmd.strip()
    return cmd or None


def _attempt_path_for(attempt: DispatchAttempt, workspace_root: Path) -> Path:
    return attempts_dir(workspace_root) / f"{attempt.attempt_id}.json"


def _build_env(
    attempt: DispatchAttempt,
    *,
    attempt_path: Path,
    exit_code: int | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env["LIVERY_TICKET_ID"] = attempt.ticket_id
    env["LIVERY_ASSIGNEE"] = attempt.assignee
    env["LIVERY_RUNTIME"] = attempt.runtime or ""
    env["LIVERY_MODEL"] = attempt.model or ""
    env["LIVERY_CWD"] = attempt.agent_cwd or ""
    env["LIVERY_ATTEMPT_ID"] = attempt.attempt_id
    env["LIVERY_ATTEMPT_PATH"] = str(attempt_path)
    env["LIVERY_PROMPT_PATH"] = attempt.prompt_path or ""
    env["LIVERY_OUTPUT_PATH"] = attempt.output_path or ""
    if exit_code is not None:
        env["LIVERY_EXIT_CODE"] = str(exit_code)
    return env


def run_hook(
    *,
    hook_name: str,
    command: str,
    attempt: DispatchAttempt,
    workspace_root: Path,
    exit_code: int | None = None,
    timeout_seconds: int = DEFAULT_HOOK_TIMEOUT_SECONDS,
) -> HookOutcome:
    """Execute one hook command via /bin/sh. Captures combined stdout+stderr
    to a sidecar log. Returns a HookOutcome — never raises (timeouts and
    OS-level errors become non-zero exit codes recorded in the log).

    Pure mechanism — does not modify the attempt record. The caller
    decides what to do with the outcome (block-on-fail vs warn).
    """
    if hook_name not in KNOWN_HOOKS:
        raise ValueError(f"Unknown hook name: {hook_name!r}")

    log_dir = hooks_dir(workspace_root)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{attempt.attempt_id}-{hook_name}.log"

    attempt_path = _attempt_path_for(attempt, workspace_root)
    env = _build_env(attempt, attempt_path=attempt_path, exit_code=exit_code)

    started_at = now_iso()
    t0 = time.monotonic()
    rc: int
    with log_path.open("w") as logf:
        logf.write(
            f"# livery hook={hook_name} attempt={attempt.attempt_id} "
            f"started_at={started_at}\n"
        )
        logf.write(f"# command: {command}\n")
        logf.flush()
        try:
            proc = subprocess.run(
                command,
                shell=True,  # noqa: S602 — hook commands come from the user's own livery.toml
                env=env,
                stdout=logf,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                check=False,
            )
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            logf.write(f"\n# TIMEOUT after {timeout_seconds}s\n")
            rc = 124  # Posix-ish convention for timeout
        except OSError as e:
            logf.write(f"\n# ERROR launching hook: {type(e).__name__}: {e}\n")
            rc = 127

    duration = round(time.monotonic() - t0, 3)
    return HookOutcome(
        name=hook_name,
        exit_code=rc,
        duration_seconds=duration,
        log_path=str(log_path),
        started_at=started_at,
    )


def run_pre_run_hook(
    *,
    hook_name: str,
    command: str,
    attempt: DispatchAttempt,
    workspace_root: Path,
) -> tuple[HookOutcome, bool]:
    """Run a blocking-on-failure hook. Returns (outcome, ok).

    Side effects:
      - Records `outcome` in `attempt.hooks[hook_name]`.
      - On non-zero exit: marks the attempt FAILED with
        `failure_class=hook_error`, sets `failure_detail` pointing at
        the log path, sets `finished_at`.
      - Re-writes the attempt JSON so concurrent `dispatch status` reflects
        the new state.

    Caller is expected to abort the next lifecycle step when ok is False.
    """
    if hook_name not in PRE_RUN_HOOKS:
        raise ValueError(
            f"{hook_name!r} is not a pre-run hook (one of {PRE_RUN_HOOKS})"
        )

    outcome = run_hook(
        hook_name=hook_name,
        command=command,
        attempt=attempt,
        workspace_root=workspace_root,
    )
    attempt.hooks[hook_name] = outcome
    ok = outcome.exit_code == 0
    if not ok:
        attempt.status = AttemptStatus.FAILED
        attempt.failure_class = FailureClass.HOOK_ERROR
        attempt.failure_detail = (
            f"{hook_name} hook exited {outcome.exit_code}; "
            f"see {outcome.log_path}"
        )
        attempt.finished_at = now_iso()
    write_attempt(attempt, workspace_root)
    return outcome, ok


def run_post_run_hook(
    *,
    command: str,
    attempt: DispatchAttempt,
    workspace_root: Path,
    exit_code: int,
) -> HookOutcome:
    """Run an advisory hook (after_run). Side effects:

      - Records the outcome in `attempt.hooks["after_run"]`.
      - On non-zero exit: appends a one-line warning to
        `attempt.hook_warnings`. The primary status is NOT changed —
        the runtime's exit code already determined that.
      - Re-writes the attempt JSON.
    """
    outcome = run_hook(
        hook_name="after_run",
        command=command,
        attempt=attempt,
        workspace_root=workspace_root,
        exit_code=exit_code,
    )
    attempt.hooks["after_run"] = outcome
    if outcome.exit_code != 0:
        attempt.hook_warnings.append(
            f"after_run hook exited {outcome.exit_code}; "
            f"see {outcome.log_path}"
        )
    write_attempt(attempt, workspace_root)
    return outcome
