"""Workspace session identity, shared by startup, scaffolding, and upgrades."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

import frontmatter

from .config import load
from .cos_engines import find_managed_block
from .paths_safety import assert_path_contained, sanitize_path_component


SESSION_INSTRUCTIONS = """## Session identity

At the start of a new user conversation, before your first substantive reply,
run `livery session-brief --format json` unless a startup hook has already
supplied the brief. Follow its `agent_instruction` and load its agent
instructions, workspace context, and team roster. Briefly identify yourself
by name and role. Do not invent an identity when the brief reports an error.

The default agent is the workspace's Chief of Staff for user conversations.
An explicit assignment to a named agent (dispatch, Talk, Walkie-Talkie,
scheduled work, or advisory child) takes precedence: keep that assigned
identity and task restrictions, never adopt the default CoS instead.
Workspace and project rules still apply. An agent's runtime/model settings
do not switch the model or harness of an existing chat.
"""

LINKED_BEGIN = "<!-- LIVERY-SESSION:BEGIN -->"
LINKED_END = "<!-- LIVERY-SESSION:END -->"


def linked_session_content(current: str) -> str:
    """Maintain only our startup block, leaving repository instructions intact."""
    fresh = f"{LINKED_BEGIN}\n{SESSION_INSTRUCTIONS}\n{LINKED_END}"
    if LINKED_BEGIN in current or LINKED_END in current:
        if current.count(LINKED_BEGIN) != 1 or current.count(LINKED_END) != 1:
            raise ValueError("Malformed Livery session markers; repair before upgrading")
        start = current.index(LINKED_BEGIN)
        end = current.index(LINKED_END) + len(LINKED_END)
        if end < start:
            raise ValueError("Reversed Livery session markers")
        return current[:start] + fresh + current[end:]
    return fresh + "\n\n" + current


def load_identity(root: Path, agent_id: str) -> dict[str, object]:
    if not isinstance(agent_id, str) or not agent_id or sanitize_path_component(agent_id) != agent_id:
        raise ValueError("default_agent must be a single safe agent id")
    directory = root / "agents" / agent_id
    assert_path_contained(root / "agents", root)
    assert_path_contained(directory, root / "agents")
    for filename in ("agent.md", "AGENTS.md"):
        path = directory / filename
        assert_path_contained(path, root / "agents")
        if not path.is_file():
            raise ValueError(f"Agent {agent_id!r} is missing {path}")
    try:
        post = frontmatter.load(directory / "agent.md")
    except Exception as exc:
        raise ValueError(f"Cannot read agent {agent_id!r}: {exc}") from exc
    instructions = (directory / "AGENTS.md").read_text().strip()
    if not instructions:
        raise ValueError(f"Agent {agent_id!r} has empty AGENTS.md instructions")
    return {
        "id": agent_id,
        "name": str(post.get("name") or agent_id),
        "role": post.content.strip(),
        "instructions": instructions,
        "instructions_path": str(directory / "AGENTS.md"),
    }


def session_identity(root: Path) -> dict[str, object]:
    cfg = load(root)
    active = os.environ.get("LIVERY_ACTIVE_AGENT")
    active_root = os.environ.get("LIVERY_ACTIVE_WORKSPACE")
    worker = bool(active and active_root and Path(active_root).resolve() == root.resolve())
    selected = None if worker else cfg.default_agent
    result: dict[str, object] = {
        "default_agent": cfg.default_agent,
        "identity_source": "assignment" if worker else "default",
        "assigned_agent": active if worker else None,
        "workspace": {"name": cfg.name, "description": cfg.description},
        "agent": None,
        "team": [],
    }
    if selected is not None:
        result["agent"] = load_identity(root, selected)
    conventions = []
    seen = set()
    for filename in ("AGENTS.md", "CLAUDE.md"):
        path = root / filename
        if path.is_file():
            content = path.read_text()
            block = find_managed_block(content)
            if block:
                content = content[:block[0]] + content[block[1]:]
            content = content.strip()
            if content and content not in seen:
                conventions.append({"path": str(path), "instructions": content})
                seen.add(content)
    result["workspace_instructions"] = conventions
    team = []
    for path in sorted((root / "agents").glob("*/agent.md")):
        try:
            assert_path_contained(path, root / "agents")
            post = frontmatter.load(path)
            team.append({"id": path.parent.name, "name": str(post.get("name") or path.parent.name),
                         "role": post.content.strip(), "reports_to": post.get("reports_to")})
        except Exception:
            team.append({"id": path.parent.name, "error": "Cannot read agent definition"})
    result["team"] = team
    return result


def chief_of_staff_files(root: Path, name: str, runtime: str = "codex") -> dict[Path, str]:
    """Create-only defaults. Subsequent upgrades never rewrite these files."""
    role = f"Chief of Staff for {name}: coordinate priorities, agents, and follow-through."
    post = frontmatter.Post(role, id="cos", name=f"{name} Chief of Staff",
                           runtime=runtime, cwd=str(root.resolve()), reports_to="operator",
                           subagents="never")
    instructions = f"""# {name} Chief of Staff

{role}

You are the operator's primary contact in this workspace. Speak plainly,
question weak assumptions, and keep decisions and next steps clear.

## Responsibilities

- Understand the workspace purpose and follow its convention files.
- Use the live session brief and `livery agents --format json` to understand
  the team. Consult specialists with Talk; dispatch scoped work through tickets.
- Keep tickets current, review results, and report blockers honestly.
- Use workspace memory for durable knowledge; do not claim to remember chats
  you have not read. Record new durable knowledge only when authorized.
- Respect the operator's approval boundaries and each agent's scope.

## Custom instructions

Customize this file with this Chief of Staff's persona, remit, and working
preferences. Existing workspace conventions remain in the workspace's
AGENTS.md / CLAUDE.md; do not discard them.
"""
    return {root / "agents/cos/agent.md": frontmatter.dumps(post) + "\n",
            root / "agents/cos/AGENTS.md": instructions}


def assigned_command(command: str, root: Path | None, agent_id: str | None) -> str:
    """Carry assigned identity into runtime startup hooks and their subprocesses."""
    if root is None or not agent_id:
        return command
    return (f"export LIVERY_ACTIVE_AGENT={shlex.quote(agent_id)} "
            f"LIVERY_ACTIVE_WORKSPACE={shlex.quote(str(root.resolve()))}; {command}")
