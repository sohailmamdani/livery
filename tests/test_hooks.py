from __future__ import annotations

from pathlib import Path

import pytest

from livery.hooks import (
    HOOK_MARKER,
    HookStatus,
    PRE_COMMIT_HOOK,
    install_hooks,
    uninstall_hooks,
)


def _git_workspace(tmp_path: Path) -> Path:
    """Workspace with a .git/ directory (no need for it to be a real git repo)."""
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    return root


def test_install_creates_hook_when_missing(tmp_path):
    root = _git_workspace(tmp_path)
    results = install_hooks(root)
    assert len(results) == 1
    r = results[0]
    assert r.name == "pre-commit"
    assert r.status == HookStatus.INSTALLED
    assert r.path.exists()
    assert HOOK_MARKER in r.path.read_text()


def test_install_marks_hook_executable(tmp_path):
    root = _git_workspace(tmp_path)
    results = install_hooks(root)
    mode = results[0].path.stat().st_mode & 0o777
    # owner-execute bit must be set; we set 0o755
    assert mode & 0o100 == 0o100


def test_install_idempotent_on_second_run(tmp_path):
    root = _git_workspace(tmp_path)
    install_hooks(root)
    results = install_hooks(root)
    assert results[0].status == HookStatus.UP_TO_DATE


def test_install_refreshes_when_our_hook_drifted(tmp_path):
    root = _git_workspace(tmp_path)
    install_hooks(root)
    hook_path = root / ".git" / "hooks" / "pre-commit"
    # Append something — still has the marker, so still ours, but content drifted
    hook_path.write_text(hook_path.read_text() + "\n# user fiddled with this\n")

    results = install_hooks(root)
    assert results[0].status == HookStatus.REFRESHED
    # File is back to canonical content
    assert hook_path.read_text() == PRE_COMMIT_HOOK


def test_install_skips_user_written_hook_without_force(tmp_path):
    root = _git_workspace(tmp_path)
    hook_path = root / ".git" / "hooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("#!/bin/sh\n# user wrote this themselves\necho hello\n")

    results = install_hooks(root)
    assert results[0].status == HookStatus.SKIPPED
    # User content untouched
    assert "user wrote this themselves" in hook_path.read_text()


def test_install_force_overwrites_user_hook(tmp_path):
    root = _git_workspace(tmp_path)
    hook_path = root / ".git" / "hooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("#!/bin/sh\n# user wrote this themselves\n")

    results = install_hooks(root, force=True)
    assert results[0].status == HookStatus.INSTALLED
    assert HOOK_MARKER in hook_path.read_text()


def test_install_raises_when_not_a_git_repo(tmp_path):
    root = tmp_path / "no-git"
    root.mkdir()
    with pytest.raises(FileNotFoundError) as ei:
        install_hooks(root)
    assert "git repository" in str(ei.value)


def test_uninstall_removes_our_hook(tmp_path):
    root = _git_workspace(tmp_path)
    install_hooks(root)
    hook_path = root / ".git" / "hooks" / "pre-commit"
    assert hook_path.exists()

    results = uninstall_hooks(root)
    assert len(results) == 1
    assert results[0].status == HookStatus.UNINSTALLED
    assert not hook_path.exists()


def test_uninstall_leaves_user_written_hook_alone(tmp_path):
    root = _git_workspace(tmp_path)
    hook_path = root / ".git" / "hooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("#!/bin/sh\n# theirs\n")

    results = uninstall_hooks(root)
    assert results[0].status == HookStatus.SKIPPED
    assert "theirs" in hook_path.read_text()


def test_uninstall_no_op_when_nothing_to_remove(tmp_path):
    root = _git_workspace(tmp_path)
    results = uninstall_hooks(root)
    assert results == []


def test_pre_commit_content_calls_sync_cos():
    """Sanity: the shipped hook actually invokes the right command."""
    assert "livery sync-cos --apply" in PRE_COMMIT_HOOK
    assert "git add" in PRE_COMMIT_HOOK
