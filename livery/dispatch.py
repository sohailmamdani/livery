"""Dispatch prep: compose a prompt + (optional) worktree + ready-to-run runtime command."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from .attempts import (
    SCHEMA_VERSION,
    AttemptStatus,
    DispatchAttempt,
    attempt_id_for,
    now_iso,
    write_attempt,
)
from .paths_safety import assert_path_contained, sanitize_path_component


@dataclass(slots=True)
class DispatchPrep:
    ticket_id: str
    assignee: str
    runtime: str
    model: str | None
    cwd: str
    prompt_path: Path
    output_path: Path
    command: str
    attempt_id: str | None = None
    """ID of the DispatchAttempt JSON record written for this prep, or None
    if no attempt was written (e.g. caller passed `record_attempt=False`)."""
    attempt_path: Path | None = None
    """Path to the attempt JSON file, if one was written."""


PROMPT_PREAMBLE = "You are acting as the \"{assignee}\" agent in Livery. Below is your AGENTS.md (system prompt / job description) followed by the ticket you are working."

DISPATCH_SUMMARY_BLOCK = """\
When the ticket is done, print a final block in this exact format:

=== DISPATCH_SUMMARY ===
Ticket: {ticket_id}
Status: done | blocked
Summary: <2-4 sentences>
Files touched: <list of absolute paths>
Tests run: <command + pass/fail>
Pushback / flags for sohail: <list or "none">
=== END DISPATCH_SUMMARY ===
"""


def compose_prompt(*, assignee: str, agents_md: str, ticket_md: str, ticket_id: str) -> str:
    """Build the dispatch prompt from the agent's AGENTS.md + the ticket markdown."""
    lines = [
        PROMPT_PREAMBLE.format(assignee=assignee),
        "",
        "---BEGIN AGENTS.md---",
        "",
        agents_md.rstrip(),
        "",
        "---END AGENTS.md---",
        "",
        DISPATCH_SUMMARY_BLOCK.format(ticket_id=ticket_id).rstrip(),
        "",
        "---BEGIN TICKET---",
        "",
        ticket_md.rstrip(),
        "",
        "---END TICKET---",
        "",
        "Proceed.",
        "",
    ]
    return "\n".join(lines)


def build_runtime_command(
    *,
    runtime: str,
    model: str | None,
    cwd: str,
    prompt_path: Path,
    output_path: Path,
) -> str:
    """Return the shell command to launch the runtime with the composed prompt.

    Each adapter produces a bash-ready one-liner that reads the prompt from
    the file at `prompt_path` and writes combined stdout+stderr to
    `output_path`. The caller runs it via Bash (typically in the background).

    Supported runtimes:
      - codex / codex_local — OpenAI Codex CLI (verified)
      - claude_code / claude — Claude Code CLI (verified shape; first
        real dispatch may reveal flag tweaks)
      - cursor / cursor_agent — Cursor Agent CLI (best-effort; verify
        against installed `cursor-agent --help` on first real dispatch)
    """
    cwd_q = shlex.quote(cwd)
    prompt_q = shlex.quote(str(prompt_path))
    output_q = shlex.quote(str(output_path))

    if runtime in {"codex", "codex_local"}:
        parts = ["codex", "exec"]
        if model:
            parts += ["--model", model]
        parts += [
            "--cd", cwd,
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "-",
        ]
        quoted = " ".join(shlex.quote(p) for p in parts)
        return f"{quoted} < {prompt_q} > {output_q} 2>&1"

    if runtime in {"claude_code", "claude"}:
        # Claude Code's CLI has no --cd flag, so cd before invoking.
        # -p prints one response and exits; prompt is read from stdin when
        # no prompt positional arg is given.
        parts = ["claude", "-p", "--dangerously-skip-permissions"]
        if model:
            parts += ["--model", model]
        quoted = " ".join(shlex.quote(p) for p in parts)
        return f"cd {cwd_q} && {quoted} < {prompt_q} > {output_q} 2>&1"

    if runtime in {"cursor", "cursor_agent"}:
        # Cursor Agent CLI (`cursor-agent`). Reads prompt from stdin with
        # --print mode; --force skips interactive confirmation.
        parts = ["cursor-agent", "--print", "--force"]
        if model:
            parts += ["--model", model]
        quoted = " ".join(shlex.quote(p) for p in parts)
        return f"cd {cwd_q} && {quoted} < {prompt_q} > {output_q} 2>&1"

    if runtime in {"lm_studio", "mlx", "ollama"}:
        # HTTP POST to an OpenAI-compatible local endpoint. LM Studio defaults
        # to http://localhost:1234/v1; Ollama exposes the same API shape at
        # http://localhost:11434/v1. Same runtime code, different base URL.
        if not model:
            raise ValueError(
                f"{runtime} runtime requires an explicit model in agent.md"
            )
        livery_root = str(Path(__file__).resolve().parent.parent)
        parts = [
            "uv", "run", "--directory", livery_root,
            "python", "-m", "livery.runtimes.lm_studio",
            "--model", model,
            "--verbose",
        ]
        if runtime == "ollama":
            parts += ["--url", "http://localhost:11434/v1"]
        quoted = " ".join(shlex.quote(p) for p in parts)
        return f"{quoted} < {prompt_q} > {output_q} 2>&1"

    raise NotImplementedError(
        f"Runtime '{runtime}' not supported yet. Implemented: codex, claude_code, cursor, lm_studio. "
        "Add an adapter in livery/dispatch.py::build_runtime_command when you hire "
        "an agent on a different runtime."
    )


def ensure_worktree(*, repo: Path, ticket_id: str, agent_id: str | None = None) -> tuple[Path, str]:
    """Create a sibling git worktree of `repo` checked out on a ticket-specific branch.

    Returns (worktree_path, branch_name). Idempotent if the worktree already exists.

    When `agent_id` is provided, the worktree path and branch include it, so
    two agents dispatched on the same ticket into the same repo get separate
    worktrees instead of colliding.

    The ticket id and agent id are user-controlled, so both are run through
    `paths_safety.sanitize_path_component` before being used to build the
    worktree directory name. The resulting path is then verified to live
    strictly under `repo.parent` (where sibling worktrees belong) before
    `git worktree add` ever runs.
    """
    raw_suffix = ticket_id.split("-")[-1] or ticket_id[-6:]
    short_suffix = sanitize_path_component(raw_suffix, fallback="t")

    if agent_id:
        safe_agent = sanitize_path_component(agent_id, fallback="agent")
        branch = f"ticket-{ticket_id}-{agent_id}"
        worktree_name = f"{repo.name}-{safe_agent}-t{short_suffix}"
    else:
        branch = f"ticket-{ticket_id}"
        worktree_name = f"{repo.name}-t{short_suffix}"

    worktree_path = repo.parent / worktree_name

    # Defence in depth: even though `worktree_name` is built from sanitized
    # components, verify the final path lives strictly under `repo.parent`.
    # Catches symlink shenanigans and any future edits that drop the
    # sanitizer call. Raises PathContainmentError on escape.
    assert_path_contained(worktree_path, repo.parent)

    if worktree_path.exists():
        return worktree_path, branch

    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(worktree_path), "main"],
        check=True,
    )
    return worktree_path, branch


def prepare_dispatch(
    *,
    root: Path,
    ticket_path: Path,
    output_dir: Path,
    make_worktree: bool,
    assignee_override: str | None = None,
) -> DispatchPrep:
    """End-to-end: load ticket + agent, compose prompt, optionally create worktree, build command.

    `assignee_override` lets the caller dispatch a ticket to an agent other
    than the one in the ticket's frontmatter — used by fan-out to run the
    same ticket against multiple agents.
    """
    ticket_post = frontmatter.load(ticket_path)
    ticket_id = str(ticket_post.get("id") or ticket_path.stem)
    assignee = assignee_override or ticket_post.get("assignee")
    if not assignee or assignee == "cos":
        raise ValueError(
            f"Ticket {ticket_id} has no agent assignee (assignee={assignee!r}). "
            "Dispatch is only for tickets assigned to a registered agent."
        )

    agent_dir = root / "agents" / assignee
    agent_md_path = agent_dir / "agent.md"
    agents_md_path = agent_dir / "AGENTS.md"
    if not agent_md_path.exists():
        raise ValueError(f"Agent '{assignee}' not found: missing {agent_md_path}")
    if not agents_md_path.exists():
        raise ValueError(f"Agent '{assignee}' missing system prompt: {agents_md_path}")

    agent_post = frontmatter.load(agent_md_path)
    runtime = str(agent_post.get("runtime") or "codex")
    model = agent_post.get("model")
    cwd = agent_post.get("cwd")
    if not cwd:
        raise ValueError(f"Agent '{assignee}' has no cwd in agent.md")

    actual_cwd = str(cwd)
    worktree_path: Path | None = None
    if make_worktree:
        # Always include agent_id in the worktree so fan-out into the same
        # repo produces separate checkouts. Single-agent dispatches get a
        # slightly more specific worktree name than before, but the old
        # shape (without agent_id) is no longer reachable.
        worktree_path, _branch = ensure_worktree(
            repo=Path(cwd), ticket_id=ticket_id, agent_id=str(assignee)
        )
        actual_cwd = str(worktree_path)

    agents_md = agents_md_path.read_text()
    ticket_md = ticket_path.read_text()
    prompt = compose_prompt(
        assignee=str(assignee),
        agents_md=agents_md,
        ticket_md=ticket_md,
        ticket_id=ticket_id,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    # Include assignee in filenames so fan-out dispatches don't overwrite
    # each other's prompt / output.
    prompt_path = output_dir / f"livery-dispatch-{ticket_id}-{assignee}.txt"
    output_path = output_dir / f"livery-dispatch-{ticket_id}-{assignee}.out"
    prompt_path.write_text(prompt)

    command = build_runtime_command(
        runtime=runtime,
        model=str(model) if model else None,
        cwd=actual_cwd,
        prompt_path=prompt_path,
        output_path=output_path,
    )

    # Write the attempt record. This is the durable metadata the rest of
    # the framework uses to find this dispatch later (`dispatch status`,
    # `dispatch continue`, mid-flight cancellation). Attempt is created
    # in PREPARED state; subprocess lifecycle (RUNNING / SUCCEEDED / FAILED)
    # is updated by the caller in `--run` mode.
    attempt = DispatchAttempt(
        schema_version=SCHEMA_VERSION,
        attempt_id=attempt_id_for(ticket_id, str(assignee)),
        ticket_id=ticket_id,
        assignee=str(assignee),
        runtime=runtime,
        model=str(model) if model else None,
        workspace_root=str(root),
        agent_cwd=str(cwd),
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
    )
    attempt_path = write_attempt(attempt, root)

    # Run the after_worktree_create hook (if configured AND we made one).
    # Failure is blocking: the attempt is marked FAILED with hook_error,
    # we raise so the caller doesn't proceed to launch the runtime.
    if worktree_path is not None:
        from .config import load as _load_cfg
        from .dispatch_hooks import get_hook_command, run_pre_run_hook
        cfg = _load_cfg(root)
        hook_cmd = get_hook_command(cfg.raw, "after_worktree_create")
        if hook_cmd:
            _, ok = run_pre_run_hook(
                hook_name="after_worktree_create",
                command=hook_cmd,
                attempt=attempt,
                workspace_root=root,
            )
            if not ok:
                raise RuntimeError(
                    f"after_worktree_create hook failed for "
                    f"{attempt.attempt_id}; see "
                    f"{attempt.hooks['after_worktree_create'].log_path}"
                )

    return DispatchPrep(
        ticket_id=ticket_id,
        assignee=str(assignee),
        runtime=runtime,
        model=str(model) if model else None,
        cwd=actual_cwd,
        prompt_path=prompt_path,
        output_path=output_path,
        command=command,
        attempt_id=attempt.attempt_id,
        attempt_path=attempt_path,
    )


def prepare_fan_out(
    *,
    root: Path,
    ticket_path: Path,
    output_dir: Path,
    make_worktree: bool,
    assignees: list[str],
) -> list[DispatchPrep]:
    """Prepare dispatches of the same ticket to multiple agents in parallel.

    Each agent gets its own prompt file, output file, and (if requested)
    git worktree. Stops at the first invalid agent rather than returning a
    partial list, since fan-out is all-or-nothing.
    """
    if not assignees:
        raise ValueError("fan-out requires at least one agent in --to")
    if len(set(assignees)) != len(assignees):
        raise ValueError(f"duplicate agents in fan-out list: {assignees}")

    preps: list[DispatchPrep] = []
    for assignee in assignees:
        preps.append(
            prepare_dispatch(
                root=root,
                ticket_path=ticket_path,
                output_dir=output_dir,
                make_worktree=make_worktree,
                assignee_override=assignee,
            )
        )
    return preps
