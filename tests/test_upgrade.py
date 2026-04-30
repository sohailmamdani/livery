from __future__ import annotations

from pathlib import Path

import pytest

from livery.cos_engines import MANAGED_BEGIN, MANAGED_END, wrap_managed
from livery.init import COS_MANAGED_BLOCK, NEW_TICKET_SKILL, init_workspace
from livery.upgrade import Action, apply_plan, compute_plan


def _fresh_workspace(tmp_path: Path, cos_engine: str = "both") -> Path:
    target = tmp_path / "ws"
    init_workspace(target=target, name="ws", description="test ws", cos_engine=cos_engine)
    return target


def test_compute_plan_after_fresh_init_is_no_op(tmp_path):
    """Right after `livery init`, upgrade-workspace finds nothing to change."""
    root = _fresh_workspace(tmp_path)
    plan = compute_plan(root)
    assert all(item.action == Action.SKIP for item in plan.items)
    assert not plan.has_changes


def test_compute_plan_creates_missing_convention_file(tmp_path):
    """If a workspace declares cos_engines=[claude_code,codex] but only CLAUDE.md exists, AGENTS.md should be created."""
    root = _fresh_workspace(tmp_path, cos_engine="both")
    (root / "AGENTS.md").unlink()

    plan = compute_plan(root)
    agents_item = next(i for i in plan.items if i.path.name == "AGENTS.md")
    assert agents_item.action == Action.CREATE
    assert MANAGED_BEGIN in agents_item.new_content
    assert "## Custom conventions for the CoS" in agents_item.new_content


def test_compute_plan_refreshes_stale_managed_block(tmp_path):
    """If the LIVERY-MANAGED block content drifted, refresh it; preserve user content outside markers."""
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    claude = root / "CLAUDE.md"

    original = claude.read_text()
    # Stale managed block + user-added section below.
    stale_managed = wrap_managed("# Old framework conventions\n\n- ancient rule\n")
    user_section = "\n# My customizations\n\nSohail-specific stuff here.\n"
    claude.write_text(stale_managed + user_section)

    plan = compute_plan(root)
    item = next(i for i in plan.items if i.path == claude)
    assert item.action == Action.REFRESH
    # User content survives
    assert "Sohail-specific stuff here." in item.new_content
    # Fresh managed content is in there
    assert "Push back hard" in item.new_content
    # Old stale content is gone
    assert "ancient rule" not in item.new_content


def test_compute_plan_inserts_block_into_legacy_file(tmp_path):
    """A pre-existing CLAUDE.md without markers gets a managed block prepended."""
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "livery.toml").write_text(
        'name = "legacy"\ncos_engines = ["claude_code"]\n'
    )
    (root / "CLAUDE.md").write_text("# legacy\n\nUser content with no markers.\n")
    (root / "agents").mkdir()
    (root / "tickets").mkdir()

    plan = compute_plan(root)
    item = next(i for i in plan.items if i.path.name == "CLAUDE.md")
    assert item.action == Action.INSERT
    # Managed block prepended
    assert item.new_content.startswith(MANAGED_BEGIN)
    # Legacy content preserved verbatim below
    assert "User content with no markers." in item.new_content


def test_compute_plan_warns_on_customized_skill(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    skill_path = root / ".claude" / "skills" / "new-ticket" / "SKILL.md"
    skill_path.write_text(NEW_TICKET_SKILL + "\n# user-customized addition\n")

    plan = compute_plan(root)
    item = next(i for i in plan.items if i.path == skill_path)
    assert item.action == Action.WARN
    assert item.new_content is None  # nothing to apply without --force


def test_compute_plan_creates_missing_skill(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    skill_path = root / ".claude" / "skills" / "new-ticket" / "SKILL.md"
    skill_path.unlink()

    plan = compute_plan(root)
    item = next(i for i in plan.items if i.path == skill_path)
    assert item.action == Action.CREATE
    assert item.new_content == NEW_TICKET_SKILL


def test_compute_plan_detects_engines_from_files_when_toml_silent(tmp_path):
    """Legacy livery.toml without cos_engines field → detect from existing files."""
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "livery.toml").write_text('name = "legacy"\n')
    (root / "CLAUDE.md").write_text("# legacy\n")
    (root / "agents").mkdir()
    (root / "tickets").mkdir()

    plan = compute_plan(root)
    assert "claude_code" in plan.cos_engines
    assert "codex" not in plan.cos_engines


def test_compute_plan_supports_pi_workspace(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="pi")
    plan = compute_plan(root)
    assert plan.cos_engines == ["pi"]
    # Pi has no skill or command dirs, so the only items are AGENTS.md.
    assert all(i.path.name == "AGENTS.md" for i in plan.items)


def test_apply_plan_writes_changes_idempotently(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="both")
    (root / "AGENTS.md").unlink()

    plan = compute_plan(root)
    written = apply_plan(plan)
    assert any(item.path.name == "AGENTS.md" for item in written)
    assert (root / "AGENTS.md").exists()

    # Second pass: nothing to do
    plan2 = compute_plan(root)
    assert not plan2.has_changes


def test_apply_plan_skips_warned_items_without_force(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    skill_path = root / ".claude" / "skills" / "new-ticket" / "SKILL.md"
    skill_path.write_text("custom skill content")

    plan = compute_plan(root)
    written = apply_plan(plan, force=False)
    assert all(item.path != skill_path for item in written)
    # File still has custom content
    assert skill_path.read_text() == "custom skill content"


def test_apply_plan_never_touches_user_files(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    # User-owned: livery.toml, agents/, tickets/, CLAUDE.md user section
    livery_toml_before = (root / "livery.toml").read_text()
    (root / "agents" / "writer").mkdir()
    (root / "agents" / "writer" / "agent.md").write_text("agent")
    (root / "tickets" / "ticket.md").write_text("ticket")

    # Add customizations inside CLAUDE.md user section (below the managed block)
    claude = root / "CLAUDE.md"
    content = claude.read_text()
    customized = content + "\n## My custom rule\n\n- never close on Fridays\n"
    claude.write_text(customized)

    plan = compute_plan(root)
    apply_plan(plan)

    # User files untouched
    assert (root / "livery.toml").read_text() == livery_toml_before
    assert (root / "agents" / "writer" / "agent.md").read_text() == "agent"
    assert (root / "tickets" / "ticket.md").read_text() == "ticket"
    # User customization in CLAUDE.md preserved
    assert "never close on Fridays" in claude.read_text()
