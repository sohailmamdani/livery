from __future__ import annotations

from pathlib import Path

import frontmatter
import pytest

from livery.hire import SUPPORTED_RUNTIMES, hire_agent


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "agents").mkdir(parents=True)
    (root / "tickets").mkdir()
    (root / "livery.toml").write_text("name = 'test'\n")
    return root


def test_hire_agent_writes_agent_md_and_agents_md(tmp_path):
    root = _make_root(tmp_path)
    agent_cwd = tmp_path / "somerepo"
    agent_cwd.mkdir()

    created = hire_agent(
        root=root,
        agent_id="qa",
        name="Quality Assurance",
        runtime="codex",
        model="gpt-5-codex",
        cwd=str(agent_cwd),
        reports_to="cos",
        role="Reviews crawl output for schema compliance.",
        hired="2026-04-20",
    )

    assert len(created) == 2
    agent_md = root / "agents" / "qa" / "agent.md"
    agents_md = root / "agents" / "qa" / "AGENTS.md"
    assert agent_md.exists()
    assert agents_md.exists()
    assert created == [agent_md, agents_md]


def test_hire_agent_frontmatter_fields(tmp_path):
    root = _make_root(tmp_path)
    hire_agent(
        root=root,
        agent_id="research",
        name="Research Analyst",
        runtime="claude_code",
        model="claude-sonnet-4-6",
        cwd="/tmp/branddb",
        reports_to="cos",
        role="Drafts brand profiles for BrandDB.",
        hired="2026-04-20",
    )

    post = frontmatter.load(root / "agents" / "research" / "agent.md")
    assert post.get("id") == "research"
    assert post.get("name") == "Research Analyst"
    assert post.get("runtime") == "claude_code"
    assert post.get("model") == "claude-sonnet-4-6"
    assert post.get("cwd") == "/tmp/branddb"
    assert post.get("reports_to") == "cos"
    assert post.get("hired") == "2026-04-20"
    assert "Drafts brand profiles" in post.content


def test_hire_agent_without_model_omits_field(tmp_path):
    root = _make_root(tmp_path)
    hire_agent(
        root=root,
        agent_id="free",
        name="Freeform",
        runtime="cursor",
        model=None,
        cwd="/tmp/x",
        reports_to="cos",
        role="Does things.",
        hired="2026-04-20",
    )
    post = frontmatter.load(root / "agents" / "free" / "agent.md")
    assert "model" not in post.metadata


def test_hire_agent_agents_md_has_section_headers(tmp_path):
    root = _make_root(tmp_path)
    hire_agent(
        root=root,
        agent_id="qa",
        name="Quality Assurance",
        runtime="codex",
        model="gpt-5-codex",
        cwd="/tmp/x",
        reports_to="cos",
        role="Reviews crawl output.",
        hired="2026-04-20",
    )
    content = (root / "agents" / "qa" / "AGENTS.md").read_text()
    assert "# Quality Assurance" in content
    assert "## Role" in content
    assert "## Scope" in content
    assert "## Out of scope" in content
    assert "## Process" in content
    assert "## Quality bar" in content
    assert "## Output format" in content
    assert "Reviews crawl output." in content


def test_hire_agent_rejects_unknown_runtime(tmp_path):
    root = _make_root(tmp_path)
    with pytest.raises(ValueError) as ei:
        hire_agent(
            root=root,
            agent_id="x",
            name="X",
            runtime="not-a-runtime",
            model=None,
            cwd="/tmp/x",
            reports_to="cos",
            role="",
            hired="2026-04-20",
        )
    assert "not-a-runtime" in str(ei.value)


def test_hire_agent_refuses_to_overwrite_without_force(tmp_path):
    root = _make_root(tmp_path)
    hire_agent(
        root=root,
        agent_id="qa",
        name="QA",
        runtime="codex",
        model="gpt-5-codex",
        cwd="/tmp/x",
        reports_to="cos",
        role="Original role.",
        hired="2026-04-20",
    )
    with pytest.raises(FileExistsError):
        hire_agent(
            root=root,
            agent_id="qa",
            name="QA 2",
            runtime="codex",
            model="gpt-5-codex",
            cwd="/tmp/x",
            reports_to="cos",
            role="New role.",
            hired="2026-04-20",
        )


def test_hire_agent_force_overwrites(tmp_path):
    root = _make_root(tmp_path)
    hire_agent(
        root=root,
        agent_id="qa",
        name="QA",
        runtime="codex",
        model="gpt-5-codex",
        cwd="/tmp/x",
        reports_to="cos",
        role="Original role.",
        hired="2026-04-20",
    )
    hire_agent(
        root=root,
        agent_id="qa",
        name="QA Updated",
        runtime="claude_code",
        model="claude-sonnet-4-6",
        cwd="/tmp/y",
        reports_to="cos",
        role="New role.",
        hired="2026-04-21",
        overwrite=True,
    )
    post = frontmatter.load(root / "agents" / "qa" / "agent.md")
    assert post.get("name") == "QA Updated"
    assert post.get("runtime") == "claude_code"
    assert "New role." in post.content


def test_supported_runtimes_includes_all_expected():
    for r in ("codex", "claude_code", "cursor", "lm_studio", "ollama"):
        assert r in SUPPORTED_RUNTIMES
