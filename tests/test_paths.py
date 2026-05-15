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
    move_existing_workspace_to_link,
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


def _make_in_repo_workspace(tmp_path: Path) -> Path:
    repo = _make_repo(tmp_path)
    (repo / "livery.toml").write_text('name = "repo-local"\n')
    (repo / "tickets").mkdir()
    (repo / "tickets" / ".gitkeep").write_text("")
    (repo / "tickets" / "2026-05-15-001-fix-auth.md").write_text("ticket\n")
    (repo / "agents" / "dev").mkdir(parents=True)
    (repo / "agents" / "dev" / "agent.md").write_text("---\nid: dev\n---\n")
    (repo / "CLAUDE.md").write_text("# Repo CoS\n")
    (repo / ".claude" / "commands").mkdir(parents=True)
    (repo / ".claude" / "commands" / "ticket.md").write_text("command\n")
    (repo / ".livery" / "dispatch" / "attempts").mkdir(parents=True)
    (repo / ".livery" / "dispatch" / "attempts" / "a.json").write_text("{}\n")
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
    (workspace / LINK_MARKER).write_text(
        f'workspace = "{other_workspace}"\n',
        encoding="utf-8",
    )

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


def test_write_link_refuses_in_repo_workspace_without_move_flag(tmp_path):
    workspace = _make_workspace(tmp_path)
    repo = _make_in_repo_workspace(tmp_path)

    with pytest.raises(RuntimeError) as ei:
        write_link(repo_root=repo, workspace_root=workspace)

    assert "--move-existing-workspace" in str(ei.value)


def test_move_existing_workspace_to_parent_then_link(tmp_path):
    workspace = _make_workspace(tmp_path)
    (workspace / "tickets" / ".gitkeep").write_text("")
    (workspace / "CLAUDE.md").write_text("# Repo CoS\n")
    (workspace / ".claude" / "commands").mkdir(parents=True)
    (workspace / ".claude" / "commands" / "ticket.md").write_text("command\n")
    repo = _make_in_repo_workspace(tmp_path)

    result = move_existing_workspace_to_link(
        repo_root=repo,
        workspace_root=workspace,
        repo_id="api",
    )
    write_link(repo_root=repo, workspace_root=workspace, repo_id="api")

    assert not (repo / "livery.toml").exists()
    assert not (repo / "tickets").exists()
    assert not (repo / "agents").exists()
    assert not (repo / "CLAUDE.md").exists()
    assert (repo / LINK_MARKER).exists()
    assert (
        (workspace / "tickets" / "2026-05-15-001-fix-auth.md").read_text()
        == "ticket\n"
    )
    assert (workspace / "agents" / "dev" / "agent.md").exists()
    assert (workspace / "CLAUDE.md").read_text() == "# Repo CoS\n"
    assert (workspace / ".claude" / "commands" / "ticket.md").exists()
    assert (workspace / ".livery" / "dispatch" / "attempts" / "a.json").exists()
    assert result.preserved_config == (
        workspace / ".livery" / "linked-repos" / "api" / "livery.toml"
    )
    assert result.preserved_config.read_text() == 'name = "repo-local"\n'
    assert find_root(repo) == workspace


def test_move_existing_workspace_refuses_conflicts_without_moving(tmp_path):
    workspace = _make_workspace(tmp_path)
    (workspace / "tickets" / "2026-05-15-001-fix-auth.md").write_text("parent\n")
    repo = _make_in_repo_workspace(tmp_path)

    with pytest.raises(RuntimeError) as ei:
        move_existing_workspace_to_link(
            repo_root=repo,
            workspace_root=workspace,
            repo_id="api",
        )

    assert "destination conflicts" in str(ei.value)
    assert (repo / "livery.toml").exists()
    assert (
        (repo / "tickets" / "2026-05-15-001-fix-auth.md").read_text()
        == "ticket\n"
    )
    assert (
        (workspace / "tickets" / "2026-05-15-001-fix-auth.md").read_text()
        == "parent\n"
    )


def test_move_existing_workspace_refuses_preserved_config_conflict(tmp_path):
    workspace = _make_workspace(tmp_path)
    preserved_config = workspace / ".livery" / "linked-repos" / "api" / "livery.toml"
    preserved_config.parent.mkdir(parents=True)
    preserved_config.write_text("existing\n")
    repo = _make_in_repo_workspace(tmp_path)

    with pytest.raises(RuntimeError) as ei:
        move_existing_workspace_to_link(
            repo_root=repo,
            workspace_root=workspace,
            repo_id="api",
        )

    assert "destination conflicts" in str(ei.value)
    assert (repo / "livery.toml").read_text() == 'name = "repo-local"\n'
    assert (repo / "tickets" / "2026-05-15-001-fix-auth.md").exists()
    assert preserved_config.read_text() == "existing\n"


def test_move_existing_workspace_refuses_source_archive_collision(tmp_path):
    workspace = _make_workspace(tmp_path)
    repo = _make_in_repo_workspace(tmp_path)
    source_archive = repo / ".livery" / "linked-repos" / "api" / "livery.toml"
    source_archive.parent.mkdir(parents=True)
    source_archive.write_text("source archive\n")

    with pytest.raises(RuntimeError) as ei:
        move_existing_workspace_to_link(
            repo_root=repo,
            workspace_root=workspace,
            repo_id="api",
        )

    assert "destination conflicts" in str(ei.value)
    assert (repo / "livery.toml").read_text() == 'name = "repo-local"\n'
    assert source_archive.read_text() == "source archive\n"
    assert not (workspace / ".livery").exists()


def test_move_existing_workspace_sanitizes_preserved_config_repo_id(tmp_path):
    workspace = _make_workspace(tmp_path)
    repo = _make_in_repo_workspace(tmp_path)

    result = move_existing_workspace_to_link(
        repo_root=repo,
        workspace_root=workspace,
        repo_id="api/team",
    )

    assert result.preserved_config == (
        workspace / ".livery" / "linked-repos" / "api_team" / "livery.toml"
    )
    assert result.preserved_config.read_text() == 'name = "repo-local"\n'


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


def test_livery_link_command_moves_existing_workspace(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path)
    repo = _make_in_repo_workspace(tmp_path)

    monkeypatch.chdir(repo)
    result = CliRunner().invoke(
        app,
        [
            "link",
            str(workspace),
            "--repo-id",
            "api",
            "--move-existing-workspace",
        ],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Moved items:" in result.stdout
    assert not (repo / "livery.toml").exists()
    assert (repo / LINK_MARKER).exists()
    assert (workspace / "tickets" / "2026-05-15-001-fix-auth.md").exists()
    assert (workspace / ".livery" / "linked-repos" / "api" / "livery.toml").exists()


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
