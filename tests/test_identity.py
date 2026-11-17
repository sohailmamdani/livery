from __future__ import annotations

import json
import subprocess
import tomllib

import pytest
from typer.testing import CliRunner

from livery.cli import app
from livery.capabilities import session_brief
from livery.dispatch import build_runtime_command
from livery.identity import LINKED_BEGIN, linked_session_content, load_identity
from livery.init import init_workspace
from livery.paths import write_link
from livery.upgrade import apply_plan, compute_plan


def test_new_workspace_loads_custom_cos_and_live_team(tmp_path, monkeypatch):
    init_workspace(tmp_path, "Studio", description="Make useful things", cos_engine="codex")
    (tmp_path / "agents/cos/AGENTS.md").write_text("Be direct. Own editorial coordination.")
    custom = "\n## Workspace rule\nUse the editorial calendar.\n"
    with (tmp_path / "AGENTS.md").open("a") as stream:
        stream.write(custom)
    specialist = tmp_path / "agents/editor"
    specialist.mkdir()
    (specialist / "agent.md").write_text("---\nname: Editor\nreports_to: cos\n---\nReviews copy.")
    monkeypatch.chdir(tmp_path)
    response = CliRunner().invoke(app, ["session-brief", "--format", "json"])
    assert response.exit_code == 0, response.output
    data = json.loads(response.stdout)
    assert data["agent"]["id"] == "cos"
    assert data["agent"]["instructions"] == "Be direct. Own editorial coordination."
    assert data["workspace"]["description"] == "Make useful things"
    assert any("Use the editorial calendar" in row["instructions"] for row in data["workspace_instructions"])
    assert next(row for row in data["team"] if row["id"] == "editor")["role"] == "Reviews copy."
    text = CliRunner().invoke(app, ["session-brief"])
    assert "Be direct. Own editorial coordination." in text.stdout
    assert "Reviews copy." in text.stdout
    assert "Use the editorial calendar" in text.stdout


def test_upgrade_migrates_without_losing_config_or_instructions(tmp_path, monkeypatch):
    marker = tmp_path / "livery.toml"
    original = '# keep comment\nname = "Old"\n[telegram]\nchat_id = "example"\n'
    marker.write_text(original)
    (tmp_path / "AGENTS.md").write_text("# My rules\nBe concise.\n")
    monkeypatch.chdir(tmp_path)
    preview = CliRunner().invoke(app, ["upgrade-workspace"])
    assert preview.exit_code == 0, preview.output
    assert "agents/cos/AGENTS.md" in preview.stdout
    assert marker.read_text() == original
    assert not (tmp_path / "agents").exists()
    applied = CliRunner().invoke(app, ["upgrade-workspace", "--apply"])
    assert applied.exit_code == 0, applied.output
    config = tomllib.loads(marker.read_text())
    assert config["default_agent"] == "cos"
    assert config["telegram"]["chat_id"] == "example"
    assert "keep comment" in marker.read_text()
    assert "Be concise." in (tmp_path / "AGENTS.md").read_text()
    (tmp_path / "agents/cos/AGENTS.md").write_text("My custom CoS instructions.")
    assert not compute_plan(tmp_path).has_changes
    apply_plan(compute_plan(tmp_path), force=True)
    assert (tmp_path / "agents/cos/AGENTS.md").read_text() == "My custom CoS instructions."


def test_upgrade_reuses_existing_cos_and_respects_other_default(tmp_path):
    init_workspace(tmp_path, "Studio")
    marker = tmp_path / "livery.toml"
    marker.write_text(marker.read_text().replace('default_agent = "cos"\n', ''))
    (tmp_path / "agents/cos/AGENTS.md").write_text("Existing persona.")
    apply_plan(compute_plan(tmp_path))
    assert load_identity(tmp_path, "cos")["instructions"] == "Existing persona."
    (tmp_path / "agents/cos").rename(tmp_path / "agents/alex")
    marker.write_text(marker.read_text().replace('default_agent = "cos"', 'default_agent = "alex"'))
    assert not compute_plan(tmp_path).has_changes
    assert session_brief(tmp_path)["agent"]["id"] == "alex"
    assert not (tmp_path / "agents/cos").exists()


@pytest.mark.parametrize("default", ['"missing"', '"../escape"', '""', '42'])
def test_invalid_default_fails_without_mutation(tmp_path, monkeypatch, default):
    init_workspace(tmp_path, "Studio")
    marker = tmp_path / "livery.toml"
    marker.write_text(marker.read_text().replace('default_agent = "cos"', f'default_agent = {default}'))
    before = marker.read_text()
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["upgrade-workspace", "--apply"])
    assert result.exit_code == 1
    assert marker.read_text() == before
    assert "identity_error" in session_brief(tmp_path)


def test_incomplete_existing_cos_is_not_overwritten(tmp_path):
    (tmp_path / "livery.toml").write_text('name = "Old"\n')
    (tmp_path / "agents/cos").mkdir(parents=True)
    (tmp_path / "agents/cos/agent.md").write_text("An unfinished definition")
    with pytest.raises(ValueError, match="missing"):
        compute_plan(tmp_path)
    assert not (tmp_path / "agents/cos/AGENTS.md").exists()


def test_linked_upgrade_refreshes_registered_and_legacy_links(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    init_workspace(root, "Studio")
    repo = tmp_path / "project"
    repo.mkdir()
    write_link(repo_root=repo, workspace_root=root)
    (repo / "AGENTS.md").write_text("# Project rules\nRun tests.\n")
    monkeypatch.chdir(root)
    preview = CliRunner().invoke(app, ["upgrade-workspace"])
    assert preview.exit_code == 0, preview.output
    assert LINKED_BEGIN not in (repo / "AGENTS.md").read_text()
    apply_plan(compute_plan(root))
    assert "Run tests." in (repo / "AGENTS.md").read_text()
    assert LINKED_BEGIN in (repo / "AGENTS.md").read_text()
    assert session_brief(repo)["agent"]["id"] == "cos"
    assert not compute_plan(root).has_changes
    # Links made by older Livery versions have no registry: upgrade from the repo.
    (root / ".livery/linked-repos.json").unlink()
    (repo / "AGENTS.md").write_text("# Old project rules\n")
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(app, ["upgrade-workspace", "--apply"])
    assert result.exit_code == 0, result.output
    assert LINKED_BEGIN in (repo / "AGENTS.md").read_text()
    assert "Old project rules" in (repo / "AGENTS.md").read_text()


def test_stale_link_not_modified_even_with_force(tmp_path):
    root = tmp_path / "workspace"
    init_workspace(root, "Studio")
    repo = tmp_path / "project"
    repo.mkdir()
    write_link(repo_root=repo, workspace_root=root)
    (repo / ".livery-link.toml").unlink()
    (repo / "AGENTS.md").write_text("Unrelated project")
    apply_plan(compute_plan(root), force=True)
    assert (repo / "AGENTS.md").read_text() == "Unrelated project"


@pytest.mark.parametrize("runtime", ["codex", "claude_code", "cursor", "ollama"])
def test_runtime_startup_keeps_assigned_identity(tmp_path, runtime):
    init_workspace(tmp_path, "Studio")
    command = build_runtime_command(runtime=runtime, model="test", cwd=str(tmp_path),
                                    prompt_path=tmp_path / "prompt", output_path=tmp_path / "output",
                                    workspace_root=tmp_path, assignee="writer:reviewer")
    # Execute just the generated environment prefix, never a real model.
    prefix = command.split(";", 1)[0]
    result = subprocess.run(prefix + '; printf "%s\\n%s" "$LIVERY_ACTIVE_AGENT" "$LIVERY_ACTIVE_WORKSPACE"',
                            shell=True, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == ["writer:reviewer", str(tmp_path.resolve())]


def test_worker_brief_does_not_load_default_persona(tmp_path, monkeypatch):
    init_workspace(tmp_path, "Studio")
    monkeypatch.setenv("LIVERY_ACTIVE_AGENT", "writer:reviewer")
    monkeypatch.setenv("LIVERY_ACTIVE_WORKSPACE", str(tmp_path))
    data = session_brief(tmp_path)
    assert data["agent"] is None
    assert data["identity_source"] == "assignment"
    assert "writer:reviewer" in data["agent_instruction"]
    # Assignment markers from a different workspace cannot select this one's agent.
    monkeypatch.setenv("LIVERY_ACTIVE_WORKSPACE", str(tmp_path / "other"))
    assert session_brief(tmp_path)["agent"]["id"] == "cos"


def test_linked_markers_preserve_both_sides_and_reject_corruption():
    content = linked_session_content("Project custom rules\n")
    content = "Header\n" + content
    assert linked_session_content(content) == content
    with pytest.raises(ValueError):
        linked_session_content(LINKED_BEGIN + "incomplete")


def test_doctor_reports_broken_default(tmp_path, monkeypatch):
    from livery import doctor

    init_workspace(tmp_path, "Studio")
    monkeypatch.setattr(doctor, "check_all_runtimes", lambda: [])
    monkeypatch.setattr(doctor, "check_workspace_agents", lambda *_: [])
    assert doctor.run_doctor(tmp_path).ok
    (tmp_path / "agents/cos/AGENTS.md").unlink()
    report = doctor.run_doctor(tmp_path)
    assert not report.ok
    assert "missing" in report.to_dict()["identity_errors"][0]
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "Session identity" in result.stdout


def test_default_identity_rejects_external_symlink(tmp_path):
    root = tmp_path / "workspace"
    init_workspace(root, "Studio")
    prompt = root / "agents/cos/AGENTS.md"
    prompt.unlink()
    outside = tmp_path / "external.md"
    outside.write_text("Unrelated private instructions")
    prompt.symlink_to(outside)
    with pytest.raises(ValueError, match="not contained"):
        compute_plan(root)
    assert "Unrelated private instructions" not in str(session_brief(root))


def test_explicit_legacy_link_is_registered_on_apply(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    init_workspace(root, "Studio")
    repo = tmp_path / "older repo"
    repo.mkdir()
    (repo / ".livery-link.toml").write_text(f"workspace = {json.dumps(str(root))}\n")
    monkeypatch.chdir(root)
    args = ["upgrade-workspace", "--linked-repo", str(repo)]
    preview = CliRunner().invoke(app, args)
    assert preview.exit_code == 0, preview.output
    assert not (repo / "AGENTS.md").exists()
    result = CliRunner().invoke(app, args + ["--apply"])
    assert result.exit_code == 0, result.output
    assert json.loads((root / ".livery/linked-repos.json").read_text()) == [str(repo)]
    assert not compute_plan(root).has_changes


def test_legacy_workspace_brief_does_not_invent_identity(tmp_path):
    (tmp_path / "livery.toml").write_text('name = "Old"\n')
    data = session_brief(tmp_path)
    assert data["agent"] is None
    assert "No default agent configured" in data["agent_instruction"]
