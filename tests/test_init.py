from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from livery.init import _render_livery_toml, init_workspace


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
    paths = {p.relative_to(target) for p in created}
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
    paths = {p.relative_to(target) for p in created}
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
