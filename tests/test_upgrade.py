from __future__ import annotations

from pathlib import Path
from shutil import rmtree

import pytest
from typer.testing import CliRunner

from livery.cli import app
from livery.cos_engines import MANAGED_BEGIN, MANAGED_END, wrap_managed
from livery.harness_assets import render_command_skill, render_command_slash
from livery.init import (
    COS_MANAGED_BLOCK,
    HELLO_SKILL,
    HELLO_SLASH,
    LIST_AGENTS_SKILL,
    LIST_AGENTS_SLASH,
    NEW_TICKET_SKILL,
    TALK_SKILL,
    TALK_SLASH,
    WALKIE_SKILL,
    WALKIE_SLASH,
    init_workspace,
)
from livery.upgrade import Action, apply_plan, compute_plan, compute_sync_plan


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
    # When a sibling exists, the new file mirrors it instead of using the bare template
    assert "mirroring from CLAUDE.md" in agents_item.reason


def test_create_mirrors_user_content_from_sibling(tmp_path):
    """User customizations in CLAUDE.md should appear in a newly-created AGENTS.md."""
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    # Add user content to CLAUDE.md, then opt into Codex
    claude = root / "CLAUDE.md"
    user_section = "\n## My BrandDB conventions\n\n- Cite or don't state.\n- No speculation about people.\n"
    claude.write_text(claude.read_text() + user_section)

    # Switch the workspace to claude_code + codex
    toml_text = (root / "livery.toml").read_text().replace(
        '"claude_code"', '"claude_code", "codex"',
    )
    (root / "livery.toml").write_text(toml_text)

    plan = compute_plan(root)
    agents_item = next(i for i in plan.items if i.path.name == "AGENTS.md")
    assert agents_item.action == Action.CREATE
    # The user's BrandDB section should be in the new AGENTS.md
    assert "Cite or don't state." in agents_item.new_content
    assert "No speculation about people." in agents_item.new_content
    # And the framework block is fresh, not stale
    assert MANAGED_BEGIN in agents_item.new_content


def test_create_uses_template_when_no_sibling_exists(tmp_path):
    """Brand-new workspace with neither convention file → fresh template, not a mirror."""
    root = tmp_path / "fresh"
    root.mkdir()
    (root / "livery.toml").write_text(
        'name = "fresh"\ncos_engines = ["claude_code", "codex"]\n'
    )
    (root / "agents").mkdir()
    (root / "tickets").mkdir()
    # Note: no CLAUDE.md, no AGENTS.md

    plan = compute_plan(root)
    claude_item = next(i for i in plan.items if i.path.name == "CLAUDE.md")
    assert claude_item.action == Action.CREATE
    assert "Custom conventions for the CoS" in claude_item.new_content
    assert "mirroring from" not in claude_item.reason


def test_create_mirrors_legacy_sibling_with_no_managed_block(tmp_path):
    """If the sibling is a pre-markers legacy file, the new file gets a managed block prepended + the legacy content."""
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "livery.toml").write_text(
        'name = "legacy"\ncos_engines = ["claude_code", "codex"]\n'
    )
    (root / "agents").mkdir()
    (root / "tickets").mkdir()
    # Legacy CLAUDE.md with no markers but real user content
    (root / "CLAUDE.md").write_text("# legacy\n\nLots of Sohail-specific stuff here.\n")

    plan = compute_plan(root)
    agents_item = next(i for i in plan.items if i.path.name == "AGENTS.md")
    assert agents_item.action == Action.CREATE
    # Managed block prepended
    assert agents_item.new_content.startswith(MANAGED_BEGIN)
    # Legacy user content preserved
    assert "Lots of Sohail-specific stuff here." in agents_item.new_content


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
    assert "The pushback rule" in item.new_content
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
    skill_path = root / ".claude" / "skills" / "livery-new-ticket" / "SKILL.md"
    skill_path.write_text(NEW_TICKET_SKILL + "\n# user-customized addition\n")

    plan = compute_plan(root)
    item = next(i for i in plan.items if i.path == skill_path)
    assert item.action == Action.WARN
    assert item.new_content == NEW_TICKET_SKILL  # available when applying with --force


def test_compute_plan_creates_missing_skill(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    skill_path = root / ".claude" / "skills" / "livery-new-ticket" / "SKILL.md"
    skill_path.unlink()

    plan = compute_plan(root)
    item = next(i for i in plan.items if i.path == skill_path)
    assert item.action == Action.CREATE
    assert item.new_content == NEW_TICKET_SKILL


def test_compute_plan_creates_missing_hello_assets(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="both")
    command_path = root / ".claude" / "commands" / "livery" / "hello.md"
    claude_skill_path = root / ".claude" / "skills" / "livery-hello" / "SKILL.md"
    codex_skill_path = root / ".agents" / "skills" / "livery-hello" / "SKILL.md"
    command_path.unlink()
    claude_skill_path.unlink()
    codex_skill_path.unlink()

    plan = compute_plan(root)
    command_item = next(i for i in plan.items if i.path == command_path)
    claude_skill_item = next(i for i in plan.items if i.path == claude_skill_path)
    codex_skill_item = next(i for i in plan.items if i.path == codex_skill_path)

    assert command_item.action == Action.CREATE
    assert command_item.new_content == HELLO_SLASH
    assert claude_skill_item.action == Action.CREATE
    assert claude_skill_item.new_content == HELLO_SKILL
    assert codex_skill_item.action == Action.CREATE
    assert codex_skill_item.new_content == HELLO_SKILL


def test_compute_plan_creates_missing_list_agents_assets(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="both")
    command_path = root / ".claude" / "commands" / "livery" / "agents.md"
    claude_skill_path = root / ".claude" / "skills" / "livery-list-agents" / "SKILL.md"
    codex_skill_path = root / ".agents" / "skills" / "livery-list-agents" / "SKILL.md"
    command_path.unlink()
    claude_skill_path.unlink()
    codex_skill_path.unlink()

    plan = compute_plan(root)
    command_item = next(i for i in plan.items if i.path == command_path)
    claude_skill_item = next(i for i in plan.items if i.path == claude_skill_path)
    codex_skill_item = next(i for i in plan.items if i.path == codex_skill_path)

    assert command_item.action == Action.CREATE
    assert command_item.new_content == LIST_AGENTS_SLASH
    assert claude_skill_item.action == Action.CREATE
    assert claude_skill_item.new_content == LIST_AGENTS_SKILL
    assert codex_skill_item.action == Action.CREATE
    assert codex_skill_item.new_content == LIST_AGENTS_SKILL


def test_compute_plan_creates_missing_command_shaped_assets(tmp_path):
    from livery.harness_assets import COMMAND_HARNESS_ASSETS

    root = _fresh_workspace(tmp_path, cos_engine="both")
    asset = next(a for a in COMMAND_HARNESS_ASSETS if a.skill_name == "livery-ticket-list")
    command_path = root / ".claude" / "commands" / "livery" / asset.slash_file
    claude_skill_path = root / ".claude" / "skills" / asset.skill_name / "SKILL.md"
    codex_skill_path = root / ".agents" / "skills" / asset.skill_name / "SKILL.md"
    command_path.unlink()
    claude_skill_path.unlink()
    codex_skill_path.unlink()

    plan = compute_plan(root)
    command_item = next(i for i in plan.items if i.path == command_path)
    claude_skill_item = next(i for i in plan.items if i.path == claude_skill_path)
    codex_skill_item = next(i for i in plan.items if i.path == codex_skill_path)

    assert command_item.action == Action.CREATE
    assert command_item.new_content == render_command_slash(asset)
    assert claude_skill_item.action == Action.CREATE
    assert claude_skill_item.new_content == render_command_skill(asset)
    assert codex_skill_item.action == Action.CREATE
    assert codex_skill_item.new_content == render_command_skill(asset)


def test_compute_plan_creates_missing_talk_assets(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="both")
    command_path = root / ".claude" / "commands" / "livery" / "talk.md"
    claude_skill_path = root / ".claude" / "skills" / "livery-talk-agent" / "SKILL.md"
    codex_skill_path = root / ".agents" / "skills" / "livery-talk-agent" / "SKILL.md"
    command_path.unlink()
    claude_skill_path.unlink()
    codex_skill_path.unlink()

    plan = compute_plan(root)
    command_item = next(i for i in plan.items if i.path == command_path)
    claude_skill_item = next(i for i in plan.items if i.path == claude_skill_path)
    codex_skill_item = next(i for i in plan.items if i.path == codex_skill_path)

    assert command_item.action == Action.CREATE
    assert command_item.new_content == TALK_SLASH
    assert claude_skill_item.action == Action.CREATE
    assert claude_skill_item.new_content == TALK_SKILL
    assert codex_skill_item.action == Action.CREATE
    assert codex_skill_item.new_content == TALK_SKILL


def test_compute_plan_creates_missing_walkie_assets(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    command_path = root / ".claude" / "commands" / "livery" / "walkie.md"
    skill_path = root / ".claude" / "skills" / "livery-walkie-talkie" / "SKILL.md"
    command_path.unlink()
    skill_path.unlink()

    plan = compute_plan(root)
    command_item = next(i for i in plan.items if i.path == command_path)
    skill_item = next(i for i in plan.items if i.path == skill_path)

    assert command_item.action == Action.CREATE
    assert command_item.new_content == WALKIE_SLASH
    assert skill_item.action == Action.CREATE
    assert skill_item.new_content == WALKIE_SKILL


def test_compute_plan_creates_missing_memory_scaffold(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    rmtree(root / "memory")

    plan = compute_plan(root)
    created = {i.path.relative_to(root) for i in plan.items if i.action == Action.CREATE}

    assert Path("memory/decisions/.gitkeep") in created
    assert Path("memory/lessons/.gitkeep") in created
    assert Path("memory/preferences/.gitkeep") in created


def test_apply_plan_backfills_memory_scaffold_idempotently(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    rmtree(root / "memory")

    apply_plan(compute_plan(root))

    assert (root / "memory" / "decisions" / ".gitkeep").exists()
    assert (root / "memory" / "lessons" / ".gitkeep").exists()
    assert (root / "memory" / "preferences" / ".gitkeep").exists()
    assert all(i.action != Action.CREATE for i in compute_plan(root).items)


def test_upgrade_workspace_dry_run_reports_missing_memory_scaffold(tmp_path, monkeypatch):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    rmtree(root / "memory")
    monkeypatch.chdir(root)

    result = CliRunner().invoke(app, ["upgrade-workspace"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "[create] memory/decisions/.gitkeep" in result.stdout
    assert "dry-run; pass --apply" in result.stdout


def test_compute_plan_fails_when_memory_is_file(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "livery.toml").write_text('name = "ws"\ncos_engines = ["pi"]\n')
    (root / "AGENTS.md").write_text("# ws\n")
    (root / "agents").mkdir()
    (root / "tickets").mkdir()
    (root / "memory").write_text("not a directory")

    with pytest.raises(RuntimeError) as ei:
        compute_plan(root)

    assert "exists but is not a directory" in str(ei.value)


def test_compute_plan_legacy_workspace_migrates_to_all_engines(tmp_path):
    """Legacy livery.toml without cos_engines → upgrade-workspace migrates to ALL supported engines."""
    from livery.upgrade import ALL_SUPPORTED_ENGINES, Action

    root = tmp_path / "legacy"
    root.mkdir()
    (root / "livery.toml").write_text('name = "legacy"\n')
    (root / "CLAUDE.md").write_text("# legacy\n")
    (root / "agents").mkdir()
    (root / "tickets").mkdir()

    plan = compute_plan(root)
    # All engines are now in the plan, not just the detected one
    assert plan.cos_engines == ALL_SUPPORTED_ENGINES
    # And there's a MIGRATE item for livery.toml
    migrate_items = [i for i in plan.items if i.action == Action.MIGRATE]
    assert len(migrate_items) == 1
    assert migrate_items[0].path == root / "livery.toml"
    # Migration content includes a cos_engines line with all engines
    assert "cos_engines = [" in migrate_items[0].new_content
    for engine in ALL_SUPPORTED_ENGINES:
        assert f'"{engine}"' in migrate_items[0].new_content


def test_legacy_migration_inserts_before_section_header(tmp_path):
    """TOML quirk: top-level keys can't follow a [section] header. Verify insertion order."""
    import tomllib

    from livery.upgrade import Action, apply_plan

    root = tmp_path / "legacy"
    root.mkdir()
    # livery-im-style: top-level fields, then a [telegram] table
    (root / "livery.toml").write_text(
        'name = "legacy"\n'
        'description = "x"\n'
        '\n'
        '[telegram]\n'
        'chat_id = "-100123"\n'
    )
    (root / "agents").mkdir()
    (root / "tickets").mkdir()

    plan = compute_plan(root)
    apply_plan(plan)

    # After apply, livery.toml must still be valid TOML AND have cos_engines
    # at top-level (not nested inside [telegram]).
    parsed = tomllib.loads((root / "livery.toml").read_text())
    assert "cos_engines" in parsed
    assert isinstance(parsed["cos_engines"], list)
    # And [telegram] still parses correctly
    assert parsed["telegram"]["chat_id"] == "-100123"


def test_legacy_migration_idempotent(tmp_path):
    """Once migrated, subsequent runs see the cos_engines field and skip the migrate step."""
    from livery.upgrade import Action, apply_plan

    root = tmp_path / "legacy"
    root.mkdir()
    (root / "livery.toml").write_text('name = "legacy"\n')
    (root / "agents").mkdir()
    (root / "tickets").mkdir()

    apply_plan(compute_plan(root))
    second_plan = compute_plan(root)
    migrate_items = [i for i in second_plan.items if i.action == Action.MIGRATE]
    assert migrate_items == []  # no migration needed second time around


def test_legacy_migration_preserves_existing_content(tmp_path):
    """Migration is purely additive — existing fields, comments, and sections stay verbatim."""
    from livery.upgrade import apply_plan

    root = tmp_path / "legacy"
    root.mkdir()
    original_body = (
        "# A comment.\n"
        'name = "legacy"\n'
        'description = "I should survive"\n'
        '\n'
        '[telegram]\n'
        '# inline comment\n'
        'chat_id = "-100123"\n'
    )
    (root / "livery.toml").write_text(original_body)
    (root / "agents").mkdir()
    (root / "tickets").mkdir()

    apply_plan(compute_plan(root))
    new_body = (root / "livery.toml").read_text()

    # Every line of the original is still present
    for line in original_body.splitlines():
        assert line in new_body


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
    skill_path = root / ".claude" / "skills" / "livery-new-ticket" / "SKILL.md"
    skill_path.write_text("custom skill content")

    plan = compute_plan(root)
    written = apply_plan(plan, force=False)
    assert all(item.path != skill_path for item in written)
    # File still has custom content
    assert skill_path.read_text() == "custom skill content"


def test_apply_plan_overwrites_warned_items_with_force(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    skill_path = root / ".claude" / "skills" / "livery-new-ticket" / "SKILL.md"
    skill_path.write_text("custom skill content")

    plan = compute_plan(root)
    written = apply_plan(plan, force=True)

    assert any(item.path == skill_path for item in written)
    assert skill_path.read_text() == NEW_TICKET_SKILL


def test_upgrade_workspace_force_reports_and_overwrites_customized_skill(tmp_path, monkeypatch):
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    skill_path = root / ".claude" / "skills" / "livery-new-ticket" / "SKILL.md"
    skill_path.write_text("custom skill content")
    monkeypatch.chdir(root)

    result = CliRunner().invoke(app, ["upgrade-workspace", "--apply", "--force"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "[force ] .claude/skills/livery-new-ticket/SKILL.md" in result.stdout
    assert "will overwrite because --force was passed" in result.stdout
    assert "use --force to overwrite" not in result.stdout
    assert "Applied 1 change(s)." in result.stdout
    assert skill_path.read_text() == NEW_TICKET_SKILL


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


# -----------------------------------------------------------------------------
# `livery sync-cos` — mirror user content from one convention file to siblings
# -----------------------------------------------------------------------------


def test_sync_no_op_when_only_one_convention_file(tmp_path):
    """sync-cos against a single-engine workspace has nothing to sync."""
    root = _fresh_workspace(tmp_path, cos_engine="claude_code")
    plan = compute_sync_plan(root)
    assert plan.items == []


def test_sync_no_op_when_already_in_sync(tmp_path):
    """Right after init, both convention files are identical → nothing to do."""
    root = _fresh_workspace(tmp_path, cos_engine="both")
    plan = compute_sync_plan(root)
    assert all(i.action == Action.SKIP for i in plan.items)
    assert not plan.has_changes


def test_sync_propagates_user_edit_from_richer_file(tmp_path):
    """User edits CLAUDE.md → sync mirrors that into AGENTS.md."""
    root = _fresh_workspace(tmp_path, cos_engine="both")
    claude = root / "CLAUDE.md"
    agents = root / "AGENTS.md"

    # Add real user content to CLAUDE.md → it becomes the richer file.
    claude.write_text(claude.read_text() + "\n## My new conventions\n\n- always do X\n")

    plan = compute_sync_plan(root)
    sync_item = next(i for i in plan.items if i.path == agents)
    assert sync_item.action == Action.REFRESH
    assert "always do X" in sync_item.new_content
    assert "from CLAUDE.md" in sync_item.reason


def test_sync_explicit_source_overrides_default(tmp_path):
    """--from picks the named file even if it's not the auto-default."""
    root = _fresh_workspace(tmp_path, cos_engine="both")
    claude = root / "CLAUDE.md"
    agents = root / "AGENTS.md"

    # Make AGENTS.md the divergent one. Without --from, CLAUDE.md would still
    # win on content-size (they're roughly equal — tie-breaker mtime would
    # go to AGENTS.md, but we want to verify --from overrides regardless).
    agents.write_text(agents.read_text() + "\n## AGENTS-only edit\n\nfrom Codex side.\n")

    plan = compute_sync_plan(root, source_filename="AGENTS.md")
    claude_item = next(i for i in plan.items if i.path == claude)
    assert claude_item.action == Action.REFRESH
    assert "AGENTS-only edit" in claude_item.new_content
    assert "from AGENTS.md" in claude_item.reason


def test_sync_bare_template_never_clobbers_rich_file(tmp_path):
    """Regression: bare-template AGENTS.md must not overwrite long-edited CLAUDE.md.

    This was the v0.8.4 data-loss bug: AGENTS.md was created by upgrade-workspace
    on v0.8.0 (before the v0.8.1 mirror fix existed) with the bare template, so
    it had a much newer mtime than the user's rich CLAUDE.md. The mtime-based
    source picker chose the template file and overwrote the rich content.
    """
    root = _fresh_workspace(tmp_path, cos_engine="both")
    claude = root / "CLAUDE.md"
    agents = root / "AGENTS.md"

    # Sohail's CLAUDE.md: 100+ lines of real workspace content.
    rich = claude.read_text() + "\n\n## BrandDB conventions\n\n" + "\n".join(
        f"- conventional rule {i}: never speculate without sources" for i in range(50)
    )
    claude.write_text(rich)

    # Bump AGENTS.md's mtime to "much newer than CLAUDE.md" (simulating the
    # freshly-created bare-template scenario). It still has bare-template content.
    import os
    import time
    later = time.time() + 1000
    os.utime(agents, (later, later))
    earlier = time.time() - 1000
    os.utime(claude, (earlier, earlier))

    plan = compute_sync_plan(root)
    # CLAUDE.md (rich) must be the source, NOT AGENTS.md (bare template).
    sync_item = next(i for i in plan.items if i.path == agents)
    assert sync_item.action == Action.REFRESH
    assert "BrandDB conventions" in sync_item.new_content, (
        "AGENTS.md should be overwritten BY the rich CLAUDE.md content"
    )
    assert "from CLAUDE.md" in sync_item.reason


def test_sync_apply_writes_changes_idempotently(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="both")
    claude = root / "CLAUDE.md"
    claude.write_text(claude.read_text() + "\n## sync test\n")

    apply_plan(compute_sync_plan(root))
    # Second pass: in sync now
    plan2 = compute_sync_plan(root)
    assert all(i.action == Action.SKIP for i in plan2.items)


def test_sync_explicit_source_unknown_file_raises(tmp_path):
    root = _fresh_workspace(tmp_path, cos_engine="both")
    with pytest.raises(ValueError) as ei:
        compute_sync_plan(root, source_filename="nonexistent.md")
    assert "doesn't match" in str(ei.value)
