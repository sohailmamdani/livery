"""Path sanitization + containment for generated worktree paths.

`livery dispatch prep --worktree` constructs worktree paths from ticket
ids and agent ids — both user-controlled inputs. Without sanitization, a
ticket id containing `..` or other path-traversal characters could push
the worktree outside its expected sibling-of-the-repo location. These
helpers normalize the inputs and verify the result before
`ensure_worktree` ever calls `git worktree add`.

No external deps; pure stdlib. The contract:

- `sanitize_path_component(s)` accepts any string and returns a string
  containing only `[A-Za-z0-9._-]`. Everything else becomes `_`. Leading
  and trailing dots are also replaced (a leading `.` would create a
  hidden directory; a sole `..` would escape).
- `assert_path_contained(path, root)` raises `PathContainmentError` if
  `path` (after resolution) is not strictly under `root`. The check is
  symlink-resistant: it operates on resolved paths.

Both functions are conservative on purpose. False positives (rejecting
something benign) cost the user a slightly mangled path. False negatives
(accepting something unsafe) cost a worktree being created where it
shouldn't be, or in worst case a `git worktree add` writing into an
unexpected directory. We optimize for false positives.
"""

from __future__ import annotations

import re
from pathlib import Path


# Anything outside [A-Za-z0-9._-] gets replaced with `_`. This is the
# POSIX "portable filename character set" plus dot — same set git
# itself prefers in branch names.
_SAFE_CHAR = re.compile(r"[^A-Za-z0-9._-]")


class PathContainmentError(ValueError):
    """Raised when a generated path escapes its expected root."""


def sanitize_path_component(value: str, *, fallback: str = "x") -> str:
    """Return a filesystem-safe single path component derived from `value`.

    - Replaces every character outside `[A-Za-z0-9._-]` with `_`.
    - Strips leading dots (so the result can't accidentally name a hidden
      directory or be a `.`/`..` traversal token).
    - Returns `fallback` if the result would otherwise be empty.

    Idempotent: `sanitize(sanitize(x)) == sanitize(x)`.
    """
    if not isinstance(value, str):
        raise TypeError(f"sanitize_path_component expects str, got {type(value).__name__}")

    cleaned = _SAFE_CHAR.sub("_", value)
    cleaned = cleaned.lstrip(".")
    if not cleaned:
        return fallback
    return cleaned


def assert_path_contained(path: Path, root: Path) -> Path:
    """Verify that `path`, fully resolved, is strictly under `root`.

    Returns the resolved path on success. Raises `PathContainmentError`
    when the resolved path is `root` itself, equal to `root`, or escapes
    via `..` / symlink.

    Both `path` and `root` are resolved (via `Path.resolve(strict=False)`)
    before comparison, so symlinks-in-path are followed and `..` segments
    are normalized.
    """
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)

    try:
        # `relative_to` raises ValueError if `resolved_path` is not under
        # `resolved_root`, which is exactly the failure mode we want to flag.
        relative = resolved_path.relative_to(resolved_root)
    except ValueError as e:
        raise PathContainmentError(
            f"path {resolved_path} is not contained under {resolved_root}"
        ) from e

    # `relative_to` returns `Path('.')` when path == root. That counts as
    # not-strictly-contained for our purposes — we want to enforce that the
    # generated worktree is inside root, not equal to it.
    if str(relative) == ".":
        raise PathContainmentError(
            f"path {resolved_path} equals containment root {resolved_root}; "
            "expected a strict subdirectory"
        )

    return resolved_path
