"""Harness entrypoints installed inside linked project repos.

Workspace scaffolding belongs in the parent Livery workspace. Linked repos
need a smaller overlay: local harness commands/skills that tell the CoS it is
standing in a project repo whose Livery commands resolve to a parent workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import frontmatter


LINKED_REPO_ENGINES = ("codex", "claude_code")


@dataclass(slots=True)
class LinkedRepoAssetResult:
    engine: str
    path: Path
    status: str
    detail: str = ""


LINKED_REPO_HELLO_SLASH = """---
description: Orient this linked repo to its parent Livery workspace
livery: managed
---

Help the user start from live Livery linked-repo context.

Steps:
1. Run `livery session-brief --format json`.
2. Confirm `resolution.kind` is `linked-repo`. If it is not, say so plainly
   and continue with the normal Livery workspace flow.
3. Briefly tell the user:
   - this directory is a Livery linked repo
   - the linked repo path
   - the parent workspace path
   - the repo id, if present
4. Run `livery status --format json`.
5. Summarize the useful workspace status signals. Make clear that Livery
   commands run here mutate the parent workspace's tickets, memory, and
   walkie-talkie records.
"""


LINKED_REPO_NEW_TICKET_SLASH = """---
description: Create a parent-workspace Livery ticket from this linked repo
argument-hint: [title or brief description]
livery: managed
---

Help the user create a Livery ticket from this linked project repo.

Steps:
1. Run `livery where --format json` and confirm `resolution.kind` is
   `linked-repo`.
2. Gather missing fields conversationally:
   - **title** — one-line, imperative
   - **assignee** — agent id from the parent workspace, `cos`, or blank
   - **repo** — Livery records the linked repo id/name automatically from
     `.livery-link.toml`; pass `--repo <repo>` only to override it
   - **description** — one paragraph stating the goal
   - **context** (optional) — include repo-local details such as paths,
     branch names and constraints
3. Run `livery ticket new --title "..." --assignee <id|cos> --description "..." [--context "..."] --format json`.
   If assignee is blank, omit `--assignee`.
4. Tell the user the ticket id, repo metadata, and relative path from the JSON,
   and note that it was created in the parent workspace, not inside this repo.
"""


LINKED_REPO_LIST_AGENTS_SLASH = """---
description: List parent-workspace Livery agents from this linked repo
livery: managed
---

Help the user see which parent-workspace agents are available while working
from this linked project repo.

Steps:
1. Run `livery where --format json` and confirm `resolution.kind` is
   `linked-repo`.
2. Run `livery agents --format json`.
3. If there are no agents, say that plainly and mention `livery hire <agent-id>`
   only if the user is trying to delegate work.
4. Otherwise summarize the returned agent ids, names, runtimes, models, and
   working directories. Make clear these agents belong to the parent workspace.
"""


LINKED_REPO_WALKIE_SLASH = """---
description: Create a parent-workspace Walkie-Talkie from this linked repo
argument-hint: [topic] [--with peer-id]
livery: managed
---

Help the user create or continue a Livery Walkie-Talkie from this linked
project repo.

Steps:
1. Run `livery where --format json` and confirm `resolution.kind` is
   `linked-repo`.
2. If creating a manual walkie, gather the topic and peer identity, then run
   `livery walkie new <topic> --with <peer> --as <self>`.
3. If running an automated debate, gather `--peer-a`, `--peer-b`, and any
   briefing or ticket context, then run `livery walkie auto <topic> --peer-a <id> --peer-b <id> ...`.
4. For repo-local planning context, prefer `--briefing @path` from the current
   repo; Livery reads the file from this cwd while creating the transcript in
   the parent workspace.
5. Tell the user the created transcript path under the parent workspace's
   `walkie-talkie/` directory.
"""


LINKED_REPO_HELLO_SKILL = """---
name: livery-hello
description: Orient this linked project repo to its parent Livery workspace. Use when the user invokes /livery-hello, asks "where are we?", or wants the harness to understand that this repo is linked to Livery.
livery: managed
---

# Livery hello for linked repos

Use this skill to ground the current harness session in live linked-repo
context.

## Steps

1. Run `livery session-brief --format json`.
2. Confirm `resolution.kind` is `linked-repo`. If it is not, say so plainly
   and continue with the normal Livery workspace flow.
3. Briefly tell the user:
   - this directory is a Livery linked repo
   - the linked repo path
   - the parent workspace path
   - the repo id, if present
4. Run `livery status --format json`.
5. Summarize the useful workspace status signals. Make clear that Livery
   commands run here mutate the parent workspace's tickets, memory, and
   walkie-talkie records.
"""


LINKED_REPO_NEW_TICKET_SKILL = """---
name: livery-new-ticket
description: Create a parent-workspace Livery ticket from this linked project repo. Use when the user invokes /livery-new-ticket or asks to file/create/open a ticket while working in a linked repo.
livery: managed
---

# Create a Livery ticket from a linked repo

This repo is only the work surface. The ticket record belongs in the parent
Livery workspace.

## Steps

1. Run `livery where --format json` and confirm `resolution.kind` is
   `linked-repo`.
2. Gather missing fields conversationally:
   - **title** — one-line, imperative
   - **assignee** — agent id from the parent workspace, `cos`, or blank
   - **repo** — Livery records the linked repo id/name automatically from
     `.livery-link.toml`; pass `--repo <repo>` only to override it
   - **description** — one paragraph stating the goal
   - **context** (optional) — include repo-local details such as paths,
     branch names and constraints
3. Run `livery ticket new --title "..." --assignee <id|cos> --description "..." [--context "..."] --format json`.
   If assignee is blank, omit `--assignee`.
4. Tell the user the ticket id, repo metadata, and relative path from the JSON,
   and note that it was created in the parent workspace, not inside this repo.
"""


LINKED_REPO_LIST_AGENTS_SKILL = """---
name: livery-list-agents
description: List parent-workspace Livery agents from a linked project repo. Use when the user invokes /livery-list-agents, asks what agents exist, asks who can take work, needs valid assignee ids, or wants available runtimes/cwds before creating tickets, dispatching, or starting Walkie-Talkies.
livery: managed
---

# List Livery agents from a linked repo

This repo is only the work surface. The hired-agent inventory belongs to the
parent workspace.

## Steps

1. Run `livery where --format json` and confirm `resolution.kind` is
   `linked-repo`.
2. Run `livery agents --format json`.
3. If the response contains no agents, say that plainly. Suggest
   `livery hire <agent-id>` only when the user is trying to delegate work.
4. Otherwise summarize the agent ids, names, runtimes, models, and working
   directories from the JSON.
5. Use agent ids exactly as returned by the command when creating tickets,
   dispatching work, or starting Walkie-Talkies.

Do not hand-scan `agents/` in this repo; linked repos resolve to the parent
workspace through Livery's CLI.
"""


LINKED_REPO_WALKIE_SKILL = """---
name: livery-walkie-talkie
description: Create or continue a parent-workspace Livery Walkie-Talkie from this linked project repo. Use when the user invokes /livery-walkie-talkie or asks to plan/debate a feature from a linked repo.
livery: managed
---

# Livery Walkie-Talkie from a linked repo

Use this skill when the user wants a planning debate while working inside a
project repo linked to a parent Livery workspace.

## Steps

1. Run `livery where --format json` and confirm `resolution.kind` is
   `linked-repo`.
2. If creating a manual walkie, gather the topic and peer identity, then run
   `livery walkie new <topic> --with <peer> --as <self>`.
3. If running an automated debate, gather `--peer-a`, `--peer-b`, and any
   briefing or ticket context, then run `livery walkie auto <topic> --peer-a <id> --peer-b <id> ...`.
4. For repo-local planning context, prefer `--briefing @path` from the current
   repo; Livery reads the file from this cwd while creating the transcript in
   the parent workspace.
5. Tell the user the created transcript path under the parent workspace's
   `walkie-talkie/` directory.
"""


def parse_linked_repo_engines(engines: str) -> list[str]:
    raw = [part.strip() for part in engines.split(",") if part.strip()]
    selected = raw or list(LINKED_REPO_ENGINES)
    unknown = [engine for engine in selected if engine not in LINKED_REPO_ENGINES]
    if unknown:
        expected = ", ".join(LINKED_REPO_ENGINES)
        raise RuntimeError(
            f"Unknown linked-repo harness engine(s): {', '.join(unknown)}. "
            f"Expected: {expected}."
        )
    return selected


def _is_livery_managed(path: Path) -> bool:
    try:
        post = frontmatter.load(path)
    except Exception:
        return False
    return post.get("livery") == "managed"


def _write_asset(
    *,
    engine: str,
    path: Path,
    content: str,
    force: bool,
) -> LinkedRepoAssetResult:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return LinkedRepoAssetResult(
            engine,
            path,
            "installed",
            "Installed linked-repo Livery harness entrypoint.",
        )

    current = path.read_text()
    if current == content:
        return LinkedRepoAssetResult(engine, path, "unchanged", "Already current.")

    if _is_livery_managed(path) or force:
        path.write_text(content)
        return LinkedRepoAssetResult(
            engine,
            path,
            "updated",
            "Refreshed linked-repo Livery harness entrypoint.",
        )

    return LinkedRepoAssetResult(
        engine,
        path,
        "skipped",
        "User-written file exists at linked-repo Livery entrypoint path.",
    )


def install_linked_repo_assets(
    *,
    repo_root: Path,
    engines: str = "codex,claude_code",
    force: bool = False,
) -> list[LinkedRepoAssetResult]:
    repo_root = repo_root.resolve()
    selected = parse_linked_repo_engines(engines)
    results: list[LinkedRepoAssetResult] = []

    if "claude_code" in selected:
        for name, content in (
            ("livery-hello.md", LINKED_REPO_HELLO_SLASH),
            ("livery-list-agents.md", LINKED_REPO_LIST_AGENTS_SLASH),
            ("livery-new-ticket.md", LINKED_REPO_NEW_TICKET_SLASH),
            ("livery-walkie-talkie.md", LINKED_REPO_WALKIE_SLASH),
        ):
            results.append(
                _write_asset(
                    engine="claude_code",
                    path=repo_root / ".claude" / "commands" / name,
                    content=content,
                    force=force,
                )
            )
        for name, content in (
            ("livery-hello", LINKED_REPO_HELLO_SKILL),
            ("livery-list-agents", LINKED_REPO_LIST_AGENTS_SKILL),
            ("livery-new-ticket", LINKED_REPO_NEW_TICKET_SKILL),
            ("livery-walkie-talkie", LINKED_REPO_WALKIE_SKILL),
        ):
            results.append(
                _write_asset(
                    engine="claude_code",
                    path=repo_root / ".claude" / "skills" / name / "SKILL.md",
                    content=content,
                    force=force,
                )
            )

    if "codex" in selected:
        for name, content in (
            ("livery-hello", LINKED_REPO_HELLO_SKILL),
            ("livery-list-agents", LINKED_REPO_LIST_AGENTS_SKILL),
            ("livery-new-ticket", LINKED_REPO_NEW_TICKET_SKILL),
            ("livery-walkie-talkie", LINKED_REPO_WALKIE_SKILL),
        ):
            results.append(
                _write_asset(
                    engine="codex",
                    path=repo_root / ".agents" / "skills" / name / "SKILL.md",
                    content=content,
                    force=force,
                )
            )

    return results
