from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import frontmatter
import pytest
from typer.testing import CliRunner

from livery.attempts import AttemptStatus, load_attempt, write_attempt
from livery.cli import app
from livery.dispatch import prepare_dispatch
from livery.subagents import prepare_subagent_dispatch


def _workspace(
    tmp_path: Path,
    *,
    agent_policy: str = "allowed",
    ticket_policy: str = "inherit",
    max_subagents: int = 2,
    max_depth: int = 1,
) -> tuple[Path, Path, Path]:
    root = tmp_path / "workspace"
    repo = tmp_path / "repo"
    agent_dir = root / "agents" / "dev"
    ticket_path = root / "tickets" / "2026-08-18-001-build-it.md"
    agent_dir.mkdir(parents=True)
    ticket_path.parent.mkdir()
    repo.mkdir()
    (root / "livery.toml").write_text("name = 'test'\n")
    agent = frontmatter.Post(
        "Builds the product.",
        id="dev",
        name="Developer",
        runtime="codex",
        model="gpt-5",
        cwd=str(repo),
        reports_to="cos",
        hired="2026-08-18",
        subagents=agent_policy,
        max_subagents=max_subagents,
        max_subagent_depth=max_depth,
    )
    (agent_dir / "agent.md").write_text(frontmatter.dumps(agent) + "\n")
    (agent_dir / "AGENTS.md").write_text("# Developer\n\nImplement carefully.\n")
    ticket = frontmatter.Post(
        "## Description\n\nBuild it.\n\n## Thread\n",
        id="2026-08-18-001-build-it",
        title="Build it",
        assignee="dev",
        status="open",
        created="2026-08-18T00:00:00Z",
        updated="2026-08-18T00:00:00Z",
        subagents=ticket_policy,
    )
    ticket_path.write_text(frontmatter.dumps(ticket) + "\n")
    return root, repo, ticket_path


def _parent(root: Path, ticket_path: Path, output_dir: Path):
    return prepare_dispatch(
        root=root,
        ticket_path=ticket_path,
        output_dir=output_dir,
        make_worktree=False,
    )


def test_parent_prompt_exposes_audited_subagent_command(tmp_path):
    root, _, ticket_path = _workspace(tmp_path, ticket_policy="encouraged")
    parent = _parent(root, ticket_path, tmp_path / "out")

    prompt = parent.prompt_path.read_text()
    assert "## Advisory subagents" in prompt
    assert "Use advisory subagents" in prompt
    assert f"--parent-attempt {parent.attempt_id}" in prompt
    assert "livery subagent run" in prompt
    assert "native\nuntracked delegation feature" in prompt


def test_agent_never_is_hard_ceiling_even_when_ticket_encourages(tmp_path):
    root, _, ticket_path = _workspace(
        tmp_path, agent_policy="never", ticket_policy="encouraged"
    )
    parent = _parent(root, ticket_path, tmp_path / "out")
    assert "livery subagent run" not in parent.prompt_path.read_text()

    with pytest.raises(ValueError, match="disabled"):
        prepare_subagent_dispatch(
            root=root,
            ticket_path=ticket_path,
            parent_attempt_id=str(parent.attempt_id),
            role="reviewer",
            task="Review the design.",
            output_dir=tmp_path / "out",
        )


def test_child_attempt_is_linked_read_only_and_auditable(tmp_path):
    root, repo, ticket_path = _workspace(tmp_path)
    parent = _parent(root, ticket_path, tmp_path / "out")
    child = prepare_subagent_dispatch(
        root=root,
        ticket_path=ticket_path,
        parent_attempt_id=str(parent.attempt_id),
        role="security reviewer",
        task="Identify security risks in the proposed implementation.",
        output_dir=tmp_path / "out",
    )

    attempt = load_attempt(child.attempt_path)
    assert attempt.parent_attempt_id == parent.attempt_id
    assert attempt.root_assignee == "dev"
    assert attempt.subagent_role == "security reviewer"
    assert attempt.delegation_depth == 1
    assert attempt.trigger == "subagent"
    assert attempt.status == AttemptStatus.PREPARED
    assert child.cwd == str(repo)
    prompt = child.prompt_path.read_text()
    assert "Do not edit files" in prompt
    assert "Focused task: Identify security risks" in prompt
    assert "parent, which alone owns implementation and synthesis" in prompt


def test_direct_child_limit_counts_existing_attempts(tmp_path):
    root, _, ticket_path = _workspace(tmp_path, max_subagents=1)
    parent = _parent(root, ticket_path, tmp_path / "out")
    kwargs = {
        "root": root,
        "ticket_path": ticket_path,
        "parent_attempt_id": str(parent.attempt_id),
        "role": "reviewer",
        "task": "Review the plan.",
        "output_dir": tmp_path / "out",
    }
    prepare_subagent_dispatch(**kwargs)
    with pytest.raises(ValueError, match=r"limit reached \(1\)"):
        prepare_subagent_dispatch(**kwargs)


def test_depth_limit_blocks_grandchild(tmp_path):
    root, _, ticket_path = _workspace(tmp_path, max_depth=1)
    parent = _parent(root, ticket_path, tmp_path / "out")
    child = prepare_subagent_dispatch(
        root=root,
        ticket_path=ticket_path,
        parent_attempt_id=str(parent.attempt_id),
        role="reviewer",
        task="Review the plan.",
        output_dir=tmp_path / "out",
    )
    with pytest.raises(ValueError, match="depth limit"):
        prepare_subagent_dispatch(
            root=root,
            ticket_path=ticket_path,
            parent_attempt_id=str(child.attempt_id),
            role="second reviewer",
            task="Check the first review.",
            output_dir=tmp_path / "out",
        )


def test_ticket_never_blocks_agent_that_can_otherwise_delegate(tmp_path):
    root, _, ticket_path = _workspace(tmp_path, ticket_policy="never")
    parent = _parent(root, ticket_path, tmp_path / "out")
    assert "livery subagent run" not in parent.prompt_path.read_text()
    with pytest.raises(ValueError, match="disabled"):
        prepare_subagent_dispatch(
            root=root,
            ticket_path=ticket_path,
            parent_attempt_id=str(parent.attempt_id),
            role="reviewer",
            task="Review the plan.",
            output_dir=tmp_path / "out",
        )


def test_subagent_run_cli_executes_and_returns_linked_attempt(
    tmp_path, monkeypatch
):
    root, _, ticket_path = _workspace(tmp_path)
    parent = _parent(root, ticket_path, tmp_path / "out")

    def fake_execute(prep, *, workspace_root):
        attempt = load_attempt(prep.attempt_path)
        attempt.status = AttemptStatus.SUCCEEDED
        attempt.exit_code = 0
        attempt.finished_at = "2026-08-18T01:00:00Z"
        attempt.summary_excerpt = [
            "Status: done",
            "Summary: No authorization gaps found.",
        ]
        write_attempt(attempt, workspace_root)
        return SimpleNamespace(
            attempt_path=prep.attempt_path,
            output_path=prep.output_path,
            exit_code=0,
            launched=True,
        )

    monkeypatch.setattr(
        "livery.dispatch_runner.execute_prepared_dispatch", fake_execute
    )
    monkeypatch.chdir(root)
    result = CliRunner().invoke(
        app,
        [
            "subagent", "run", ticket_path.stem,
            "--parent-attempt", str(parent.attempt_id),
            "--role", "security reviewer",
            "--task", "Review authorization boundaries.",
            "--output-dir", str(tmp_path / "out"),
            "--format", "json",
        ],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)["subagent"]
    assert payload["attempt"]["parent_attempt_id"] == parent.attempt_id
    assert payload["attempt"]["status"] == "succeeded"
    assert payload["attempt"]["subagent_role"] == "security reviewer"
