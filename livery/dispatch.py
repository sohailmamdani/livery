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


WALKIE_TASK_TEMPLATE = """\
You are participating in a Livery Walkie-Talkie debate as the peer "{peer}".

The shared walkie-talkie file is at:
{walkie_path}

Your job for this turn:
1. Read the entire walkie-talkie file from the top — frontmatter,
   briefing (if present), every previous turn in order, AND the protocol
   section at the bottom. The protocol governs how you participate.
2. Take Turn {turn_n}. Your peer is "{other_peer}".
3. Append your turn to the file. New content goes at the bottom, *above*
   the `<!-- LIVERY-WALKIE-TALKIE PROTOCOL -->` marker. Do not touch any
   existing content; this file is append-only.
4. Use this exact turn header (the controller parses it):
   `## Turn {turn_n} — {peer} — <ISO8601-UTC timestamp>`
5. Push back hard if you disagree with prior turns. Don't capitulate to
   manufacture consensus. Walkie-talkie exists to converge on the
   correct answer.
6. If you believe the plan in the file is correct AND your peer's
   reasoning supports it, end your turn with a line:
   `SIGNED: {peer} @ <ISO8601-UTC timestamp>`

After you have appended exactly one turn, STOP. Do not take a second
turn — wait for your peer. Do not modify other files. Exit.
"""


WALKIE_BRIEFING_BLOCK = """\
---BEGIN BRIEFING---

The debate's canonical question and context. This is the same for every
turn — both peers see it identically.

{briefing}

---END BRIEFING---
"""


def compose_walkie_prompt(
    *,
    peer: str,
    other_peer: str,
    agents_md: str,
    walkie_path: Path,
    turn_n: int,
    briefing: str | None = None,
    ticket_md: str | None = None,
) -> str:
    """Three-layer prompt for one walkie turn:

      1. Agent identity — the peer's `AGENTS.md` (who they are).
      2. Briefing — either the inline briefing string, or the ticket
         markdown if a ticket holds the question (or both, in that
         order). Constant across all turns of this walkie.
      3. Task — the per-turn instruction telling the peer to read the
         walkie file, take Turn N, follow the protocol, and exit.

    Order matters: identity first (sets the role), then briefing
    (the question), then task (what to do right now). The walkie file
    itself is referenced by absolute path; the peer reads it during the
    turn, so we don't embed its content here (it changes mid-debate).
    """
    parts = [
        f"You are acting as the \"{peer}\" agent in a Livery Walkie-Talkie.",
        "",
        "---BEGIN AGENTS.md---",
        "",
        agents_md.rstrip(),
        "",
        "---END AGENTS.md---",
        "",
    ]
    if briefing:
        parts.append(WALKIE_BRIEFING_BLOCK.format(briefing=briefing.rstrip()).rstrip())
        parts.append("")
    if ticket_md:
        parts.extend([
            "---BEGIN TICKET (debate context)---",
            "",
            ticket_md.rstrip(),
            "",
            "---END TICKET---",
            "",
        ])
    parts.append(WALKIE_TASK_TEMPLATE.format(
        peer=peer,
        other_peer=other_peer,
        walkie_path=str(walkie_path),
        turn_n=turn_n,
    ).rstrip())
    parts.append("")
    return "\n".join(parts)


def prepare_walkie_turn(
    *,
    root: Path,
    walkie_path: Path,
    peer: str,
    other_peer: str,
    turn_n: int,
    briefing: str | None = None,
    ticket_md: str | None = None,
) -> DispatchPrep:
    """Prepare a single walkie turn as a Livery DispatchAttempt.

    Mirrors `prepare_dispatch` but bypasses the ticket-as-task model:
    the task is the walkie protocol continuation (built from
    `compose_walkie_prompt`), the briefing and/or ticket markdown serve
    as the debate context, and the peer is treated as the assignee.

    Side effects:
      - Writes the composed prompt to
        `<workspace>/.livery/walkie-talkie/prompts/<attempt-id>.txt`.
      - Writes a `DispatchAttempt` JSON (status=PREPARED) to the usual
        attempts dir; the caller manages the lifecycle from there.
      - Output is captured to /tmp like a normal dispatch so existing
        `dispatch status` / `dispatch tail` machinery works.
    """
    agent_dir = root / "agents" / peer
    agent_md_path = agent_dir / "agent.md"
    agents_md_path = agent_dir / "AGENTS.md"
    if not agent_md_path.exists():
        raise ValueError(
            f"Walkie peer '{peer}' is not a hired agent: missing {agent_md_path}. "
            f"Run `livery hire {peer}` first."
        )
    if not agents_md_path.exists():
        raise ValueError(f"Peer '{peer}' missing system prompt: {agents_md_path}")

    agent_post = frontmatter.load(agent_md_path)
    runtime = str(agent_post.get("runtime") or "codex")
    model = agent_post.get("model")
    cwd = agent_post.get("cwd") or str(root)

    agents_md = agents_md_path.read_text()
    prompt = compose_walkie_prompt(
        peer=peer,
        other_peer=other_peer,
        agents_md=agents_md,
        walkie_path=walkie_path,
        turn_n=turn_n,
        briefing=briefing,
        ticket_md=ticket_md,
    )

    # Prompts live under the workspace, alongside attempts, so they're
    # part of the durable record (not /tmp). Walkie label uses the file
    # stem so multiple turns of the same walkie share a prefix.
    walkie_label = walkie_path.stem
    pseudo_ticket_id = f"walkie-{walkie_label}-t{turn_n:03d}"
    attempt_id = attempt_id_for(pseudo_ticket_id, str(peer))

    prompts_dir = root / ".livery" / "walkie-talkie" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompts_dir / f"{attempt_id}.txt"
    prompt_path.write_text(prompt)

    # Outputs still go to /tmp so `dispatch tail` works without
    # workspace knowledge. Naming follows the existing convention.
    output_dir = Path("/tmp")
    output_path = output_dir / f"livery-dispatch-{pseudo_ticket_id}-{peer}.out"

    command = build_runtime_command(
        runtime=runtime,
        model=str(model) if model else None,
        cwd=str(cwd),
        prompt_path=prompt_path,
        output_path=output_path,
    )

    attempt = DispatchAttempt(
        schema_version=SCHEMA_VERSION,
        attempt_id=attempt_id,
        ticket_id=pseudo_ticket_id,
        assignee=str(peer),
        runtime=runtime,
        model=str(model) if model else None,
        workspace_root=str(root),
        agent_cwd=str(cwd),
        worktree_path=None,
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

    return DispatchPrep(
        ticket_id=pseudo_ticket_id,
        assignee=str(peer),
        runtime=runtime,
        model=str(model) if model else None,
        cwd=str(cwd),
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
