"""`livery install-hooks` — install Livery's git hooks into a workspace.

Currently ships one hook: a `pre-commit` that runs `livery sync-cos --apply`
before every commit and re-stages any convention files the sync touched.
Keeps `CLAUDE.md` / `AGENTS.md` (and any other sibling convention files)
from drifting silently between commits.

Hooks are NOT installed automatically by `livery init` or
`livery upgrade-workspace` — `.git/hooks/` is user-owned territory, and
some users have existing hook setups (e.g. the `pre-commit` framework)
they don't want the framework to clobber. `livery install-hooks` is an
opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# A marker line at the top of every hook we ship. Used to recognize "this
# file is ours" on subsequent install runs (so we can refresh in place
# without prompting), and to distinguish from hooks the user wrote
# themselves (which we refuse to overwrite without --force).
HOOK_MARKER = "# LIVERY-MANAGED HOOK — do not edit; refresh via `livery install-hooks`."

PRE_COMMIT_HOOK = f"""\
#!/usr/bin/env bash
{HOOK_MARKER}
#
# Mirrors user content between sibling convention files (CLAUDE.md,
# AGENTS.md, ...) before each commit, so the two engines' files don't
# silently drift apart.
#
# Skips silently if Livery isn't on PATH or the workspace doesn't have
# convention files — never blocks a commit.
#
# To uninstall: `rm .git/hooks/pre-commit` (or `livery install-hooks
# --uninstall` if you'd rather not delete by hand).

set -euo pipefail

if ! command -v livery >/dev/null 2>&1; then
    exit 0
fi

# Run sync-cos. Send its output to stderr so the user sees it during the
# commit; treat failures as non-fatal (don't block commits if sync errors).
livery sync-cos --apply >&2 || exit 0

# Re-stage any convention files the sync may have modified, so they're
# part of the commit being made.
for f in CLAUDE.md AGENTS.md; do
    if [ -f "$f" ]; then
        git add "$f"
    fi
done
"""


class HookStatus(Enum):
    INSTALLED = "installed"  # newly written
    REFRESHED = "refreshed"  # was ours, content drifted, rewrote
    UP_TO_DATE = "up-to-date"  # was ours, already current
    SKIPPED = "skipped"      # user-written hook, refused without --force
    UNINSTALLED = "uninstalled"


@dataclass(slots=True)
class HookResult:
    name: str            # e.g. "pre-commit"
    path: Path           # absolute path to the hook file
    status: HookStatus
    detail: str = ""


# Map of hook name → content. Add more hooks here when we ship them.
SHIPPED_HOOKS: dict[str, str] = {
    "pre-commit": PRE_COMMIT_HOOK,
}


def _is_ours(path: Path) -> bool:
    """Heuristic: did Livery write this file? Recognize the HOOK_MARKER."""
    if not path.is_file():
        return False
    try:
        head = path.read_text()[:512]
    except OSError:
        return False
    return HOOK_MARKER in head


def install_hooks(workspace_root: Path, *, force: bool = False) -> list[HookResult]:
    """Install (or refresh) every Livery-shipped hook into `<root>/.git/hooks/`.

    Returns one HookResult per hook. Raises FileNotFoundError if the
    workspace isn't a git repo (no `.git/` directory).
    """
    git_dir = workspace_root / ".git"
    if not git_dir.is_dir():
        raise FileNotFoundError(
            f"{workspace_root} is not a git repository — `git init` first, "
            "then re-run `livery install-hooks`."
        )

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    results: list[HookResult] = []
    for name, content in SHIPPED_HOOKS.items():
        path = hooks_dir / name
        results.append(_install_one(path, name, content, force=force))
    return results


def _install_one(path: Path, name: str, content: str, *, force: bool) -> HookResult:
    if not path.exists():
        path.write_text(content)
        path.chmod(0o755)
        return HookResult(
            name=name, path=path, status=HookStatus.INSTALLED,
            detail="hook missing — wrote fresh copy",
        )

    if _is_ours(path):
        if path.read_text() == content:
            return HookResult(
                name=name, path=path, status=HookStatus.UP_TO_DATE,
                detail="already current",
            )
        path.write_text(content)
        path.chmod(0o755)
        return HookResult(
            name=name, path=path, status=HookStatus.REFRESHED,
            detail="Livery-managed hook updated to current shipped content",
        )

    # Hook exists, isn't ours.
    if not force:
        return HookResult(
            name=name, path=path, status=HookStatus.SKIPPED,
            detail="hook exists but isn't Livery-managed; pass --force to overwrite",
        )
    path.write_text(content)
    path.chmod(0o755)
    return HookResult(
        name=name, path=path, status=HookStatus.INSTALLED,
        detail="overwrote pre-existing user hook (--force)",
    )


def uninstall_hooks(workspace_root: Path) -> list[HookResult]:
    """Remove every Livery-shipped hook from `<root>/.git/hooks/`. User-written
    hooks (no LIVERY-MANAGED marker) are left alone."""
    git_dir = workspace_root / ".git"
    if not git_dir.is_dir():
        raise FileNotFoundError(f"{workspace_root} is not a git repository")

    hooks_dir = git_dir / "hooks"
    results: list[HookResult] = []
    for name in SHIPPED_HOOKS:
        path = hooks_dir / name
        if not path.exists():
            continue
        if not _is_ours(path):
            results.append(HookResult(
                name=name, path=path, status=HookStatus.SKIPPED,
                detail="hook isn't Livery-managed; left alone",
            ))
            continue
        path.unlink()
        results.append(HookResult(
            name=name, path=path, status=HookStatus.UNINSTALLED,
            detail="removed Livery-managed hook",
        ))
    return results
