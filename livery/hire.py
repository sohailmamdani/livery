"""`livery hire` — scaffold a new agent directory under `agents/<id>/`.

Creates:
  - agents/<id>/agent.md (frontmatter with runtime, model, cwd, reports_to + one-line role)
  - agents/<id>/AGENTS.md (stub with section headers the user fills in with their CoS)

The wizard captures the *structured* config (runtime, model, cwd) — the kind
of thing a CLI wizard does well. It does NOT try to extract the AGENTS.md
system prompt via sequential questions; that work happens in a follow-up
conversation with Claude Code, where the user's CoS can help with full
workspace context.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter


SUPPORTED_RUNTIMES: tuple[str, ...] = (
    "codex",
    "claude_code",
    "cursor",
    "lm_studio",
    "ollama",
)


# Model suggestions per runtime — offered as defaults in the wizard, not
# enforced. Users can type anything. `None` = no suggested default (the
# wizard will require an answer).
SUGGESTED_MODELS: dict[str, str | None] = {
    "codex": "gpt-5-codex",
    "claude_code": "claude-sonnet-4-6",
    "cursor": None,
    "lm_studio": None,
    "ollama": None,
}


AGENTS_MD_TEMPLATE = """\
# {name}

{role}

## Role

_Expand: what does this agent do, for whom, and what's the outcome? One paragraph._

## Scope

_What is this agent responsible for? Enumerate what's in scope so future tickets are unambiguous._

## Out of scope

_What belongs to other agents or to you? Prevents scope creep._

## Process

_Step-by-step workflow this agent follows per ticket. Be specific — this is the system prompt, not marketing copy._

## Quality bar

_What does "good work" look like here? Non-negotiables? (e.g., "cite or don't state", "push back at ≥70% confidence", "no speculation about people".)_

## Output format

_What does the agent produce? Files touched, data shape, reporting format. End every dispatch with DISPATCH_SUMMARY._
"""


def hire_agent(
    *,
    root: Path,
    agent_id: str,
    name: str,
    runtime: str,
    model: str | None,
    cwd: str,
    reports_to: str,
    role: str,
    hired: str,
    overwrite: bool = False,
) -> list[Path]:
    """Scaffold an agent directory. Returns the list of files created.

    Raises FileExistsError if the agent directory already exists and
    `overwrite` is False. Raises ValueError for invalid runtime.
    """
    if runtime not in SUPPORTED_RUNTIMES:
        raise ValueError(
            f"Unsupported runtime '{runtime}'. Supported: {', '.join(SUPPORTED_RUNTIMES)}"
        )

    agent_dir = root / "agents" / agent_id
    agent_md = agent_dir / "agent.md"
    agents_md = agent_dir / "AGENTS.md"

    if agent_dir.exists() and not overwrite:
        if agent_md.exists() or agents_md.exists():
            raise FileExistsError(
                f"Agent '{agent_id}' already exists at {agent_dir}. "
                "Pass overwrite=True to replace."
            )

    agent_dir.mkdir(parents=True, exist_ok=True)

    metadata: dict[str, object] = {
        "id": agent_id,
        "name": name,
        "runtime": runtime,
        "cwd": cwd,
        "reports_to": reports_to,
        "hired": hired,
    }
    if model:
        metadata["model"] = model

    post = frontmatter.Post(role.strip() + "\n", **metadata)
    agent_md.write_text(frontmatter.dumps(post) + "\n")
    agents_md.write_text(AGENTS_MD_TEMPLATE.format(name=name, role=role.strip()))

    return [agent_md, agents_md]
