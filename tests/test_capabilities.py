from __future__ import annotations

import json

from typer.testing import CliRunner

from livery.cli import app
from livery.init import init_workspace
from livery.paths import write_link


def test_capabilities_text_lists_feature_groups():
    result = CliRunner().invoke(app, ["capabilities"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "# Livery capabilities" in result.stdout
    assert "Connect repos" in result.stdout
    assert "livery link <workspace> --repo-id <repo>" in result.stdout
    assert "livery next --format json" in result.stdout
    assert "livery telegram register-commands" in result.stdout


def test_capabilities_json_is_agent_readable():
    result = CliRunner().invoke(app, ["capabilities", "--format", "json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    ids = {item["id"] for item in parsed["capabilities"]}
    assert "discover" in ids
    assert "linked-repos" in ids
    assert all("agent_note" in item for item in parsed["capabilities"])


def test_next_outside_workspace_suggests_setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["next"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Workspace: (none)" in result.stdout
    assert "livery onboard" in result.stdout
    assert "livery init" in result.stdout


def test_next_workspace_suggests_hire_and_ticket(tmp_path, monkeypatch):
    workspace = tmp_path / "acme-livery"
    init_workspace(target=workspace, name="acme")
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(app, ["next"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert f"Workspace: {workspace}" in result.stdout
    assert "livery hire <agent-id>" in result.stdout
    assert 'livery ticket new --title "..." --assignee cos' in result.stdout


def test_next_json_in_linked_repo_reports_resolution(tmp_path, monkeypatch):
    workspace = tmp_path / "acme-livery"
    init_workspace(target=workspace, name="acme")
    repo = tmp_path / "acme-api"
    repo.mkdir()
    write_link(repo_root=repo, workspace_root=workspace, repo_id="api")
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(app, ["next", "--format", "json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["resolution"]["kind"] == "linked-repo"
    assert parsed["resolution"]["workspace_root"] == str(workspace)
    assert parsed["resolution"]["repo_id"] == "api"
    assert any(item["command"] == "livery where" for item in parsed["suggestions"])


def test_next_legacy_resolution_does_not_suggest_workspace_work(tmp_path, monkeypatch):
    legacy = tmp_path / "framework-repo"
    legacy.mkdir()
    (legacy / "pyproject.toml").write_text("[project]\nname = \"livery\"\n")
    (legacy / "livery").mkdir()
    monkeypatch.chdir(legacy)

    result = CliRunner().invoke(app, ["next", "--format", "json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["resolution"]["kind"] == "legacy-workspace"
    commands = {item["command"] for item in parsed["suggestions"]}
    assert "livery where" in commands
    assert "livery onboard" in commands
    assert "livery hire <agent-id>" not in commands


def test_invalid_output_format_exits_cleanly():
    result = CliRunner().invoke(app, ["capabilities", "--format", "yaml"])

    assert result.exit_code == 1
    assert "--format must be one of: text, json" in result.stderr


def test_managed_cos_block_points_agents_to_discovery(tmp_path):
    workspace = tmp_path / "acme-livery"
    init_workspace(target=workspace, name="acme")

    content = (workspace / "AGENTS.md").read_text()
    assert "## Discoverability" in content
    assert "livery next --format json" in content
    assert "livery capabilities --format json" in content
