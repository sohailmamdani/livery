from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from livery.cli import app
from livery.identity import LINKED_BEGIN
from livery.init import init_workspace
from livery.linked_discovery import discover_linked_repos
from livery.upgrade import Action, apply_plan, compute_plan


def legacy_link(repo: Path, workspace: Path) -> Path:
    repo.mkdir(parents=True)
    # Bypass write_link to reproduce links made before registration existed.
    relative = os.path.relpath(workspace, repo)
    (repo / ".livery-link.toml").write_text(f"workspace = {json.dumps(relative)}\n")
    (repo / "AGENTS.md").write_text("# Existing project rules\nKeep my conventions.\n")
    return repo


def test_workspace_upgrade_discovers_all_old_sibling_links(tmp_path, monkeypatch):
    root = tmp_path / "hq"
    init_workspace(root, "HQ")
    repos = [legacy_link(tmp_path / "Weave", root),
             legacy_link(tmp_path / "clients/project with spaces", root)]
    monkeypatch.chdir(root)
    original = {p: (p / "AGENTS.md").read_bytes() for p in repos}
    preview = CliRunner().invoke(app, ["upgrade-workspace"])
    assert preview.exit_code == 0, preview.output
    assert "Verified linked projects: 2" in preview.stdout
    assert "Linked-project search roots:" in preview.stdout
    for repo in repos:
        assert str(repo / "AGENTS.md") in preview.stdout
        assert (repo / "AGENTS.md").read_bytes() == original[repo]
    assert not (root / ".livery/linked-repos.json").exists()
    result = CliRunner().invoke(app, ["upgrade-workspace", "--apply"])
    assert result.exit_code == 0, result.output
    for repo in repos:
        assert LINKED_BEGIN in (repo / "AGENTS.md").read_text()
        assert (repo / "AGENTS.md").read_bytes().endswith(original[repo])
    assert set(json.loads((root / ".livery/linked-repos.json").read_text())) == {str(p) for p in repos}
    assert not compute_plan(root).has_changes


def test_discovery_verifies_ownership_and_ignores_other_workspaces(tmp_path):
    root = tmp_path / "hq"
    other = tmp_path / "other"
    init_workspace(root, "HQ")
    init_workspace(other, "Other")
    own = legacy_link(tmp_path / "own-project", root)
    foreign = legacy_link(tmp_path / "foreign-project", other)
    legacy_link(other / "nested-project", root)
    plan = compute_plan(root)
    assert plan.linked_repos == [own]
    apply_plan(plan)
    assert LINKED_BEGIN not in (foreign / "AGENTS.md").read_text()


def test_additional_search_roots_and_agent_cwds_find_distant_links(tmp_path):
    root = tmp_path / "companies/hq"
    init_workspace(root, "HQ")
    extra = tmp_path / "projects"
    configured = legacy_link(extra / "one", root)
    agent_repo = legacy_link(tmp_path / "elsewhere/two", root)
    (agent_repo / "src").mkdir()
    agent = root / "agents/helper"
    agent.mkdir()
    (agent / "agent.md").write_text(f"---\ncwd: {agent_repo / 'src'}\n---\nHelper\n")
    with (root / "livery.toml").open("a") as stream:
        stream.write('\n[linked_repos]\nsearch_roots = ["../../projects"]\n')
    found = discover_linked_repos(root)
    assert found.repos == {configured, agent_repo}
    assert not found.warnings


def test_discovery_does_not_follow_symlinks_or_dependency_trees(tmp_path):
    root = tmp_path / "projects/hq"
    init_workspace(root, "HQ")
    hidden = legacy_link(tmp_path / "elsewhere/hidden", root)
    (root.parent / "symlink").symlink_to(hidden, target_is_directory=True)
    legacy_link(root.parent / "app/node_modules/package", root)
    legacy_link(root.parent / "app/.git/worktree", root)
    assert not discover_linked_repos(root).repos


def test_missing_search_root_and_bad_marker_are_visible_warnings(tmp_path, monkeypatch):
    root = tmp_path / "hq"
    init_workspace(root, "HQ")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / ".livery-link.toml").write_text("invalid = [")
    with (root / "livery.toml").open("a") as stream:
        stream.write('\n[linked_repos]\nsearch_roots = ["missing"]\n')
    plan = compute_plan(root)
    warnings = [item for item in plan.items if item.action == Action.WARN]
    assert {item.path for item in warnings} == {bad, root / "missing"}
    assert all(item.new_content is None for item in warnings)
    monkeypatch.chdir(root)
    result = CliRunner().invoke(app, ["upgrade-workspace", "--apply", "--force"])
    assert result.exit_code == 0, result.output
    assert "warning(s) remain" in result.stderr
    assert not (bad / "AGENTS.md").exists()


def test_unreadable_scan_directory_reports_incomplete_discovery(tmp_path, monkeypatch):
    root = tmp_path / "hq"
    init_workspace(root, "HQ")

    def denied_walk(path, *, followlinks, onerror):
        onerror(PermissionError(13, "Permission denied", str(path)))
        return iter(())

    monkeypatch.setattr("livery.linked_discovery.os.walk", denied_walk)
    discovery = discover_linked_repos(root)
    assert "incomplete" in discovery.warnings[0][1]


@pytest.mark.parametrize("setting", ['search_roots = "wrong"', 'search_roots = [42]', 'search_roots = [""]'])
def test_invalid_discovery_config_aborts_without_writes(tmp_path, setting):
    root = tmp_path / "hq"
    init_workspace(root, "HQ")
    repo = legacy_link(tmp_path / "project", root)
    with (root / "livery.toml").open("a") as stream:
        stream.write(f"\n[linked_repos]\n{setting}\n")
    with pytest.raises(ValueError, match="search_roots"):
        compute_plan(root)
    assert LINKED_BEGIN not in (repo / "AGENTS.md").read_text()
