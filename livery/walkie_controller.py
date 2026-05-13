"""Auto-mode controller for Walkie-Talkie debates.

A controller turns the manual walkie protocol into an automated loop:
each turn becomes a `DispatchAttempt`, the peer's runtime is spawned as
a subprocess, and the walkie file is the shared state both peers
operate on.

The loop, in plain words:

    while not walkie.is_locked and turns_taken < max_turns:
        peer = decide_next_peer(walkie, declared_peers)
        prep = prepare_walkie_turn(peer, walkie_path, turn_n, briefing, ticket_md)
        run prep.command (subprocess); wait
        mark attempt finished
        re-parse walkie file
        if turn count didn't increment → stall: stop

Failure modes are explicit:

  - Peer ran but didn't append a turn → STALLED (caller's choice
    whether to retry; first version stops).
  - Peer's subprocess exited non-zero → controller stops; the attempt
    JSON records the failure with `failure_class=runtime_error`.
  - User Ctrl+Cs → in-flight attempt marked CANCELLED via existing
    dispatch attempt machinery; walkie file is left intact (resume by
    re-running `livery walkie auto`).

This module is the *mechanism*. Policy (max turns, timeouts, hook
config) is read from the workspace via existing primitives.
"""

from __future__ import annotations

import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

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
from .dispatch import prepare_walkie_turn
from .dispatch_hooks import get_hook_command, run_post_run_hook, run_pre_run_hook
from .walkie import ControllerStep, decide_next_peer, parse_walkie

DEFAULT_TURN_TIMEOUT_SECONDS = 600
DEFAULT_MAX_TURNS = 20


@dataclass(slots=True)
class ControllerResult:
    """Final state from `run_controller`."""
    walkie_path: Path
    steps: list[ControllerStep] = field(default_factory=list)
    locked: bool = False
    stopped_reason: str = ""

    @property
    def ok(self) -> bool:
        return self.locked


def _read_ticket_markdown(workspace_root: Path, ticket_id: str | None) -> str | None:
    """Locate `tickets/<ticket-id>.md` and return its raw content, or None.

    Walkie auto-mode lets the briefing live in a ticket OR inline in
    the walkie file (or both). When a ticket is referenced, every turn
    embeds the full ticket markdown — it's part of the debate context.
    """
    if not ticket_id:
        return None
    candidate = workspace_root / "tickets" / f"{ticket_id}.md"
    if not candidate.is_file():
        return None
    return candidate.read_text()


def controller_step(
    *,
    workspace_root: Path,
    walkie_path: Path,
    declared_peers: list[str],
    briefing: str | None,
    ticket_md: str | None,
    turn_timeout_seconds: int = DEFAULT_TURN_TIMEOUT_SECONDS,
    log: Callable[[str], None] | None = None,
) -> ControllerStep:
    """Run exactly one walkie turn end-to-end.

    Selects the next peer, prepares the attempt, spawns the runtime,
    waits for it to finish, marks the attempt, and re-parses the
    walkie file to confirm a turn was appended.

    Returns a `ControllerStep`. The caller decides whether to continue.
    """
    _log = log or (lambda _msg: None)

    walkie_before = parse_walkie(walkie_path)
    peer = decide_next_peer(walkie_before, declared_peers)
    other_peer = next(p for p in declared_peers if p != peer)
    turn_n = walkie_before.next_turn_n
    _log(f"[walkie] turn {turn_n}: dispatching to peer={peer}")

    prep = prepare_walkie_turn(
        root=workspace_root,
        walkie_path=walkie_path,
        peer=peer,
        other_peer=other_peer,
        turn_n=turn_n,
        briefing=briefing,
        ticket_md=ticket_md,
    )

    # Run dispatch hooks alongside the turn just like a normal dispatch.
    cfg = load_config(workspace_root)
    before_run_cmd = get_hook_command(cfg.raw, "before_run")
    after_run_cmd = get_hook_command(cfg.raw, "after_run")

    if before_run_cmd and prep.attempt_path:
        attempt = load_attempt(prep.attempt_path)
        _, ok = run_pre_run_hook(
            hook_name="before_run",
            command=before_run_cmd,
            attempt=attempt,
            workspace_root=workspace_root,
        )
        if not ok:
            _log(f"[walkie] before_run hook failed; skipping turn")
            return ControllerStep(
                peer=peer, turn_n=turn_n,
                attempt_id=prep.attempt_id, exit_code=None,
                advanced=False, locked_after=False,
            )

    # Spawn the runtime as a shell subprocess (same pattern as the
    # existing `dispatch ... --run` path).
    proc = subprocess.Popen(prep.command, shell=True)  # noqa: S602 — command is from build_runtime_command
    _log(f"[walkie] turn {turn_n}: pid={proc.pid}")

    if prep.attempt_path:
        attempt = load_attempt(prep.attempt_path)
        mark_running(attempt, pid=proc.pid, workspace_root=workspace_root)

    try:
        rc = proc.wait(timeout=turn_timeout_seconds)
    except subprocess.TimeoutExpired:
        _log(f"[walkie] turn {turn_n}: timeout after {turn_timeout_seconds}s — killing")
        proc.kill()
        proc.wait()
        rc = 124  # POSIX-ish timeout exit

    if prep.attempt_path:
        attempt = load_attempt(prep.attempt_path)
        if rc == 124:
            # Timeout — mark as STALE explicitly rather than rely on
            # mark_finished's exit-code mapping.
            attempt.status = AttemptStatus.STALE
            attempt.failure_class = FailureClass.RUNTIME_ERROR
            attempt.failure_detail = f"turn dispatch timed out after {turn_timeout_seconds}s"
            attempt.finished_at = now_iso()
            attempt.exit_code = rc
            write_attempt(attempt, workspace_root)
        else:
            mark_finished(attempt, exit_code=rc, workspace_root=workspace_root)

    if after_run_cmd and prep.attempt_path:
        try:
            attempt = load_attempt(prep.attempt_path)
            run_post_run_hook(
                command=after_run_cmd,
                attempt=attempt,
                workspace_root=workspace_root,
                exit_code=rc,
            )
        except Exception as e:
            _log(f"[walkie] after_run hook errored: {e}")

    # Confirm the file advanced.
    walkie_after = parse_walkie(walkie_path)
    advanced = walkie_after.next_turn_n > walkie_before.next_turn_n
    locked_after = walkie_after.is_locked

    if not advanced:
        _log(
            f"[walkie] turn {turn_n}: peer {peer} did NOT append a turn "
            f"(exit_code={rc}). Treating as stall."
        )
    else:
        _log(f"[walkie] turn {turn_n}: appended; locked={locked_after}")

    return ControllerStep(
        peer=peer, turn_n=turn_n,
        attempt_id=prep.attempt_id, exit_code=rc,
        advanced=advanced, locked_after=locked_after,
    )


def run_controller(
    *,
    workspace_root: Path,
    walkie_path: Path,
    max_turns: int = DEFAULT_MAX_TURNS,
    turn_timeout_seconds: int = DEFAULT_TURN_TIMEOUT_SECONDS,
    log: Callable[[str], None] | None = None,
) -> ControllerResult:
    """Loop until the walkie is locked, max_turns is reached, or a peer
    stalls / fails.

    Reads the declared peers, briefing presence, and ticket reference
    from the walkie file's frontmatter — the controller is stateless,
    the file is canonical. This lets you stop and resume the same
    walkie by re-running.
    """
    _log = log or (lambda _msg: None)
    result = ControllerResult(walkie_path=walkie_path)

    walkie = parse_walkie(walkie_path)
    if not walkie.declared_peers or len(walkie.declared_peers) < 2:
        raise ValueError(
            f"walkie {walkie_path} has no `peers` in frontmatter; "
            f"auto-mode needs at least two declared peers. Recreate the "
            f"walkie with `livery walkie auto`."
        )

    ticket_md = _read_ticket_markdown(workspace_root, walkie.ticket_id)
    # Briefing comes from the walkie file itself — we don't re-read it
    # per turn (the peer reads the entire file anyway), but we pass None
    # to compose_walkie_prompt and let the peer parse `## Briefing`
    # from the file. The ticket markdown IS embedded in the prompt as
    # extra debate context.
    briefing = None

    if walkie.is_locked:
        result.locked = True
        result.stopped_reason = "already locked"
        return result

    for _ in range(max_turns):
        step = controller_step(
            workspace_root=workspace_root,
            walkie_path=walkie_path,
            declared_peers=walkie.declared_peers,
            briefing=briefing,
            ticket_md=ticket_md,
            turn_timeout_seconds=turn_timeout_seconds,
            log=log,
        )
        result.steps.append(step)
        if step.locked_after:
            result.locked = True
            result.stopped_reason = f"both peers signed after turn {step.turn_n}"
            return result
        if not step.advanced:
            result.stopped_reason = (
                f"peer {step.peer} stalled on turn {step.turn_n} "
                f"(dispatch exit={step.exit_code}); walkie unchanged"
            )
            return result
        if step.exit_code not in (0, None):
            result.stopped_reason = (
                f"peer {step.peer} dispatch failed on turn {step.turn_n} "
                f"(exit={step.exit_code})"
            )
            return result

    result.stopped_reason = f"hit max_turns ({max_turns}) without convergence"
    return result
