"""Read-only discovery of links that predate the local workspace registry."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from .paths import LINK_MARKER, WORKSPACE_MARKER, resolve_workspace


@dataclass(slots=True)
class LinkedDiscovery:
    roots: list[Path]
    repos: set[Path] = field(default_factory=set)
    warnings: list[tuple[Path, str]] = field(default_factory=list)


def discover_linked_repos(workspace: Path) -> LinkedDiscovery:
    """Scan sibling projects and configured roots; also inspect agent working dirs.

    Never follow directory symlinks, scan dependency trees, or infer ownership
    from proximity. A project's own link must resolve back to this workspace.
    """
    workspace = workspace.resolve()
    marker = workspace / "livery.toml"
    raw = tomllib.loads(marker.read_text()) if marker.is_file() else {}
    config = raw.get("linked_repos", {})
    if not isinstance(config, dict):
        raise ValueError("linked_repos must be a TOML table")
    extras = config.get("search_roots", [])
    if not isinstance(extras, list) or not all(isinstance(p, str) and p.strip() for p in extras):
        raise ValueError("linked_repos.search_roots must be a list of nonempty paths")
    roots = {workspace if workspace.parent == Path(workspace.anchor) else workspace.parent}
    for value in extras:
        path = Path(os.path.expandvars(value)).expanduser()
        roots.add((workspace / path).resolve() if not path.is_absolute() else path.resolve())
    result = LinkedDiscovery(roots=sorted(roots))

    def inspect(path: Path, *, exact: bool) -> None:
        try:
            resolution = resolve_workspace(path)
            if (resolution.kind == "linked-repo"
                    and resolution.workspace_root == workspace
                    and (not exact or resolution.linked_repo_root == path)):
                result.repos.add(resolution.linked_repo_root)
        except (RuntimeError, ValueError, OSError) as exc:
            # Only warn for actual marker candidates, not arbitrary agent dirs.
            if exact:
                result.warnings.append((path, f"cannot inspect linked-repo marker: {exc}"))

    visited: set[Path] = set()
    skipped = {"node_modules", "vendor", "__pycache__"}

    def scan_error(error: OSError) -> None:
        result.warnings.append((Path(error.filename or workspace), f"link discovery incomplete: {error}"))

    for root in result.roots:
        if not root.is_dir():
            result.warnings.append((root, "link search root is missing or not a directory"))
            continue
        for directory, children, files in os.walk(root, followlinks=False, onerror=scan_error):
            current = Path(directory)
            if current in visited:
                children[:] = []
                continue
            visited.add(current)
            children[:] = sorted(name for name in children
                                 if not name.startswith(".") and name not in skipped
                                 and not (current / name).is_symlink())
            if LINK_MARKER in files:
                inspect(current, exact=True)
            if WORKSPACE_MARKER in files and current != workspace and current not in result.roots:
                # Another operation owns this subtree. Explicit search roots or
                # registered links can still select a project within it.
                children[:] = []

    # Agents may work outside the sibling-project tree. Inspect their cwd and
    # its ancestors using normal workspace resolution, without scanning that disk.
    for agent in sorted((workspace / "agents").glob("*/agent.md")):
        try:
            cwd = frontmatter.load(agent).get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                path = Path(os.path.expandvars(cwd)).expanduser()
                path = path if path.is_absolute() else workspace / path
                inspect(path.resolve(), exact=False)
        except Exception as exc:
            result.warnings.append((agent, f"cannot inspect agent working directory: {exc}"))
    return result
