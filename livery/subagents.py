"""Bounded, auditable delegation from one Livery dispatch to advisory children."""

from __future__ import annotations

import shlex
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import frontmatter

from .attempts import (
    SCHEMA_VERSION,
    AttemptStatus,
    DispatchAttempt,
    attempt_id_for,
    attempts_dir,
    list_attempts,
    load_attempt,
    now_iso,
    write_attempt,
)
from .paths_safety import sanitize_path_component


TICKET_POLICIES = ("inherit", "never", "allowed", "encouraged")
AGENT_POLICIES = ("never", "allowed")


@dataclass(frozen=True, slots=True)
class DelegationPolicy:
    enabled: bool
    ticket_policy: str
    max_subagents: int
    max_depth: int


@contextmanager
def _delegation_lock(root: Path) -> Iterator[None]:
    """Serialize child admission so concurrent launches cannot exceed a ceiling."""
    import fcntl

    lock_path = root / ".livery" / "dispatch" / "subagents.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def resolve_policy(
    agent_post: frontmatter.Post,
    ticket_post: frontmatter.Post,
) -> DelegationPolicy:
    agent_policy = str(agent_post.get("subagents") or "never")
    if agent_policy not in AGENT_POLICIES:
        raise ValueError(
            f"agent.md subagents must be one of: {', '.join(AGENT_POLICIES)}"
        )
    ticket_policy = str(ticket_post.get("subagents") or "inherit")
    if ticket_policy not in TICKET_POLICIES:
        raise ValueError(
            f"ticket subagents must be one of: {', '.join(TICKET_POLICIES)}"
        )
    try:
        maximum = int(agent_post.get("max_subagents", 3))
        depth = int(agent_post.get("max_subagent_depth", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("agent subagent limits must be integers") from exc
    if maximum < 1 or depth < 1:
        raise ValueError("agent subagent limits must be at least 1")
    return DelegationPolicy(
        enabled=agent_policy == "allowed" and ticket_policy != "never",
        ticket_policy=ticket_policy,
        max_subagents=maximum,
        max_depth=depth,
    )


def parent_prompt_block(
    *,
    policy: DelegationPolicy,
    ticket_id: str,
    workspace_root: Path,
    attempt_id: str,
) -> str:
    if not policy.enabled:
        return ""
    posture = (
        "Use advisory subagents where they can materially improve the result."
        if policy.ticket_policy == "encouraged"
        else "You may use advisory subagents when they would materially improve the result."
    )
    return f"""## Advisory subagents

{posture} Children are read-only specialists: they inspect and report; you
remain responsible for all edits, decisions, verification, and the final
synthesis. Launch every child through Livery so its work is bounded and
audited:

`livery subagent run {shlex.quote(ticket_id)} --workspace {shlex.quote(str(workspace_root))} --parent-attempt {shlex.quote(attempt_id)} --role "<short-role>" --task "<focused-task>" --format json`

This dispatch allows at most {policy.max_subagents} direct children and a
maximum delegation depth of {policy.max_depth}. Do not use a runtime's native
untracked delegation feature.
"""


def _load_parent(root: Path, parent_attempt_id: str) -> DispatchAttempt:
    safe_attempt_id = sanitize_path_component(parent_attempt_id, fallback="attempt")
    if safe_attempt_id != parent_attempt_id:
        raise ValueError("Parent attempt id contains invalid path characters")
    path = attempts_dir(root) / f"{safe_attempt_id}.json"
    if not path.is_file():
        raise ValueError(f"Parent attempt not found: {parent_attempt_id}")
    parent = load_attempt(path)
    if parent.status not in {AttemptStatus.PREPARED, AttemptStatus.RUNNING}:
        raise ValueError(
            f"Parent attempt must be prepared or running (is {parent.status.value})"
        )
    return parent


def prepare_subagent_dispatch(
    *,
    root: Path,
    ticket_path: Path,
    parent_attempt_id: str,
    role: str,
    task: str,
    output_dir: Path,
):
    """Validate policy and prepare one read-only child as a normal dispatch."""
    from .dispatch import DispatchPrep, build_runtime_command, compose_subagent_prompt

    role = role.strip()
    task = task.strip()
    if not role or not task:
        raise ValueError("Subagent role and task must not be blank")
    if len(role) > 80:
        raise ValueError("Subagent role must be 80 characters or fewer")

    parent = _load_parent(root, parent_attempt_id)
    ticket_post = frontmatter.load(ticket_path)
    ticket_id = str(ticket_post.get("id") or ticket_path.stem)
    if parent.ticket_id != ticket_id:
        raise ValueError("Parent attempt belongs to a different ticket")

    root_assignee = parent.root_assignee or parent.assignee
    safe_assignee = sanitize_path_component(root_assignee, fallback="agent")
    if safe_assignee != root_assignee:
        raise ValueError("Parent attempt has an invalid root assignee")
    agent_dir = root / "agents" / root_assignee
    agent_post = frontmatter.load(agent_dir / "agent.md")
    agents_md = (agent_dir / "AGENTS.md").read_text()
    policy = resolve_policy(agent_post, ticket_post)
    if not policy.enabled:
        raise ValueError("Subagents are disabled by the agent or ticket policy")

    depth = parent.delegation_depth + 1
    if depth > policy.max_depth:
        raise ValueError(f"Subagent depth limit reached ({policy.max_depth})")
    runtime = str(agent_post.get("runtime") or "codex")
    model = agent_post.get("model")
    effort = agent_post.get("effort")
    cwd = parent.worktree_path or str(agent_post.get("cwd") or "")
    if not cwd:
        raise ValueError(f"Agent '{root_assignee}' has no cwd in agent.md")

    child_label = f"{root_assignee}:{role}"
    attempt_id = attempt_id_for(ticket_id, child_label)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_role = sanitize_path_component(role, fallback="subagent")
    prompt_path = output_dir / f"livery-subagent-{ticket_id}-{safe_role}-{attempt_id[-4:]}.txt"
    output_path = output_dir / f"livery-subagent-{ticket_id}-{safe_role}-{attempt_id[-4:]}.out"
    prompt_path.write_text(compose_subagent_prompt(
        assignee=root_assignee,
        role=role,
        task=task,
        agents_md=agents_md,
        ticket_md=ticket_path.read_text(),
        ticket_id=ticket_id,
    ))
    command = build_runtime_command(
        runtime=runtime,
        model=str(model) if model else None,
        effort=str(effort) if effort else None,
        cwd=cwd,
        prompt_path=prompt_path,
        output_path=output_path,
        workspace_root=root,
        assignee=child_label,
        ticket_id=ticket_id,
        attempt_id=attempt_id,
    )
    attempt = DispatchAttempt(
        schema_version=SCHEMA_VERSION,
        attempt_id=attempt_id,
        ticket_id=ticket_id,
        assignee=child_label,
        runtime=runtime,
        model=str(model) if model else None,
        workspace_root=str(root),
        agent_cwd=str(agent_post.get("cwd")),
        worktree_path=parent.worktree_path,
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
        trigger="subagent",
        parent_attempt_id=parent_attempt_id,
        root_assignee=root_assignee,
        subagent_role=role,
        delegation_depth=depth,
    )
    with _delegation_lock(root):
        children = [
            existing for existing in list_attempts(root)
            if existing.parent_attempt_id == parent_attempt_id
        ]
        if len(children) >= policy.max_subagents:
            raise ValueError(f"Subagent limit reached ({policy.max_subagents})")
        attempt_path = write_attempt(attempt, root)
    return DispatchPrep(
        ticket_id=ticket_id,
        assignee=child_label,
        runtime=runtime,
        model=str(model) if model else None,
        effort=str(effort) if effort else None,
        cwd=cwd,
        prompt_path=prompt_path,
        output_path=output_path,
        command=command,
        attempt_id=attempt_id,
        attempt_path=attempt_path,
    )
