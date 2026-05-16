from __future__ import annotations

from pathlib import Path

import frontmatter
import pytest

from livery.dispatch import (
    build_runtime_command,
    compose_prompt,
    ensure_worktree,
    prepare_dispatch,
    prepare_fan_out,
)


def _make_livery_root(tmp_path: Path, agent_cwd: Path) -> Path:
    """Build a minimal Livery repo layout: agents/lead-dev + tickets/."""
    root = tmp_path / "livery"
    (root / "agents" / "lead-dev").mkdir(parents=True)
    (root / "tickets").mkdir()
    # pyproject.toml + livery/ so find_root would accept it, though not tested directly here
    (root / "pyproject.toml").write_text("[project]\nname = 'test-livery'\n")
    (root / "livery").mkdir()
    (root / "livery" / "__init__.py").write_text("")

    agent_md = frontmatter.Post(
        "Lead developer for test.",
        id="lead-dev",
        name="Lead Developer",
        runtime="codex",
        model="gpt-5.4",
        cwd=str(agent_cwd),
        title="Lead Developer",
        reports_to="cos",
        hired="2026-04-18",
    )
    (root / "agents" / "lead-dev" / "agent.md").write_text(
        frontmatter.dumps(agent_md) + "\n"
    )
    (root / "agents" / "lead-dev" / "AGENTS.md").write_text(
        "# Lead Developer\n\nYou are the lead dev.\n"
    )
    return root


def test_compose_prompt_includes_preamble_agents_and_ticket():
    out = compose_prompt(
        assignee="lead-dev",
        agents_md="# AGENTS\nhello\n",
        ticket_md="# Ticket\nwork to do\n",
        ticket_id="2026-04-18-001-example",
    )
    assert "acting as the \"lead-dev\" agent" in out
    assert "---BEGIN AGENTS.md---" in out
    assert "---END AGENTS.md---" in out
    assert "## Livery discovery" in out
    assert "livery next --format json" in out
    assert "livery capabilities --format json" in out
    assert "---BEGIN TICKET---" in out
    assert "---END TICKET---" in out
    assert "=== DISPATCH_SUMMARY ===" in out
    assert "2026-04-18-001-example" in out
    assert "Proceed." in out


def test_build_runtime_command_codex_with_model(tmp_path):
    prompt = tmp_path / "p.txt"
    output = tmp_path / "o.out"
    cmd = build_runtime_command(
        runtime="codex",
        model="gpt-5.4",
        cwd="/Users/sohail/code/branddb",
        prompt_path=prompt,
        output_path=output,
    )
    assert cmd.startswith("codex exec")
    assert "--model gpt-5.4" in cmd
    assert "--cd /Users/sohail/code/branddb" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--skip-git-repo-check" in cmd
    assert f"< {prompt}" in cmd
    assert f"> {output}" in cmd


def test_build_runtime_command_codex_without_model(tmp_path):
    cmd = build_runtime_command(
        runtime="codex",
        model=None,
        cwd="/tmp/x",
        prompt_path=tmp_path / "p.txt",
        output_path=tmp_path / "o.out",
    )
    assert "--model" not in cmd


def test_build_runtime_command_claude_code(tmp_path):
    prompt = tmp_path / "p.txt"
    output = tmp_path / "o.out"
    cmd = build_runtime_command(
        runtime="claude_code",
        model="claude-sonnet-4-6",
        cwd="/Users/sohail/code/brand",
        prompt_path=prompt,
        output_path=output,
    )
    assert "cd /Users/sohail/code/brand" in cmd
    assert "claude -p" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--model claude-sonnet-4-6" in cmd
    assert f"< {prompt}" in cmd
    assert f"> {output}" in cmd


def test_build_runtime_command_cursor(tmp_path):
    prompt = tmp_path / "p.txt"
    output = tmp_path / "o.out"
    cmd = build_runtime_command(
        runtime="cursor",
        model="gpt-5",
        cwd="/Users/sohail/code/brand",
        prompt_path=prompt,
        output_path=output,
    )
    assert "cd /Users/sohail/code/brand" in cmd
    assert "cursor-agent --print --force" in cmd
    assert "--model gpt-5" in cmd
    assert f"< {prompt}" in cmd
    assert f"> {output}" in cmd


def test_build_runtime_command_unknown_runtime_raises(tmp_path):
    with pytest.raises(NotImplementedError) as ei:
        build_runtime_command(
            runtime="pi",
            model="anything",
            cwd="/tmp/x",
            prompt_path=tmp_path / "p.txt",
            output_path=tmp_path / "o.out",
        )
    assert "'pi'" in str(ei.value)


def test_build_runtime_command_lm_studio(tmp_path):
    prompt = tmp_path / "p.txt"
    output = tmp_path / "o.out"
    cmd = build_runtime_command(
        runtime="lm_studio",
        model="gemma-4-26B-A4B-it-MLX-8bit",
        cwd="/Users/sohail/code/brand",
        prompt_path=prompt,
        output_path=output,
    )
    # lm_studio adapter uses `uv run --directory <livery root>` so python
    # resolves the import regardless of the agent's declared cwd.
    assert "uv run --directory" in cmd
    assert "python -m livery.runtimes.lm_studio" in cmd
    assert "--model gemma-4-26B-A4B-it-MLX-8bit" in cmd
    assert f"< {prompt}" in cmd
    assert f"> {output}" in cmd


def test_build_runtime_command_lm_studio_without_model_raises(tmp_path):
    with pytest.raises(ValueError) as ei:
        build_runtime_command(
            runtime="lm_studio",
            model=None,
            cwd="/tmp/x",
            prompt_path=tmp_path / "p.txt",
            output_path=tmp_path / "o.out",
        )
    assert "requires an explicit model" in str(ei.value)


def test_prepare_dispatch_end_to_end(tmp_path):
    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)

    ticket_id = "2026-04-19-099-example-ticket"
    ticket_post = frontmatter.Post(
        "## Description\n\nBuild X.\n\n## Thread\n\n### 2026-04-19T00:00:00Z — user\nBuild X.\n",
        id=ticket_id,
        title="Example ticket",
        assignee="lead-dev",
        status="open",
        created="2026-04-19T00:00:00Z",
        updated="2026-04-19T00:00:00Z",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    out_dir = tmp_path / "dispatch-out"
    prep = prepare_dispatch(
        root=root,
        ticket_path=ticket_path,
        output_dir=out_dir,
        make_worktree=False,
    )

    assert prep.ticket_id == ticket_id
    assert prep.assignee == "lead-dev"
    assert prep.runtime == "codex"
    assert prep.model == "gpt-5.4"
    assert prep.cwd == str(agent_cwd)
    assert prep.prompt_path.exists()
    prompt_text = prep.prompt_path.read_text()
    assert "acting as the \"lead-dev\"" in prompt_text
    assert "Build X." in prompt_text
    assert ticket_id in prompt_text


def test_prepare_dispatch_rejects_cos_assignee(tmp_path):
    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)

    ticket_post = frontmatter.Post(
        "## Description\n\nCos-only.\n",
        id="2026-04-19-098-cos-only",
        title="Cos only",
        assignee="cos",
        status="open",
        created="x",
        updated="x",
    )
    ticket_path = root / "tickets" / "2026-04-19-098-cos-only.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    with pytest.raises(ValueError) as ei:
        prepare_dispatch(
            root=root,
            ticket_path=ticket_path,
            output_dir=tmp_path / "out",
            make_worktree=False,
        )
    assert "no agent assignee" in str(ei.value)


def _add_second_agent(root: Path, agent_cwd: Path, agent_id: str, runtime: str = "codex") -> None:
    """Add a second agent next to lead-dev so fan-out tests have multiple targets."""
    (root / "agents" / agent_id).mkdir(parents=True)
    agent_md = frontmatter.Post(
        f"{agent_id} agent for test.",
        id=agent_id,
        name=agent_id,
        runtime=runtime,
        model="gpt-5.4",
        cwd=str(agent_cwd),
        reports_to="cos",
        hired="2026-04-21",
    )
    (root / "agents" / agent_id / "agent.md").write_text(
        frontmatter.dumps(agent_md) + "\n"
    )
    (root / "agents" / agent_id / "AGENTS.md").write_text(f"# {agent_id}\n")


def test_prepare_dispatch_uses_assignee_override(tmp_path):
    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)
    _add_second_agent(root, agent_cwd, "qa")

    ticket_id = "2026-04-21-001-example"
    ticket_post = frontmatter.Post(
        "## Description\n\nReview X.\n",
        id=ticket_id,
        title="Review",
        assignee="lead-dev",
        status="open",
        created="x",
        updated="x",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    prep = prepare_dispatch(
        root=root,
        ticket_path=ticket_path,
        output_dir=tmp_path / "out",
        make_worktree=False,
        assignee_override="qa",
    )
    assert prep.assignee == "qa"
    # Prompt content reflects the override, not the original ticket assignee.
    assert 'acting as the "qa" agent' in prep.prompt_path.read_text()


def test_prepare_dispatch_override_works_when_ticket_has_no_assignee(tmp_path):
    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)

    ticket_post = frontmatter.Post(
        "## Description\n\nFan-out only.\n",
        id="2026-04-21-002-fanout",
        title="Fanout",
        assignee=None,
        status="open",
        created="x",
        updated="x",
    )
    ticket_path = root / "tickets" / "2026-04-21-002-fanout.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    prep = prepare_dispatch(
        root=root,
        ticket_path=ticket_path,
        output_dir=tmp_path / "out",
        make_worktree=False,
        assignee_override="lead-dev",
    )
    assert prep.assignee == "lead-dev"


def test_prepare_dispatch_filename_includes_assignee(tmp_path):
    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)

    ticket_id = "2026-04-21-003-x"
    ticket_post = frontmatter.Post(
        "## Description\n\nX\n",
        id=ticket_id,
        title="X",
        assignee="lead-dev",
        status="open",
        created="x",
        updated="x",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    prep = prepare_dispatch(
        root=root,
        ticket_path=ticket_path,
        output_dir=tmp_path / "out",
        make_worktree=False,
    )
    assert prep.prompt_path.name == f"livery-dispatch-{ticket_id}-lead-dev.txt"
    assert prep.output_path.name == f"livery-dispatch-{ticket_id}-lead-dev.out"


def test_ensure_worktree_includes_agent_id(tmp_path, monkeypatch):
    """When agent_id is passed, worktree path and branch both include it."""
    repo = tmp_path / "branddb"
    repo.mkdir()
    # Stub out the actual `git worktree add` call so the test doesn't need a real repo.
    calls: list[list] = []
    monkeypatch.setattr(
        "livery.dispatch.subprocess.run",
        lambda cmd, check=True: calls.append(cmd),
    )

    path1, branch1 = ensure_worktree(repo=repo, ticket_id="2026-04-21-001-x", agent_id="research")
    path2, branch2 = ensure_worktree(repo=repo, ticket_id="2026-04-21-001-x", agent_id="research-gpt")

    assert path1 != path2
    assert branch1 != branch2
    assert "research" in path1.name and "research-gpt" in path2.name
    assert branch1 == "ticket-2026-04-21-001-x-research"
    assert branch2 == "ticket-2026-04-21-001-x-research-gpt"


def test_prepare_fan_out_produces_separate_preps(tmp_path):
    agent_cwd_a = tmp_path / "repo-a"
    agent_cwd_a.mkdir()
    agent_cwd_b = tmp_path / "repo-b"
    agent_cwd_b.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd_a)
    _add_second_agent(root, agent_cwd_b, "qa", runtime="claude_code")

    ticket_id = "2026-04-21-010-fanout"
    ticket_post = frontmatter.Post(
        "## Description\n\nFan X out.\n",
        id=ticket_id,
        title="Fanout",
        assignee="lead-dev",  # will be overridden per-prep
        status="open",
        created="x",
        updated="x",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    preps = prepare_fan_out(
        root=root,
        ticket_path=ticket_path,
        output_dir=tmp_path / "out",
        make_worktree=False,
        assignees=["lead-dev", "qa"],
    )
    assert [p.assignee for p in preps] == ["lead-dev", "qa"]
    # Distinct prompt/output files per agent
    assert preps[0].prompt_path != preps[1].prompt_path
    assert preps[0].output_path != preps[1].output_path
    assert all(p.prompt_path.exists() for p in preps)
    assert preps[0].runtime == "codex"
    assert preps[1].runtime == "claude_code"


def test_prepare_fan_out_empty_list_raises(tmp_path):
    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)

    ticket_post = frontmatter.Post(
        "## X\n",
        id="2026-04-21-011-x",
        title="X",
        assignee="lead-dev",
        status="open",
        created="x",
        updated="x",
    )
    ticket_path = root / "tickets" / "2026-04-21-011-x.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    with pytest.raises(ValueError) as ei:
        prepare_fan_out(
            root=root,
            ticket_path=ticket_path,
            output_dir=tmp_path / "out",
            make_worktree=False,
            assignees=[],
        )
    assert "at least one" in str(ei.value)


def test_prepare_fan_out_rejects_duplicates(tmp_path):
    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)

    ticket_post = frontmatter.Post(
        "## X\n",
        id="2026-04-21-012-x",
        title="X",
        assignee="lead-dev",
        status="open",
        created="x",
        updated="x",
    )
    ticket_path = root / "tickets" / "2026-04-21-012-x.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    with pytest.raises(ValueError) as ei:
        prepare_fan_out(
            root=root,
            ticket_path=ticket_path,
            output_dir=tmp_path / "out",
            make_worktree=False,
            assignees=["lead-dev", "lead-dev"],
        )
    assert "duplicate" in str(ei.value).lower()


def test_prepare_dispatch_rejects_unknown_agent(tmp_path):
    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)

    ticket_post = frontmatter.Post(
        "## Description\n\nX.\n",
        id="2026-04-19-097-unknown",
        title="Unknown agent",
        assignee="ghost-agent",
        status="open",
        created="x",
        updated="x",
    )
    ticket_path = root / "tickets" / "2026-04-19-097-unknown.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    with pytest.raises(ValueError) as ei:
        prepare_dispatch(
            root=root,
            ticket_path=ticket_path,
            output_dir=tmp_path / "out",
            make_worktree=False,
        )
    assert "ghost-agent" in str(ei.value)


# -----------------------------------------------------------------------------
# Hook integration with prepare_dispatch
# -----------------------------------------------------------------------------


def test_after_worktree_create_hook_fires_when_configured(tmp_path, monkeypatch):
    """When [dispatch_hooks].after_worktree_create is configured AND a
    worktree is made, the hook runs and its outcome is recorded in the
    attempt JSON."""
    from livery.attempts import attempts_dir, list_attempts, AttemptStatus
    from livery.dispatch import ensure_worktree as real_ensure_worktree
    import livery.dispatch as dispatch_mod

    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)

    # Workspace marker so config.load() picks up the hook table.
    (root / "livery.toml").write_text(
        '[dispatch_hooks]\n'
        'after_worktree_create = "echo HOOK_FIRED_FOR=$LIVERY_ASSIGNEE"\n'
    )

    # Stub ensure_worktree — the real one needs a git repo. Return a path
    # that exists so the rest of prepare_dispatch is happy.
    fake_wt = tmp_path / "branddb-tx"
    fake_wt.mkdir()
    monkeypatch.setattr(
        dispatch_mod,
        "ensure_worktree",
        lambda *, repo, ticket_id, agent_id=None: (fake_wt, "ticket-x"),
    )

    ticket_id = "2026-05-07-099-hook-test"
    ticket_post = frontmatter.Post(
        "## Description\n\nHook test.\n",
        id=ticket_id,
        title="Hook test",
        assignee="lead-dev",
        status="open",
        created="2026-05-07T00:00:00Z",
        updated="2026-05-07T00:00:00Z",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    prep = prepare_dispatch(
        root=root,
        ticket_path=ticket_path,
        output_dir=tmp_path / "dispatch-out",
        make_worktree=True,
    )

    attempts = list_attempts(root)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert "after_worktree_create" in attempt.hooks
    assert attempt.hooks["after_worktree_create"].exit_code == 0
    assert attempt.status == AttemptStatus.PREPARED  # success → unchanged
    log = Path(attempt.hooks["after_worktree_create"].log_path).read_text()
    assert "HOOK_FIRED_FOR=lead-dev" in log


def test_after_worktree_create_hook_failure_aborts_prepare(tmp_path, monkeypatch):
    """A non-zero exit from the after_worktree_create hook marks the
    attempt FAILED with hook_error AND raises so callers don't proceed."""
    from livery.attempts import attempts_dir, list_attempts, AttemptStatus, FailureClass
    import livery.dispatch as dispatch_mod

    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)

    (root / "livery.toml").write_text(
        '[dispatch_hooks]\n'
        'after_worktree_create = "exit 9"\n'
    )

    fake_wt = tmp_path / "branddb-tx"
    fake_wt.mkdir()
    monkeypatch.setattr(
        dispatch_mod,
        "ensure_worktree",
        lambda *, repo, ticket_id, agent_id=None: (fake_wt, "ticket-x"),
    )

    ticket_id = "2026-05-07-100-hook-fail"
    ticket_post = frontmatter.Post(
        "## Description\n\nHook fail test.\n",
        id=ticket_id,
        title="Hook fail",
        assignee="lead-dev",
        status="open",
        created="2026-05-07T00:00:00Z",
        updated="2026-05-07T00:00:00Z",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    with pytest.raises(RuntimeError) as ei:
        prepare_dispatch(
            root=root,
            ticket_path=ticket_path,
            output_dir=tmp_path / "dispatch-out",
            make_worktree=True,
        )
    assert "after_worktree_create" in str(ei.value)

    # And the attempt was persisted with failure metadata
    attempts = list_attempts(root)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.status == AttemptStatus.FAILED
    assert attempt.failure_class == FailureClass.HOOK_ERROR
    assert "after_worktree_create" in (attempt.failure_detail or "")


def test_after_worktree_create_hook_skipped_without_worktree(tmp_path):
    """No worktree → no after_worktree_create hook firing, even if configured."""
    from livery.attempts import list_attempts

    agent_cwd = tmp_path / "branddb"
    agent_cwd.mkdir()
    root = _make_livery_root(tmp_path, agent_cwd)

    (root / "livery.toml").write_text(
        '[dispatch_hooks]\n'
        'after_worktree_create = "exit 1"\n'  # would fail if it ran
    )

    ticket_id = "2026-05-07-101-no-wt"
    ticket_post = frontmatter.Post(
        "## Description\n\nNo worktree.\n",
        id=ticket_id,
        title="No wt",
        assignee="lead-dev",
        status="open",
        created="2026-05-07T00:00:00Z",
        updated="2026-05-07T00:00:00Z",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    # Should NOT raise — hook isn't fired without a worktree
    prepare_dispatch(
        root=root,
        ticket_path=ticket_path,
        output_dir=tmp_path / "dispatch-out",
        make_worktree=False,
    )

    attempts = list_attempts(root)
    assert "after_worktree_create" not in attempts[0].hooks
