from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from livery.cli import app
from livery.hire import hire_agent
from livery.init import init_workspace
from livery.paths import write_link


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    init_workspace(target=workspace, name="ws")
    return workspace


def test_ticket_commands_emit_json_records(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "ticket",
            "new",
            "--title",
            "Define harness API",
            "--assignee",
            "cos",
            "--description",
            "Add structured command output for harnesses.",
            "--format",
            "json",
        ],
    )

    assert created.exit_code == 0, created.stdout + created.stderr
    created_payload = json.loads(created.stdout)
    assert created_payload["schema_version"] == 1
    ticket = created_payload["ticket"]
    assert ticket["title"] == "Define harness API"
    assert ticket["assignee"] == "cos"
    assert ticket["status"] == "open"
    assert ticket["relative_path"].startswith("tickets/")
    assert "structured command output" in ticket["content"]

    listed = runner.invoke(app, ["ticket", "list", "--format", "json"])
    assert listed.exit_code == 0, listed.stdout + listed.stderr
    listed_payload = json.loads(listed.stdout)
    assert [item["id"] for item in listed_payload["tickets"]] == [ticket["id"]]

    shown = runner.invoke(app, ["ticket", "show", ticket["id"], "--format", "json"])
    assert shown.exit_code == 0, shown.stdout + shown.stderr
    shown_payload = json.loads(shown.stdout)
    assert shown_payload["ticket"]["id"] == ticket["id"]
    assert shown_payload["ticket"]["metadata"]["title"] == "Define harness API"


def test_ticket_new_records_explicit_repo_and_list_filters(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "ticket",
            "new",
            "--title",
            "Patch API client",
            "--assignee",
            "cos",
            "--repo",
            "api",
            "--description",
            "Fix the generated client in the API repo.",
            "--format",
            "json",
        ],
    )

    assert created.exit_code == 0, created.stdout + created.stderr
    ticket = json.loads(created.stdout)["ticket"]
    assert ticket["repo"] == "api"
    assert ticket["metadata"]["repo"] == "api"

    listed = runner.invoke(app, ["ticket", "list", "--repo", "api", "--format", "json"])
    assert listed.exit_code == 0, listed.stdout + listed.stderr
    assert [item["id"] for item in json.loads(listed.stdout)["tickets"]] == [ticket["id"]]

    other = runner.invoke(app, ["ticket", "list", "--repo", "web", "--format", "json"])
    assert other.exit_code == 0, other.stdout + other.stderr
    assert json.loads(other.stdout)["tickets"] == []


def test_ticket_new_from_linked_repo_records_repo_metadata(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    repo = tmp_path / "acme-api"
    repo.mkdir()
    write_link(repo_root=repo, workspace_root=workspace, repo_id="api")
    monkeypatch.chdir(repo)
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "ticket",
            "new",
            "--title",
            "Fix linked repo bug",
            "--assignee",
            "cos",
            "--description",
            "Track the bug from the linked repo.",
            "--format",
            "json",
        ],
    )

    assert created.exit_code == 0, created.stdout + created.stderr
    ticket = json.loads(created.stdout)["ticket"]
    assert ticket["repo"] == "api"
    assert ticket["metadata"]["repo"] == "api"
    assert ticket["path"].startswith(str(workspace / "tickets"))
    assert not (repo / "tickets").exists()

    listed = runner.invoke(app, ["ticket", "list", "--repo", "api", "--format", "json"])
    assert listed.exit_code == 0, listed.stdout + listed.stderr
    assert [item["id"] for item in json.loads(listed.stdout)["tickets"]] == [ticket["id"]]


def test_ticket_new_from_linked_repo_without_repo_id_uses_directory_name(
    tmp_path,
    monkeypatch,
):
    workspace = _workspace(tmp_path)
    repo = tmp_path / "acme-web"
    repo.mkdir()
    write_link(repo_root=repo, workspace_root=workspace)
    monkeypatch.chdir(repo)
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "ticket",
            "new",
            "--title",
            "Fix linked web bug",
            "--assignee",
            "cos",
            "--description",
            "Track the bug from a linked repo without repo_id.",
            "--format",
            "json",
        ],
    )

    assert created.exit_code == 0, created.stdout + created.stderr
    ticket = json.loads(created.stdout)["ticket"]
    assert ticket["repo"] == "acme-web"
    assert ticket["metadata"]["repo"] == "acme-web"


def test_agents_command_lists_hired_agents(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    agent_cwd = tmp_path / "content"
    agent_cwd.mkdir()
    hire_agent(
        root=workspace,
        agent_id="writer",
        name="Senior Writer",
        runtime="claude_code",
        model="claude-sonnet-4-6",
        cwd=str(agent_cwd),
        reports_to="cos",
        role="Writes product narratives.",
        hired="2026-06-13",
    )
    hire_agent(
        root=workspace,
        agent_id="research",
        name="Research Lead",
        runtime="codex",
        model="gpt-5-codex",
        cwd=str(agent_cwd),
        reports_to="cos",
        role="Finds and verifies facts.",
        hired="2026-06-14",
    )
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    listed = runner.invoke(app, ["agents", "--format", "json"])

    assert listed.exit_code == 0, listed.stdout + listed.stderr
    payload = json.loads(listed.stdout)
    assert payload["schema_version"] == 1
    assert payload["workspace_root"] == str(workspace)
    assert [agent["id"] for agent in payload["agents"]] == ["research", "writer"]
    writer = next(agent for agent in payload["agents"] if agent["id"] == "writer")
    assert writer["name"] == "Senior Writer"
    assert writer["runtime"] == "claude_code"
    assert writer["model"] == "claude-sonnet-4-6"
    assert writer["cwd"] == str(agent_cwd)
    assert writer["role"] == "Writes product narratives."
    assert writer["relative_path"] == "agents/writer/agent.md"
    assert writer["prompt_relative_path"] == "agents/writer/AGENTS.md"

    text = runner.invoke(app, ["agents"])
    assert text.exit_code == 0, text.stdout + text.stderr
    assert "writer" in text.stdout
    assert "claude_code" in text.stdout
    assert str(agent_cwd) in text.stdout


def test_agents_command_from_linked_repo_lists_parent_workspace_agents(
    tmp_path,
    monkeypatch,
):
    workspace = _workspace(tmp_path)
    agent_cwd = tmp_path / "api"
    agent_cwd.mkdir()
    hire_agent(
        root=workspace,
        agent_id="api-dev",
        name="API Developer",
        runtime="codex",
        model="gpt-5-codex",
        cwd=str(agent_cwd),
        reports_to="cos",
        role="Builds API features.",
        hired="2026-06-13",
    )
    repo = tmp_path / "linked-api"
    repo.mkdir()
    write_link(repo_root=repo, workspace_root=workspace, repo_id="api")
    monkeypatch.chdir(repo)

    listed = CliRunner().invoke(app, ["agents", "--format", "json"])

    assert listed.exit_code == 0, listed.stdout + listed.stderr
    payload = json.loads(listed.stdout)
    assert payload["workspace_root"] == str(workspace)
    assert [agent["id"] for agent in payload["agents"]] == ["api-dev"]
    assert payload["agents"][0]["path"].startswith(str(workspace / "agents"))
    assert not (repo / "agents").exists()


def test_memory_commands_emit_json_records(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    added = runner.invoke(
        app,
        [
            "memory",
            "add",
            "--type",
            "lesson",
            "--title",
            "Use JSON in harnesses",
            "--body",
            "Harness skills should parse JSON, not human text.",
            "--format",
            "json",
        ],
    )

    assert added.exit_code == 0, added.stdout + added.stderr
    added_payload = json.loads(added.stdout)
    assert added_payload["schema_version"] == 1
    memory = added_payload["memory"]
    assert memory["type"] == "lesson"
    assert memory["relative_path"].startswith("memory/lessons/")
    assert "parse JSON" in memory["content"]

    searched = runner.invoke(app, ["memory", "search", "harnesses", "--format", "json"])
    assert searched.exit_code == 0, searched.stdout + searched.stderr
    matches = json.loads(searched.stdout)["memory"]
    assert [item["id"] for item in matches] == [memory["id"]]
    assert "parse JSON" in matches[0]["content"]

    shown = runner.invoke(app, ["memory", "show", memory["id"], "--format", "json"])
    assert shown.exit_code == 0, shown.stdout + shown.stderr
    assert json.loads(shown.stdout)["memory"]["title"] == "Use JSON in harnesses"


def test_where_and_status_emit_json(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    ticket_result = runner.invoke(
        app,
        [
            "ticket",
            "new",
            "--title",
            "Check board",
            "--assignee",
            "cos",
            "--description",
            "Make the status command interesting.",
        ],
    )
    assert ticket_result.exit_code == 0, ticket_result.stdout + ticket_result.stderr

    from livery import doctor

    monkeypatch.setattr(doctor.shutil, "which", lambda binary: f"/bin/{binary}")
    monkeypatch.setattr(doctor, "_http_reachable", lambda *args, **kwargs: True)

    where_result = runner.invoke(app, ["where", "--format", "json"])
    assert where_result.exit_code == 0, where_result.stdout + where_result.stderr
    where_payload = json.loads(where_result.stdout)
    assert where_payload["schema_version"] == 1
    resolution = where_payload["resolution"]
    assert resolution["kind"] == "workspace"
    assert resolution["workspace_root"] == str(workspace)

    status_result = runner.invoke(app, ["status", "--format", "json"])
    assert status_result.exit_code == 0, status_result.stdout + status_result.stderr
    status = json.loads(status_result.stdout)
    assert status["workspace_root"] == str(workspace)
    assert status["open_by_assignee"] == {"cos": 1}
    assert status["runtimes"]["total"] > 0


def test_dispatch_commands_emit_json(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    agent_cwd = tmp_path / "repo"
    agent_cwd.mkdir()
    hire_agent(
        root=workspace,
        agent_id="dev",
        name="Developer",
        runtime="codex",
        model="gpt-5",
        cwd=str(agent_cwd),
        reports_to="cos",
        role="Implements test tickets.",
        hired="2026-06-13",
    )

    monkeypatch.chdir(workspace)
    runner = CliRunner()
    ticket_result = runner.invoke(
        app,
        [
            "ticket",
            "new",
            "--title",
            "Prepare dispatch JSON",
            "--assignee",
            "dev",
            "--description",
            "Exercise dispatch prep JSON.",
            "--format",
            "json",
        ],
    )
    assert ticket_result.exit_code == 0, ticket_result.stdout + ticket_result.stderr
    ticket_id = json.loads(ticket_result.stdout)["ticket"]["id"]

    output_dir = tmp_path / "dispatch-out"
    prep_result = runner.invoke(
        app,
        [
            "dispatch",
            "prep",
            ticket_id,
            "--output-dir",
            str(output_dir),
            "--format",
            "json",
        ],
    )
    assert prep_result.exit_code == 0, prep_result.stdout + prep_result.stderr
    prep_payload = json.loads(prep_result.stdout)
    assert prep_payload["schema_version"] == 1
    dispatch = prep_payload["dispatch"]
    assert dispatch["ticket_id"] == ticket_id
    assert dispatch["assignee"] == "dev"
    assert dispatch["runtime"] == "codex"
    assert dispatch["attempt_id"]
    assert Path(dispatch["prompt_path"]).is_file()

    status_result = runner.invoke(
        app,
        [
            "dispatch",
            "status",
            "--output-dir",
            str(output_dir),
            "--format",
            "json",
        ],
    )
    assert status_result.exit_code == 0, status_result.stdout + status_result.stderr
    status = json.loads(status_result.stdout)
    assert status["workspace_root"] == str(workspace)
    assert status["dispatches"][0]["source"] == "attempt"
    assert status["dispatches"][0]["status"] == "prepared"


def test_dispatch_tail_json_returns_content(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.chdir(workspace)
    output_dir = tmp_path / "dispatch-out"
    output_dir.mkdir()
    out_path = output_dir / "livery-dispatch-2026-06-13-001-x-dev.out"
    out_path.write_text("one\ntwo\nthree\n")

    result = CliRunner().invoke(
        app,
        [
            "dispatch",
            "tail",
            "dev",
            "--output-dir",
            str(output_dir),
            "--lines",
            "2",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["dispatch"]["label"] == "2026-06-13-001-x-dev"
    assert payload["lines"] == 2
    assert payload["content"] == "two\nthree\n"
