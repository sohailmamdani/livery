from __future__ import annotations

import json
from pathlib import Path

import frontmatter
import pytest
from typer.testing import CliRunner

from livery.cli import app
from livery.talk import (
    build_talk_runtime_command,
    list_transcripts,
    run_talk_turn,
)


def _make_workspace(tmp_path: Path, *, runtime: str = "codex") -> Path:
    root = tmp_path / "workspace"
    agent_cwd = tmp_path / "project"
    agent_cwd.mkdir(parents=True)
    (root / "agents" / "web-swe").mkdir(parents=True)
    (root / "tickets").mkdir(parents=True)
    (root / "livery.toml").write_text('name = "test"\ncos_engines = ["codex"]\n')
    agent_md = frontmatter.Post(
        "Builds web software.",
        id="web-swe",
        name="Web SWE",
        runtime=runtime,
        model="test-model",
        cwd=str(agent_cwd),
        reports_to="cos",
        hired="2026-07-07",
    )
    (root / "agents" / "web-swe" / "agent.md").write_text(
        frontmatter.dumps(agent_md) + "\n"
    )
    (root / "agents" / "web-swe" / "AGENTS.md").write_text(
        "# Web SWE\n\nYou give pragmatic web engineering advice.\n"
    )
    return root


def test_build_talk_runtime_command_omits_unattended_dispatch_flags(tmp_path):
    prompt = tmp_path / "prompt.txt"
    output = tmp_path / "output.out"

    codex = build_talk_runtime_command(
        runtime="codex",
        model="gpt-5-codex",
        cwd="/repo",
        prompt_path=prompt,
        output_path=output,
    )
    claude = build_talk_runtime_command(
        runtime="claude_code",
        model="claude-opus-4-8",
        cwd="/repo",
        prompt_path=prompt,
        output_path=output,
    )
    cursor = build_talk_runtime_command(
        runtime="cursor",
        model="cursor-model",
        cwd="/repo",
        prompt_path=prompt,
        output_path=output,
    )

    assert "--dangerously-bypass-approvals-and-sandbox" not in codex
    assert "--dangerously-skip-permissions" not in claude
    assert "--force" not in cursor
    assert "codex exec" in codex
    assert "claude -p" in claude
    assert "cursor-agent --print" in cursor


def test_run_talk_turn_appends_transcript_and_prompt(tmp_path, monkeypatch):
    root = _make_workspace(tmp_path)
    calls: list[tuple[str, Path, int]] = []

    def fake_run(command: str, *, output_path: Path, timeout_seconds: int) -> int:
        calls.append((command, output_path, timeout_seconds))
        output_path.write_text("I would make this a ticket before editing.\n")
        return 0

    monkeypatch.setattr("livery.talk._run_shell_command", fake_run)

    result = run_talk_turn(
        workspace_root=root,
        agent_id="web-swe",
        message="Should we change the API now?",
        session_id="api-plan",
        timeout_seconds=30,
    )

    assert result.ok
    assert result.session_id == "api-plan"
    assert result.reply == "I would make this a ticket before editing."
    assert calls and calls[0][2] == 30
    transcript = result.transcript_path.read_text()
    assert "## operator -" in transcript
    assert "Should we change the API now?" in transcript
    assert "## web-swe -" in transcript
    assert "I would make this a ticket" in transcript
    prompt = result.prompt_path.read_text()
    assert "---BEGIN AGENTS.md---" in prompt
    assert "not a ticket dispatch" in prompt
    assert "Do not modify files" in prompt
    assert "Should we change the API now?" in prompt
    listed = list_transcripts(root)
    assert [item.session_id for item in listed] == ["api-plan"]
    assert listed[0].message_count == 2


def test_run_talk_turn_rejects_unknown_agent(tmp_path):
    root = _make_workspace(tmp_path)

    with pytest.raises(ValueError, match="not hired"):
        run_talk_turn(workspace_root=root, agent_id="ghost", message="hi")


def test_run_talk_turn_rejects_unsafe_agent_id(tmp_path):
    root = _make_workspace(tmp_path)

    with pytest.raises(ValueError, match="Invalid agent id"):
        run_talk_turn(workspace_root=root, agent_id="../ghost", message="hi")


def test_run_talk_turn_rejects_session_agent_mismatch(tmp_path, monkeypatch):
    root = _make_workspace(tmp_path)

    def fake_run(command: str, *, output_path: Path, timeout_seconds: int) -> int:
        output_path.write_text("reply\n")
        return 0

    monkeypatch.setattr("livery.talk._run_shell_command", fake_run)
    run_talk_turn(workspace_root=root, agent_id="web-swe", message="hi", session_id="shared")

    (root / "agents" / "critic").mkdir()
    agent_cwd = tmp_path / "critic"
    agent_cwd.mkdir()
    agent_md = frontmatter.Post(
        "Reviews plans.",
        id="critic",
        name="Critic",
        runtime="codex",
        cwd=str(agent_cwd),
        reports_to="cos",
        hired="2026-07-07",
    )
    (root / "agents" / "critic" / "agent.md").write_text(
        frontmatter.dumps(agent_md) + "\n"
    )
    (root / "agents" / "critic" / "AGENTS.md").write_text("# Critic\n")

    with pytest.raises(ValueError, match="belongs to agent 'web-swe'"):
        run_talk_turn(workspace_root=root, agent_id="critic", message="hi", session_id="shared")


def test_talk_cli_send_list_and_show_json(tmp_path, monkeypatch):
    root = _make_workspace(tmp_path)
    monkeypatch.chdir(root)

    def fake_run(command: str, *, output_path: Path, timeout_seconds: int) -> int:
        output_path.write_text("Talk reply\n")
        return 0

    monkeypatch.setattr("livery.talk._run_shell_command", fake_run)
    runner = CliRunner()

    sent = runner.invoke(
        app,
        ["talk", "web-swe", "Can you review this?", "--session", "review", "--format", "json"],
    )
    assert sent.exit_code == 0, sent.stdout + sent.stderr
    sent_payload = json.loads(sent.stdout)
    talk = sent_payload["talk"]
    assert talk["session_id"] == "review"
    assert talk["agent_id"] == "web-swe"
    assert talk["reply"] == "Talk reply"
    assert talk["relative_transcript_path"] == "talk/review.md"

    listed = runner.invoke(app, ["talk", "list", "--format", "json"])
    assert listed.exit_code == 0, listed.stdout + listed.stderr
    rows = json.loads(listed.stdout)["talk"]
    assert rows[0]["session_id"] == "review"
    assert rows[0]["message_count"] == 2

    shown = runner.invoke(app, ["talk", "show", "review", "--format", "json"])
    assert shown.exit_code == 0, shown.stdout + shown.stderr
    shown_payload = json.loads(shown.stdout)
    assert "Can you review this?" in shown_payload["content"]
    assert "Talk reply" in shown_payload["content"]
