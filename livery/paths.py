"""Workspace discovery.

A Livery workspace is marked by a `livery.toml` file at its root. The CLI
walks up from the user's cwd looking for that marker so commands work
anywhere inside the workspace tree.

Project repos can also be linked to a workspace with `.livery-link.toml`.
That lets commands run from inside a repo while still operating on the
coordination workspace elsewhere.

For backward compatibility with the original self-hosted layout (Livery's
own repo, where the framework and workspace lived in one directory), we
still accept `pyproject.toml` + `livery/` as a fallback marker. New
workspaces should use `livery.toml` via `livery init`.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

WORKSPACE_MARKER = "livery.toml"
LINK_MARKER = ".livery-link.toml"


@dataclass(slots=True)
class WorkspaceResolution:
    """Result of resolving a cwd to the workspace Livery commands should use."""

    workspace_root: Path
    kind: str
    """`workspace`, `linked-repo`, or `legacy-workspace`."""
    marker_path: Path
    linked_repo_root: Path | None = None
    repo_id: str | None = None
    workspace_id: str | None = None


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _resolve_link(link_path: Path) -> WorkspaceResolution:
    try:
        raw = tomllib.loads(link_path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise RuntimeError(f"Invalid Livery link file at {link_path}: {e}") from e

    workspace_raw = raw.get("workspace")
    if not isinstance(workspace_raw, str) or not workspace_raw.strip():
        raise RuntimeError(
            f"Invalid Livery link file at {link_path}: missing `workspace` path."
        )

    workspace_expanded = os.path.expandvars(os.path.expanduser(workspace_raw))
    workspace_root = Path(workspace_expanded)
    if not workspace_root.is_absolute():
        workspace_root = link_path.parent / workspace_root
    workspace_root = workspace_root.resolve()

    if not (workspace_root / WORKSPACE_MARKER).is_file():
        raise RuntimeError(
            f"Livery link at {link_path} points to {workspace_root}, "
            f"but that directory has no {WORKSPACE_MARKER}."
        )

    repo_id_raw = raw.get("repo_id")
    workspace_id_raw = raw.get("workspace_id")
    return WorkspaceResolution(
        workspace_root=workspace_root,
        kind="linked-repo",
        marker_path=link_path,
        linked_repo_root=link_path.parent,
        repo_id=str(repo_id_raw) if repo_id_raw else None,
        workspace_id=str(workspace_id_raw) if workspace_id_raw else None,
    )


def resolve_workspace(start: Path | None = None) -> WorkspaceResolution:
    """Resolve `start` to a Livery workspace.

    Search order is nearest marker first:
      - `livery.toml` means the directory itself is a workspace.
      - `.livery-link.toml` means this is a project repo linked to a workspace.
      - legacy `pyproject.toml + livery/` is accepted last for old layouts.
    """
    cwd = (start or Path.cwd()).resolve()
    for p in [cwd, *cwd.parents]:
        marker = p / WORKSPACE_MARKER
        if marker.is_file():
            return WorkspaceResolution(
                workspace_root=p,
                kind="workspace",
                marker_path=marker,
            )
        link = p / LINK_MARKER
        if link.is_file():
            return _resolve_link(link)

    for p in [cwd, *cwd.parents]:
        marker = p / "pyproject.toml"
        if marker.is_file() and (p / "livery").is_dir():
            return WorkspaceResolution(
                workspace_root=p,
                kind="legacy-workspace",
                marker_path=marker,
            )

    raise RuntimeError(
        f"Not inside a Livery workspace or linked repo (started from {cwd}). "
        f"Run `livery init` to create a workspace, or `livery link <workspace>` "
        f"from a project repo."
    )


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` (or cwd) until a Livery workspace marker is found.

    Also honors `.livery-link.toml` project-repo links, returning the linked
    workspace root. Falls back to the legacy `pyproject.toml + livery/`
    self-hosted-repo layout for backward compatibility.
    """
    return resolve_workspace(start).workspace_root


def write_link(
    *,
    repo_root: Path,
    workspace_root: Path,
    repo_id: str | None = None,
    workspace_id: str | None = None,
    force: bool = False,
) -> Path:
    """Write `.livery-link.toml` in `repo_root` pointing at `workspace_root`."""
    repo_root = repo_root.resolve()
    workspace_root = workspace_root.expanduser().resolve()

    if not repo_root.is_dir():
        raise RuntimeError(
            f"Repo path does not exist or is not a directory: {repo_root}"
        )
    if not (workspace_root / WORKSPACE_MARKER).is_file():
        raise RuntimeError(
            f"{workspace_root} is not a Livery workspace: missing {WORKSPACE_MARKER}"
        )

    link_path = repo_root / LINK_MARKER
    if link_path.exists() and not force:
        raise FileExistsError(f"{link_path} already exists; pass --force to overwrite.")

    lines = [
        "# Livery linked-repo marker.",
        "# This file points project-level `livery` commands at a workspace.",
        f"workspace = {_toml_string(str(workspace_root))}",
    ]
    if workspace_id:
        lines.append(f"workspace_id = {_toml_string(workspace_id)}")
    if repo_id:
        lines.append(f"repo_id = {_toml_string(repo_id)}")
    link_path.write_text("\n".join(lines) + "\n")
    return link_path


def add_link_to_git_exclude(repo_root: Path) -> bool:
    """Add `.livery-link.toml` to `.git/info/exclude` for local-only links.

    Returns True when an exclude file was updated, False when the repo is not
    a normal git checkout or the entry already existed.
    """
    git_marker = repo_root.resolve() / ".git"
    if git_marker.is_dir():
        git_dir = git_marker
    elif git_marker.is_file():
        text = git_marker.read_text(errors="replace").strip()
        prefix = "gitdir:"
        if not text.startswith(prefix):
            return False
        raw_git_dir = text[len(prefix):].strip()
        git_dir = Path(raw_git_dir)
        if not git_dir.is_absolute():
            git_dir = git_marker.parent / git_dir
    else:
        return False

    if not git_dir.exists():
        return False

    info_dir = git_dir / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    exclude_path = info_dir / "exclude"
    existing = exclude_path.read_text() if exclude_path.exists() else ""
    lines = [line.strip() for line in existing.splitlines()]
    if LINK_MARKER in lines:
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    exclude_path.write_text(
        existing
        + prefix
        + "# Livery linked-repo marker (machine-local path)\n"
        + LINK_MARKER
        + "\n"
    )
    return True
