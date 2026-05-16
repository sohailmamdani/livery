from __future__ import annotations

import json

from typer.testing import CliRunner

from livery.cli import app
from livery.init import init_workspace
from livery.paths import write_link


def _session_start_commands(path):
    data = json.loads(path.read_text())
    return [
        hook["command"]
        for group in data["hooks"]["SessionStart"]
        for hook in group["hooks"]
    ]


def test_install_agent_hooks_writes_codex_and_claude_startup_hooks(tmp_path, monkeypatch):
    workspace = tmp_path / "acme-livery"
    init_workspace(target=workspace, name="acme")
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(app, ["install-agent-hooks"])

    assert result.exit_code == 0, result.stdout + result.stderr
    codex_config = workspace / ".codex" / "config.toml"
    codex_hooks = workspace / ".codex" / "hooks.json"
    claude_settings = workspace / ".claude" / "settings.local.json"
    assert "codex_hooks = true" in codex_config.read_text()
    assert "LIVERY-MANAGED:BEGIN agent-hooks" in codex_config.read_text()
    assert _session_start_commands(codex_hooks) == ["livery session-brief --format text"]
    assert _session_start_commands(claude_settings) == ["livery session-brief --format text"]


def test_install_agent_hooks_targets_linked_repo_not_parent_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "acme-livery"
    init_workspace(target=workspace, name="acme")
    repo = tmp_path / "acme-api"
    repo.mkdir()
    write_link(repo_root=repo, workspace_root=workspace, repo_id="api")
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(app, ["install-agent-hooks"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert (repo / ".codex" / "hooks.json").exists()
    assert (repo / ".claude" / "settings.local.json").exists()
    assert not (workspace / ".codex").exists()


def test_install_agent_hooks_preserves_existing_session_start_groups(tmp_path, monkeypatch):
    workspace = tmp_path / "acme-livery"
    init_workspace(target=workspace, name="acme")
    hooks_path = workspace / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [{"type": "command", "command": "echo custom"}],
                        }
                    ]
                }
            }
        )
        + "\n"
    )
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(app, ["install-agent-hooks", "--engine", "codex"])

    assert result.exit_code == 0, result.stdout + result.stderr
    commands = _session_start_commands(hooks_path)
    assert commands == ["echo custom", "livery session-brief --format text"]


def test_install_agent_hooks_adds_codex_feature_inside_existing_features_table(tmp_path, monkeypatch):
    workspace = tmp_path / "acme-livery"
    init_workspace(target=workspace, name="acme")
    config = workspace / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[features]\ntui_app_server = true\n")
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(app, ["install-agent-hooks", "--engine", "codex"])

    assert result.exit_code == 0, result.stdout + result.stderr
    text = config.read_text()
    assert "[features]\n# LIVERY-MANAGED:BEGIN agent-hooks\ncodex_hooks = true" in text
    assert "tui_app_server = true" in text


def test_uninstall_agent_hooks_removes_only_livery_entries(tmp_path, monkeypatch):
    workspace = tmp_path / "acme-livery"
    init_workspace(target=workspace, name="acme")
    monkeypatch.chdir(workspace)
    install = CliRunner().invoke(app, ["install-agent-hooks"])
    assert install.exit_code == 0, install.stdout + install.stderr

    result = CliRunner().invoke(app, ["install-agent-hooks", "--uninstall"])

    assert result.exit_code == 0, result.stdout + result.stderr
    codex_config = workspace / ".codex" / "config.toml"
    codex_hooks = workspace / ".codex" / "hooks.json"
    claude_settings = workspace / ".claude" / "settings.local.json"
    assert "LIVERY-MANAGED:BEGIN agent-hooks" not in codex_config.read_text()
    assert "SessionStart" not in json.loads(codex_hooks.read_text()).get("hooks", {})
    assert "SessionStart" not in json.loads(claude_settings.read_text()).get("hooks", {})


def test_install_agent_hooks_rejects_legacy_workspace(tmp_path, monkeypatch):
    legacy = tmp_path / "framework-repo"
    legacy.mkdir()
    (legacy / "pyproject.toml").write_text("[project]\nname = \"livery\"\n")
    (legacy / "livery").mkdir()
    monkeypatch.chdir(legacy)

    result = CliRunner().invoke(app, ["install-agent-hooks"])

    assert result.exit_code == 1
    assert "workspace or linked repo" in result.stderr
