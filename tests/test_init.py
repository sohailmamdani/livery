from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from livery.init import (
    SkillCollisionResolution,
    _render_livery_toml,
    init_workspace,
)


def test_render_livery_toml_minimal():
    out = _render_livery_toml(
        name="ws",
        description="short",
        default_runtime=None,
        telegram_chat_id=None,
        telegram_token_file=None,
        cos_engines=["claude_code", "codex"],
    )
    parsed = tomllib.loads(out)
    assert parsed["name"] == "ws"
    assert parsed["description"] == "short"
    assert parsed["cos_engines"] == ["claude_code", "codex"]
    assert "default_runtime" not in parsed
    assert parsed.get("telegram", {}) == {}
    # Commented examples should still be present as comments
    assert "# default_runtime" in out
    assert "# chat_id" in out
    assert "# token_file" in out


def test_render_livery_toml_with_all_fields():
    out = _render_livery_toml(
        name="ws",
        description="a description",
        default_runtime="claude_code",
        telegram_chat_id="-1001234567890",
        telegram_token_file="~/.claude/channels/telegram/.env",
        cos_engines=["pi"],
    )
    parsed = tomllib.loads(out)
    assert parsed["default_runtime"] == "claude_code"
    assert parsed["telegram"]["chat_id"] == "-1001234567890"
    assert parsed["telegram"]["token_file"] == "~/.claude/channels/telegram/.env"
    assert parsed["cos_engines"] == ["pi"]
    # Commented examples should NOT appear for fields we set
    assert "# default_runtime" not in out
    assert "# chat_id" not in out
    assert "# token_file" not in out


def test_init_workspace_creates_standard_layout(tmp_path):
    """Default cos_engine='both' produces both Claude Code and Codex assets."""
    created = init_workspace(
        target=tmp_path / "ws",
        name="test-ws",
        description="desc",
    )
    target = tmp_path / "ws"
    paths = {p.relative_to(target) for p in created.created}
    assert Path("livery.toml") in paths
    assert Path("CLAUDE.md") in paths
    assert Path("AGENTS.md") in paths
    assert Path("agents/.gitkeep") in paths
    assert Path("tickets/.gitkeep") in paths
    # Claude Code assets
    assert Path(".claude/commands/ticket.md") in paths
    assert Path(".claude/skills/new-ticket/SKILL.md") in paths
    # Codex assets
    assert Path(".agents/skills/new-ticket/SKILL.md") in paths


def test_init_workspace_writes_default_runtime_and_telegram(tmp_path):
    init_workspace(
        target=tmp_path / "ws",
        name="test-ws",
        description="desc",
        default_runtime="codex",
        telegram_chat_id="-1001",
        telegram_token_file="~/.env",
    )
    parsed = tomllib.loads((tmp_path / "ws" / "livery.toml").read_text())
    assert parsed["default_runtime"] == "codex"
    assert parsed["telegram"]["chat_id"] == "-1001"
    assert parsed["telegram"]["token_file"] == "~/.env"


def test_init_workspace_default_writes_both_cos_files(tmp_path):
    """Default cos_engine='both' writes CLAUDE.md AND AGENTS.md with identical content."""
    target = tmp_path / "ws"
    init_workspace(target=target, name="ws", description="desc")
    claude = (target / "CLAUDE.md").read_text()
    agents = (target / "AGENTS.md").read_text()
    assert claude == agents
    assert "# ws" in claude
    # Markered framework block at top, user-editable section below
    assert "LIVERY-MANAGED:BEGIN" in claude
    assert "LIVERY-MANAGED:END" in claude
    assert "Custom conventions for the CoS" in claude


def test_init_workspace_cos_engine_claude_code_only(tmp_path):
    target = tmp_path / "ws"
    init_workspace(target=target, name="ws", cos_engine="claude_code")
    assert (target / "CLAUDE.md").exists()
    assert not (target / "AGENTS.md").exists()
    # Claude Code skill scaffolding present
    assert (target / ".claude" / "skills" / "new-ticket" / "SKILL.md").exists()
    assert (target / ".claude" / "commands" / "ticket.md").exists()
    # Codex skill scaffolding absent
    assert not (target / ".agents").exists()


def test_init_workspace_cos_engine_codex_only(tmp_path):
    target = tmp_path / "ws"
    init_workspace(target=target, name="ws", cos_engine="codex")
    assert (target / "AGENTS.md").exists()
    assert not (target / "CLAUDE.md").exists()
    # Codex skill scaffolding present at the .agents/ path
    assert (target / ".agents" / "skills" / "new-ticket" / "SKILL.md").exists()
    # No .claude/ directory at all — Codex doesn't read it
    assert not (target / ".claude").exists()


def test_init_workspace_rejects_invalid_cos_engine(tmp_path):
    with pytest.raises(ValueError) as ei:
        init_workspace(target=tmp_path / "ws", name="ws", cos_engine="notacos")
    assert "Unknown CoS engine" in str(ei.value)


def test_init_workspace_supports_pi(tmp_path):
    """Pi reads AGENTS.md; no skill or command directories."""
    target = tmp_path / "ws"
    init_workspace(target=target, name="ws", cos_engine="pi")
    assert (target / "AGENTS.md").exists()
    assert not (target / "CLAUDE.md").exists()
    assert not (target / ".claude").exists()
    assert not (target / ".agents").exists()
    # cos_engines persisted to livery.toml
    parsed = tomllib.loads((target / "livery.toml").read_text())
    assert parsed["cos_engines"] == ["pi"]


def test_init_workspace_supports_opencode(tmp_path):
    """OpenCode reads AGENTS.md; no skill or command directories."""
    target = tmp_path / "ws"
    init_workspace(target=target, name="ws", cos_engine="opencode")
    assert (target / "AGENTS.md").exists()
    assert not (target / ".claude").exists()
    assert not (target / ".agents").exists()


def test_init_workspace_multiple_engines_dedupe_filenames(tmp_path):
    """codex + pi + opencode all read AGENTS.md — write it once."""
    target = tmp_path / "ws"
    created = init_workspace(target=target, name="ws", cos_engine="codex,pi,opencode")
    paths = {p.relative_to(target) for p in created.created}
    # AGENTS.md scaffolded once even though three engines want it
    assert sum(1 for p in paths if str(p) == "AGENTS.md") == 1
    # Codex still gets its skill dir; pi/opencode don't add anything
    assert Path(".agents/skills/new-ticket/SKILL.md") in paths
    # CLAUDE.md not requested
    assert not (target / "CLAUDE.md").exists()


def test_init_workspace_both_alias_still_works(tmp_path):
    """Back-compat: 'both' resolves to claude_code + codex."""
    target = tmp_path / "ws"
    init_workspace(target=target, name="ws", cos_engine="both")
    assert (target / "CLAUDE.md").exists()
    assert (target / "AGENTS.md").exists()
    parsed = tomllib.loads((target / "livery.toml").read_text())
    assert parsed["cos_engines"] == ["claude_code", "codex"]


def test_init_workspace_refuses_overwrite(tmp_path):
    target = tmp_path / "ws"
    init_workspace(target=target, name="v1")
    with pytest.raises(FileExistsError):
        init_workspace(target=target, name="v2")


def test_init_workspace_overwrite_force(tmp_path):
    target = tmp_path / "ws"
    init_workspace(target=target, name="v1")
    init_workspace(target=target, name="v2", overwrite=True)
    parsed = tomllib.loads((target / "livery.toml").read_text())
    assert parsed["name"] == "v2"


# -----------------------------------------------------------------------------
# init in a populated directory: existing CLAUDE.md / AGENTS.md handling
# -----------------------------------------------------------------------------


def test_init_preserves_existing_claude_md_below_template(tmp_path):
    """If CLAUDE.md exists, init writes the fresh template and appends old content."""
    target = tmp_path / "ws"
    target.mkdir()
    old_content = "# My existing project\n\nLots of important notes.\n\n- rule one\n- rule two\n"
    (target / "CLAUDE.md").write_text(old_content)

    result = init_workspace(target=target, name="ws", cos_engine="claude_code")

    new_content = (target / "CLAUDE.md").read_text()
    # Fresh template (managed block) is at the top
    assert new_content.startswith("<!-- LIVERY-MANAGED:BEGIN")
    # Old user content is preserved somewhere in the file
    assert "Lots of important notes." in new_content
    assert "- rule one" in new_content
    # InitResult records this as appended, not created
    assert (target / "CLAUDE.md") in result.appended
    assert (target / "CLAUDE.md") not in result.created


def test_init_strips_old_managed_block_when_appending(tmp_path):
    """If the existing CLAUDE.md has its own LIVERY-MANAGED block, init strips it
    so the new template's block isn't duplicated."""
    from livery.cos_engines import MANAGED_BEGIN, MANAGED_END

    target = tmp_path / "ws"
    target.mkdir()
    old_with_managed = (
        f"{MANAGED_BEGIN}\n"
        "old framework block content\n"
        f"{MANAGED_END}\n"
        "\n"
        "# My real content\n\n"
        "- preserved rule\n"
    )
    (target / "CLAUDE.md").write_text(old_with_managed)

    init_workspace(target=target, name="ws", cos_engine="claude_code")

    new_content = (target / "CLAUDE.md").read_text()
    # New managed block is present
    assert "Push back hard" in new_content  # part of fresh COS_MANAGED_BLOCK
    # Old "framework block" content is gone
    assert "old framework block content" not in new_content
    # User's real content survives
    assert "# My real content" in new_content
    assert "- preserved rule" in new_content


def test_init_writes_fresh_template_when_no_existing_convention_file(tmp_path):
    """No existing CLAUDE.md → just writes fresh template, nothing in `appended`."""
    target = tmp_path / "ws"
    result = init_workspace(target=target, name="ws", cos_engine="claude_code")
    assert (target / "CLAUDE.md") in result.created
    assert (target / "CLAUDE.md") not in result.appended


# -----------------------------------------------------------------------------
# init in a populated directory: existing skill / command collision
# -----------------------------------------------------------------------------


def test_init_skips_user_written_skill_by_default(tmp_path):
    """No callback → user's skill is left in place; Livery's is NOT installed."""
    target = tmp_path / "ws"
    skill_path = target / ".claude" / "skills" / "new-ticket" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    user_content = "---\nname: new-ticket\ndescription: my own ticket flow\n---\n\nUser-written.\n"
    skill_path.write_text(user_content)

    result = init_workspace(target=target, name="ws", cos_engine="claude_code")

    # User content untouched
    assert skill_path.read_text() == user_content
    # Recorded as skipped
    assert any(p == skill_path for p, _ in result.skipped)


def test_init_no_op_replaces_livery_managed_skill(tmp_path):
    """Existing skill that's already Livery-managed → no-op refresh, no skip."""
    target = tmp_path / "ws"
    skill_path = target / ".claude" / "skills" / "new-ticket" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    # Use a stub with the marker to claim "this is ours"
    skill_path.write_text(
        "---\nname: new-ticket\ndescription: stale livery version\nlivery: managed\n---\n\nstale.\n"
    )

    result = init_workspace(target=target, name="ws", cos_engine="claude_code")

    # Refreshed in place — no skip, no rename
    assert not any(p == skill_path for p, _ in result.skipped)
    assert not any(orig == skill_path for orig, _ in result.backed_up)
    # Content is now the fresh shipped version (which has `livery: managed`)
    assert "livery: managed" in skill_path.read_text()


def test_init_renames_user_skill_via_callback(tmp_path):
    """Callback returns rename → user's skill folder is renamed, Livery's installed at original path."""
    target = tmp_path / "ws"
    skill_dir = target / ".claude" / "skills" / "new-ticket"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\nname: new-ticket\ndescription: my custom ticket flow\n---\n\nUser-written content.\n"
    )

    result = init_workspace(
        target=target,
        name="ws",
        cos_engine="claude_code",
        skill_collision_callback=lambda p: SkillCollisionResolution.rename("legacy-ticket"),
    )

    # User's skill is now at the new name
    new_skill_dir = target / ".claude" / "skills" / "legacy-ticket"
    assert new_skill_dir.is_dir()
    new_skill_md = new_skill_dir / "SKILL.md"
    assert new_skill_md.exists()
    # Frontmatter `name` was updated to match the new directory
    new_text = new_skill_md.read_text()
    assert "name: legacy-ticket" in new_text
    # User's body content is preserved
    assert "User-written content." in new_text

    # Livery's skill is at the original path
    assert skill_path.exists()
    assert "livery: managed" in skill_path.read_text()

    # InitResult records the rename
    assert any(orig == skill_path for orig, _ in result.backed_up)


def test_init_renames_user_command_via_callback(tmp_path):
    """Same flow for slash commands (single-file rename, no frontmatter update needed)."""
    target = tmp_path / "ws"
    cmd_dir = target / ".claude" / "commands"
    cmd_dir.mkdir(parents=True)
    cmd_path = cmd_dir / "ticket.md"
    cmd_path.write_text("---\ndescription: user's command\n---\n\nUser flow.\n")

    init_workspace(
        target=target,
        name="ws",
        cos_engine="claude_code",
        skill_collision_callback=lambda p: SkillCollisionResolution.rename("my-ticket"),
    )

    # User's command is at the new name
    assert (cmd_dir / "my-ticket.md").exists()
    assert "User flow." in (cmd_dir / "my-ticket.md").read_text()

    # Livery's command is at the original path
    assert cmd_path.exists()
    assert "livery: managed" in cmd_path.read_text()


def test_init_overwrite_callback_replaces_user_skill(tmp_path):
    """Callback returns overwrite → user's skill is replaced. Destructive."""
    target = tmp_path / "ws"
    skill_path = target / ".claude" / "skills" / "new-ticket" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: new-ticket\n---\n\nUser content gone.\n")

    init_workspace(
        target=target,
        name="ws",
        cos_engine="claude_code",
        skill_collision_callback=lambda p: SkillCollisionResolution.overwrite(),
    )

    # User content is gone; Livery's is in place
    text = skill_path.read_text()
    assert "User content gone." not in text
    assert "livery: managed" in text


def test_init_rename_collision_raises(tmp_path):
    """If the user-chosen rename target already exists, init raises FileExistsError."""
    target = tmp_path / "ws"
    skills_dir = target / ".claude" / "skills"
    (skills_dir / "new-ticket").mkdir(parents=True)
    (skills_dir / "new-ticket" / "SKILL.md").write_text(
        "---\nname: new-ticket\n---\n\nuser.\n"
    )
    # Pre-existing folder at the proposed rename target
    (skills_dir / "legacy-ticket").mkdir(parents=True)

    with pytest.raises(FileExistsError):
        init_workspace(
            target=target,
            name="ws",
            cos_engine="claude_code",
            skill_collision_callback=lambda p: SkillCollisionResolution.rename("legacy-ticket"),
        )


def test_skill_collision_resolution_rename_requires_name():
    with pytest.raises(ValueError):
        SkillCollisionResolution.rename("")
