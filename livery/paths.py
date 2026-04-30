"""Workspace discovery.

A Livery workspace is marked by a `livery.toml` file at its root. The CLI
walks up from the user's cwd looking for that marker so commands work
anywhere inside the workspace tree.

For backward compatibility with the original self-hosted layout (Livery's
own repo, where the framework and workspace lived in one directory), we
still accept `pyproject.toml` + `livery/` as a fallback marker. New
workspaces should use `livery.toml` via `livery init`.
"""

from __future__ import annotations

from pathlib import Path

WORKSPACE_MARKER = "livery.toml"


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` (or cwd) until a Livery workspace marker is found.

    Prefers `livery.toml`; falls back to the legacy `pyproject.toml + livery/`
    self-hosted-repo layout for backward compatibility.
    """
    cwd = start or Path.cwd()
    for p in [cwd, *cwd.parents]:
        if (p / WORKSPACE_MARKER).is_file():
            return p
    for p in [cwd, *cwd.parents]:
        if (p / "pyproject.toml").is_file() and (p / "livery").is_dir():
            return p
    raise RuntimeError(
        f"Not inside a Livery workspace (started from {cwd}). "
        f"Run `livery init` to create one, or cd into an existing workspace."
    )
