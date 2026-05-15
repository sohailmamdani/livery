"""Discoverable Livery capabilities and context-aware next steps."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import WORKSPACE_MARKER, WorkspaceResolution, resolve_workspace


@dataclass(frozen=True, slots=True)
class Capability:
    id: str
    group: str
    title: str
    summary: str
    commands: tuple[str, ...]
    when: str
    agent_note: str


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="setup-workspace",
        group="Set up a workspace",
        title="Create or refresh the coordination HQ",
        summary="Initialize a Livery workspace and keep framework-managed scaffolding current.",
        commands=("livery onboard", "livery init", "livery upgrade-workspace --apply"),
        when="Use when starting a new operational context or after upgrading Livery.",
        agent_note="If no workspace marker exists, suggest onboard first; it handles setup in guided order.",
    ),
    Capability(
        id="discover",
        group="Understand the current context",
        title="Ask Livery what is available from here",
        summary="Show the active workspace resolution and the most relevant next actions.",
        commands=("livery next", "livery capabilities", "livery where"),
        when="Use at the start of a CoS session or whenever the user asks what Livery can do.",
        agent_note="Run `livery next --format json` before inventing a workflow from memory.",
    ),
    Capability(
        id="linked-repos",
        group="Connect repos",
        title="Link project repos to a shared workspace",
        summary="Let commands run inside source repos while operating on the parent Livery workspace.",
        commands=(
            "livery link <workspace> --repo-id <repo>",
            "livery link <workspace> --repo-id <repo> --move-existing-workspace",
            "livery where",
        ),
        when="Use for multi-repo companies or when a repo was accidentally initialized as its own workspace.",
        agent_note="If a repo has `livery.toml`, plain link will fail; suggest `--move-existing-workspace`.",
    ),
    Capability(
        id="tickets",
        group="File and track work",
        title="Create, list, status, and close tickets",
        summary="Use markdown tickets as the shared queue for the user, CoS, and agents.",
        commands=(
            "livery ticket new --title \"...\" --assignee <id|cos>",
            "livery ticket list",
            "livery status",
            "livery ticket close <ticket-id> --summary \"...\"",
        ),
        when="Use for any task that should be remembered, delegated, closed, or audited.",
        agent_note="Prefer creating a ticket when work spans turns, needs delegation, or should be committed.",
    ),
    Capability(
        id="agents",
        group="Coordinate agents",
        title="Hire agents and dispatch tickets",
        summary="Create specialized agents, then send ticket work to their configured runtime and cwd.",
        commands=(
            "livery hire <agent-id>",
            "livery doctor",
            "livery dispatch prep <ticket-id> --worktree",
            "livery dispatch fan-out <ticket-id> --to a,b --run",
        ),
        when="Use when work should happen outside the CoS session or in a project repo/worktree.",
        agent_note="Dispatch only to hired agents; `assignee: cos` means the current CoS session owns it.",
    ),
    Capability(
        id="dispatch-observe",
        group="Observe background work",
        title="Inspect dispatch attempts and logs",
        summary="See whether dispatched work is running, stale, failed, or complete.",
        commands=("livery dispatch status", "livery dispatch tail <query>"),
        when="Use after dispatching work, after a timeout, or before closing a delegated ticket.",
        agent_note="Read dispatch output before summarizing delegated work back to the user.",
    ),
    Capability(
        id="walkie-talkie",
        group="Structured debate",
        title="Run structured AI-to-AI debate",
        summary="Create or automate append-only walkie-talkie transcripts between two peers.",
        commands=(
            "livery walkie new <topic>",
            "livery walkie show <query>",
            "livery walkie auto <topic> --peer-a <id> --peer-b <id>",
        ),
        when="Use when two agents should debate, review, or converge on a decision before implementation.",
        agent_note="Use auto mode for AI peers; use show to inspect transcript state before resuming.",
    ),
    Capability(
        id="integrations",
        group="Integrations and hygiene",
        title="Keep conventions and notifications in sync",
        summary="Install optional hooks, sync CoS convention files, and configure Telegram commands.",
        commands=(
            "livery install-hooks",
            "livery sync-cos --apply",
            "livery telegram register-commands",
        ),
        when="Use when multiple CoS convention files may drift or Telegram is configured.",
        agent_note="Run sync-cos after meaningful CoS convention edits when no hook is installed.",
    ),
)


def capability_dicts() -> list[dict[str, object]]:
    return [asdict(capability) for capability in CAPABILITIES]


def render_capabilities_text() -> str:
    lines = [
        "# Livery capabilities",
        "",
        "Use `livery next` for context-aware suggestions. Use `--format json` when an agent or tool needs structured output.",
    ]
    current_group: str | None = None
    for capability in CAPABILITIES:
        if capability.group != current_group:
            current_group = capability.group
            lines.extend(["", f"## {current_group}"])
        lines.extend(
            [
                "",
                f"### {capability.title}",
                capability.summary,
                "",
                "Commands:",
                *[f"- `{command}`" for command in capability.commands],
                f"When: {capability.when}",
                f"Agent note: {capability.agent_note}",
            ]
        )
    return "\n".join(lines) + "\n"


def render_capabilities_json() -> str:
    return json.dumps({"capabilities": capability_dicts()}, indent=2) + "\n"


def _workspace_summary(resolution: WorkspaceResolution) -> dict[str, str | None]:
    return {
        "kind": resolution.kind,
        "workspace_root": str(resolution.workspace_root),
        "marker_path": str(resolution.marker_path),
        "linked_repo_root": str(resolution.linked_repo_root)
        if resolution.linked_repo_root
        else None,
        "repo_id": resolution.repo_id,
        "workspace_id": resolution.workspace_id,
    }


def next_steps(start: Path | None = None) -> dict[str, object]:
    cwd = (start or Path.cwd()).resolve()
    try:
        resolution = resolve_workspace(cwd)
    except RuntimeError as e:
        suggestions = [
            {
                "title": "Create a workspace",
                "command": "livery onboard",
                "reason": "This directory is not inside a Livery workspace or linked repo.",
            },
            {
                "title": "Initialize here directly",
                "command": "livery init",
                "reason": "Use this for an isolated one-off project or a new operational context.",
            },
        ]
        if (cwd / ".git").exists():
            suggestions.append(
                {
                    "title": "Link this repo to an existing workspace",
                    "command": "livery link <workspace> --repo-id <repo>",
                    "reason": "This looks like a project repo; link it if a parent workspace should coordinate it.",
                }
            )
        return {
            "cwd": str(cwd),
            "resolution": None,
            "error": str(e),
            "suggestions": suggestions,
        }

    workspace = resolution.workspace_root
    suggestions: list[dict[str, str]] = []
    if resolution.kind == "legacy-workspace":
        suggestions.extend(
            [
                {
                    "title": "Confirm legacy resolution",
                    "command": "livery where",
                    "reason": "This resolved through the legacy `pyproject.toml + livery/` compatibility marker, not a normal `livery.toml` workspace.",
                },
                {
                    "title": "Create a modern workspace",
                    "command": "livery onboard",
                    "reason": "New Livery workspaces should live in a dedicated directory with a `livery.toml` marker.",
                },
            ]
        )
        return {
            "cwd": str(cwd),
            "resolution": _workspace_summary(resolution),
            "suggestions": suggestions,
        }

    if resolution.kind == "linked-repo":
        suggestions.append(
            {
                "title": "Confirm linked workspace",
                "command": "livery where",
                "reason": "You are in a linked project repo; commands operate on the parent workspace.",
            }
        )

    if not any((workspace / name).is_file() for name in ("CLAUDE.md", "AGENTS.md")):
        suggestions.append(
            {
                "title": "Refresh CoS convention files",
                "command": "livery upgrade-workspace --apply",
                "reason": "No CoS convention file was found in the workspace.",
            }
        )

    agent_files = list((workspace / "agents").glob("*/agent.md"))
    if not agent_files:
        suggestions.append(
            {
                "title": "Hire the first agent",
                "command": "livery hire <agent-id>",
                "reason": "No hired agents were found.",
            }
        )

    ticket_files = list((workspace / "tickets").glob("*.md"))
    if not ticket_files:
        suggestions.append(
            {
                "title": "File the first ticket",
                "command": 'livery ticket new --title "..." --assignee cos',
                "reason": "The workspace has no tickets yet.",
            }
        )
    else:
        suggestions.append(
            {
                "title": "Review current work",
                "command": "livery status",
                "reason": f"Found {len(ticket_files)} ticket file(s) in this workspace.",
            }
        )

    if not (workspace / ".git").exists():
        suggestions.append(
            {
                "title": "Put the workspace under git",
                "command": "git init",
                "reason": "Livery expects workspace mutations to be committed.",
            }
        )

    if (cwd / WORKSPACE_MARKER).is_file() and (cwd / ".git").exists():
        suggestions.append(
            {
                "title": "Convert to a linked repo if this was accidental",
                "command": "livery link <workspace> --repo-id <repo> --move-existing-workspace",
                "reason": "This directory is both a git repo and a Livery workspace.",
            }
        )

    return {
        "cwd": str(cwd),
        "resolution": _workspace_summary(resolution),
        "suggestions": suggestions,
    }


def render_next_text(start: Path | None = None) -> str:
    data = next_steps(start)
    lines = ["# Livery next", ""]
    lines.append(f"Cwd: {data['cwd']}")
    resolution = data.get("resolution")
    if isinstance(resolution, dict):
        lines.append(f"Workspace: {resolution['workspace_root']}")
        lines.append(f"Source: {resolution['kind']}")
        if resolution.get("linked_repo_root"):
            lines.append(f"Linked repo: {resolution['linked_repo_root']}")
        if resolution.get("repo_id"):
            lines.append(f"Repo id: {resolution['repo_id']}")
    else:
        lines.append("Workspace: (none)")
        if data.get("error"):
            lines.append(f"Problem: {data['error']}")

    lines.extend(["", "Suggested next steps:"])
    for item in data["suggestions"]:
        assert isinstance(item, dict)
        lines.append(f"- {item['title']}: `{item['command']}`")
        lines.append(f"  {item['reason']}")
    lines.extend(["", "For the full menu, run `livery capabilities`."])
    return "\n".join(lines) + "\n"


def render_next_json(start: Path | None = None) -> str:
    return json.dumps(next_steps(start), indent=2) + "\n"
