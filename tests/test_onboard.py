from __future__ import annotations

from pathlib import Path

import frontmatter
import pytest

from livery import onboard as onboard_mod
from livery.onboard import _list_agents, run_onboarding


@pytest.fixture
def fake_all_runtimes_ok(monkeypatch):
    """Make run_doctor report every runtime as OK."""
    from livery import doctor as doctor_mod

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda b: f"/usr/local/bin/{b}")
    monkeypatch.setattr(doctor_mod, "_http_reachable", lambda *a, **kw: True)


@pytest.fixture
def fake_no_runtimes(monkeypatch):
    from livery import doctor as doctor_mod

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _b: None)
    monkeypatch.setattr(doctor_mod, "_http_reachable", lambda *a, **kw: False)


def _make_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "agents").mkdir(parents=True)
    (root / "tickets").mkdir()
    (root / "livery.toml").write_text('name = "ws"\n')
    # Realistic workspace has at least one CoS convention file at root
    (root / "CLAUDE.md").write_text("# ws\n")
    return root


def _write_agent(root: Path, agent_id: str) -> None:
    d = root / "agents" / agent_id
    d.mkdir(parents=True)
    post = frontmatter.Post(
        "role",
        id=agent_id,
        name=agent_id,
        runtime="codex",
        cwd="/tmp",
        reports_to="cos",
        hired="2026-04-21",
    )
    (d / "agent.md").write_text(frontmatter.dumps(post) + "\n")
    (d / "AGENTS.md").write_text("# stub\n")


def test_list_agents_empty(tmp_path):
    root = _make_workspace(tmp_path)
    assert _list_agents(root) == []


def test_list_agents_sorted(tmp_path):
    root = _make_workspace(tmp_path)
    _write_agent(root, "writer")
    _write_agent(root, "research")
    _write_agent(root, "lead-dev")
    assert _list_agents(root) == ["lead-dev", "research", "writer"]


def test_list_agents_ignores_dirs_without_agent_md(tmp_path):
    root = _make_workspace(tmp_path)
    _write_agent(root, "writer")
    (root / "agents" / "half-built").mkdir()
    (root / "agents" / "half-built" / "AGENTS.md").write_text("# stub\n")
    # Missing agent.md — should be skipped.
    assert _list_agents(root) == ["writer"]


def test_run_onboarding_no_runtimes_returns_1(tmp_path, fake_no_runtimes):
    # cwd doesn't matter — we exit early on the runtime check.
    exit_code = run_onboarding(cwd=tmp_path)
    assert exit_code == 1


def test_run_onboarding_existing_workspace_with_agents(tmp_path, fake_all_runtimes_ok, capsys):
    root = _make_workspace(tmp_path)
    _write_agent(root, "writer")
    exit_code = run_onboarding(cwd=root)
    assert exit_code == 0
    out = capsys.readouterr().out
    # Detected the workspace
    assert str(root) in out
    # Listed the existing agent
    assert "writer" in out
    # Showed the next-steps block
    assert "Next steps" in out
    assert "CLAUDE.md" in out


def test_run_onboarding_existing_workspace_no_agents_user_declines(
    tmp_path, fake_all_runtimes_ok, monkeypatch, capsys
):
    root = _make_workspace(tmp_path)
    # User says "no" to hiring
    monkeypatch.setattr("typer.confirm", lambda *a, **kw: False)
    exit_code = run_onboarding(cwd=root)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "No agents hired yet" in out
    # Tells the user how to hire later
    assert "livery hire" in out


def test_run_onboarding_no_workspace_user_declines(
    tmp_path, fake_all_runtimes_ok, monkeypatch, capsys
):
    # Empty tmp_path, no livery.toml anywhere up the tree.
    # find_root walks up from cwd. tmp_path is inside /tmp or /private/tmp;
    # tests assume no livery.toml sits above. Sanity-check first:
    from livery.paths import find_root

    with pytest.raises(RuntimeError):
        find_root(tmp_path)

    monkeypatch.setattr("typer.confirm", lambda *a, **kw: False)
    exit_code = run_onboarding(cwd=tmp_path)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "not currently inside a Livery workspace" in out
    assert "re-run `livery onboard`" in out


def test_run_onboarding_detects_workspace_from_subdirectory(
    tmp_path, fake_all_runtimes_ok, capsys
):
    """find_root walks upward; onboard should follow suit."""
    root = _make_workspace(tmp_path)
    _write_agent(root, "writer")
    sub = root / "some" / "deep" / "subdir"
    sub.mkdir(parents=True)
    exit_code = run_onboarding(cwd=sub)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert str(root) in out
    assert "writer" in out
