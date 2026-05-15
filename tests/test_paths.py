from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from livery.cli import app
from livery.paths import (
    LINK_MARKER,
    add_link_to_git_exclude,
    find_root,
    resolve_workspace,
    write_link,
)


def _make_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "acme-livery"
    root.mkdir()
    (root / "livery.toml").write_text('name = "acme"\n')
    (root / "tickets").mkdir()
    return root


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "acme-api"
    repo.mkdir()
    return repo


def test_find_root_resolves_linked_repo_to_workspace(tmp_path):
    workspace = _make_workspace(tmp_path)
    repo = _make_repo(tmp_path)
    write_link(repo_root=repo, workspace_root=workspace, repo_id="api")

    assert find_root(repo) == workspace


def test_find_root_resolves_linked_repo_subdirectory(tmp_path):
    workspace = _make_workspace(tmp_path)
    repo = _make_repo(tmp_path)
    subdir = repo / "src" / "pkg"
    subdir.mkdir(parents=True)
    write_link(repo_root=repo, workspace_root=workspace, repo_id="api")

    resolved = resolve_workspace(subdir)

    assert resolved.kind == "linked-repo"
    assert resolved.workspace_root == workspace
    assert resolved.linked_repo_root == repo
    assert resolved.repo_id == "api"


def test_direct_workspace_marker_wins_at_same_directory(tmp_path):
    workspace = _make_workspace(tmp_path)
    other_workspace = tmp_path / "other-livery"
    other_workspace.mkdir()
    (other_workspace / "livery.toml").write_text('name = "other"\n')
    write_link(repo_root=workspace, workspace_root=other_workspace, force=True)

    resolved = resolve_workspace(workspace)

    assert resolved.kind == "workspace"
    assert resolved.workspace_root == workspace


def test_link_to_missing_workspace_raises(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / LINK_MARKER).write_text('workspace = "../missing"\n')

    with pytest.raises(RuntimeError) as ei:
        find_root(repo)

    assert "no livery.toml" in str(ei.value)


def test_write_link_refuses_to_overwrite_without_force(tmp_path):
    workspace = _make_workspace(tmp_path)
    repo = _make_repo(tmp_path)
    write_link(repo_root=repo, workspace_root=workspace)

    with pytest.raises(FileExistsError):
        write_link(repo_root=repo, workspace_root=workspace)


def test_livery_link_command_writes_marker_and_git_exclude(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path)
    repo = _make_repo(tmp_path)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)

    monkeypatch.chdir(repo)
    result = CliRunner().invoke(
        app,
        ["link", str(workspace), "--repo-id", "api"],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    link_text = (repo / LINK_MARKER).read_text()
    assert f'workspace = "{workspace}"' in link_text
    assert 'repo_id = "api"' in link_text
    assert LINK_MARKER in (repo / ".git" / "info" / "exclude").read_text()


def test_git_exclude_supports_worktree_git_file(tmp_path):
    repo = _make_repo(tmp_path)
    actual_git_dir = tmp_path / "git-storage" / "worktrees" / "acme-api"
    actual_git_dir.mkdir(parents=True)
    (repo / ".git").write_text(f"gitdir: {actual_git_dir}\n")

    assert add_link_to_git_exclude(repo) is True
    assert LINK_MARKER in (actual_git_dir / "info" / "exclude").read_text()


def test_livery_where_reports_linked_workspace(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path)
    repo = _make_repo(tmp_path)
    write_link(repo_root=repo, workspace_root=workspace, repo_id="api")

    monkeypatch.chdir(repo)
    result = CliRunner().invoke(app, ["where"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert f"Workspace: {workspace}" in result.stdout
    assert "Source:    linked-repo" in result.stdout
    assert f"Repo:      {repo}" in result.stdout
    assert "Repo id:   api" in result.stdout


def test_workspace_command_from_linked_repo_uses_linked_workspace(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path)
    repo = _make_repo(tmp_path)
    write_link(repo_root=repo, workspace_root=workspace, repo_id="api")

    monkeypatch.chdir(repo)
    result = CliRunner().invoke(
        app,
        [
            "ticket",
            "new",
            "--title",
            "Fix callback",
            "--description",
            "Fix the auth callback.",
        ],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    assert not (repo / "tickets").exists()
    tickets = list((workspace / "tickets").glob("*fix-callback.md"))
    assert len(tickets) == 1
