from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import frontmatter
import pytest

from livery.doctor import (
    RUNTIME_BINARIES,
    check_runtime,
    check_workspace_agents,
    run_doctor,
)


@pytest.fixture
def fake_http_all_down(monkeypatch):
    """Make _http_reachable return False for everything."""
    from livery import doctor

    monkeypatch.setattr(doctor, "_http_reachable", lambda *a, **kw: False)


@pytest.fixture
def fake_http_all_up(monkeypatch):
    from livery import doctor

    monkeypatch.setattr(doctor, "_http_reachable", lambda *a, **kw: True)


@pytest.fixture
def fake_which_nothing(monkeypatch):
    """shutil.which returns None for every lookup."""
    import livery.doctor as doctor_mod

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _b: None)


@pytest.fixture
def fake_which_everything(monkeypatch):
    import livery.doctor as doctor_mod

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda b: f"/usr/local/bin/{b}")


def test_check_runtime_codex_binary_present(fake_which_everything, fake_http_all_down):
    status = check_runtime("codex")
    assert status.runtime == "codex"
    assert status.binary == "codex"
    assert status.binary_path == "/usr/local/bin/codex"
    assert status.http_endpoint is None
    assert status.ok is True
    assert status.notes == []


def test_check_runtime_codex_binary_missing(fake_which_nothing, fake_http_all_down):
    status = check_runtime("codex")
    assert status.binary_path is None
    assert status.ok is False
    assert any("codex" in n for n in status.notes)


def test_check_runtime_lm_studio_endpoint_up(fake_which_nothing, fake_http_all_up):
    status = check_runtime("lm_studio")
    assert status.binary is None
    assert status.http_reachable is True
    assert status.ok is True


def test_check_runtime_lm_studio_endpoint_down(fake_which_nothing, fake_http_all_down):
    status = check_runtime("lm_studio")
    assert status.http_reachable is False
    assert status.ok is False
    assert any("1234" in n for n in status.notes)


def test_check_runtime_ollama_either_binary_or_endpoint_sufficient(monkeypatch):
    import livery.doctor as doctor_mod

    # Binary present, endpoint down → still OK
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda b: f"/bin/{b}" if b == "ollama" else None)
    monkeypatch.setattr(doctor_mod, "_http_reachable", lambda *a, **kw: False)
    status = check_runtime("ollama")
    assert status.ok is True

    # Binary missing, endpoint up → still OK
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _b: None)
    monkeypatch.setattr(doctor_mod, "_http_reachable", lambda *a, **kw: True)
    status = check_runtime("ollama")
    assert status.ok is True

    # Both missing → fails
    monkeypatch.setattr(doctor_mod, "_http_reachable", lambda *a, **kw: False)
    status = check_runtime("ollama")
    assert status.ok is False


def test_check_runtime_unknown():
    status = check_runtime("totally-made-up")
    assert status.ok is False
    assert any("unknown" in n for n in status.notes)


def test_http_reachable_uses_urllib_and_tolerates_http_errors():
    """_http_reachable treats HTTPError (e.g. 401) as 'server up'."""
    import urllib.error

    from livery.doctor import _http_reachable

    # Simulate HTTPError — server responded with a non-2xx → still "up"
    def raise_http_error(*a, **kw):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    with patch("urllib.request.urlopen", side_effect=raise_http_error):
        assert _http_reachable("http://example/") is True

    # Simulate URLError (connection refused, etc.) → "down"
    def raise_url_error(*a, **kw):
        raise urllib.error.URLError("refused")

    with patch("urllib.request.urlopen", side_effect=raise_url_error):
        assert _http_reachable("http://example/") is False


def _make_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "agents").mkdir(parents=True)
    (root / "livery.toml").write_text("name = 'ws'\n")
    return root


def _write_agent(root: Path, agent_id: str, runtime: str, cwd: str) -> None:
    d = root / "agents" / agent_id
    d.mkdir(parents=True)
    post = frontmatter.Post(
        "role",
        id=agent_id,
        name=agent_id,
        runtime=runtime,
        cwd=cwd,
        reports_to="cos",
        hired="2026-04-20",
    )
    (d / "agent.md").write_text(frontmatter.dumps(post) + "\n")


def test_check_workspace_agents_cwd_missing(tmp_path, fake_which_everything, fake_http_all_up):
    root = _make_workspace(tmp_path)
    _write_agent(root, "qa", "codex", "/does/not/exist")

    from livery.doctor import check_all_runtimes

    rt_map = {r.runtime: r for r in check_all_runtimes()}
    agents = check_workspace_agents(root, rt_map)
    assert len(agents) == 1
    assert agents[0].cwd_exists is False
    assert agents[0].ok is False
    assert any("does not exist" in n for n in agents[0].notes)


def test_check_workspace_agents_cwd_not_git(tmp_path, fake_which_everything, fake_http_all_up):
    root = _make_workspace(tmp_path)
    repo_ish = tmp_path / "not-a-repo"
    repo_ish.mkdir()
    _write_agent(root, "qa", "codex", str(repo_ish))

    from livery.doctor import check_all_runtimes

    rt_map = {r.runtime: r for r in check_all_runtimes()}
    agents = check_workspace_agents(root, rt_map)
    assert agents[0].cwd_exists is True
    assert agents[0].cwd_is_git is False
    # runtime_ok true, but a warning note about not-a-git-repo
    assert any("not a git repo" in n for n in agents[0].notes)


def test_check_workspace_agents_runtime_unavailable(tmp_path, fake_which_nothing, fake_http_all_down):
    root = _make_workspace(tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    (cwd / ".git").mkdir()
    _write_agent(root, "qa", "codex", str(cwd))

    from livery.doctor import check_all_runtimes

    rt_map = {r.runtime: r for r in check_all_runtimes()}
    agents = check_workspace_agents(root, rt_map)
    assert agents[0].runtime_ok is False
    assert agents[0].ok is False


def test_check_workspace_agents_happy_path(tmp_path, fake_which_everything, fake_http_all_up):
    root = _make_workspace(tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    (cwd / ".git").mkdir()
    _write_agent(root, "qa", "codex", str(cwd))

    from livery.doctor import check_all_runtimes

    rt_map = {r.runtime: r for r in check_all_runtimes()}
    agents = check_workspace_agents(root, rt_map)
    assert agents[0].ok is True
    assert agents[0].notes == []


def test_run_doctor_without_workspace(fake_which_everything, fake_http_all_up):
    report = run_doctor(workspace_root=None)
    assert report.workspace_root is None
    assert report.agents == []
    assert report.ok is True
    assert len(report.runtimes) == len(RUNTIME_BINARIES)


def test_run_doctor_to_dict_shape(fake_which_everything, fake_http_all_up):
    report = run_doctor(workspace_root=None)
    d = report.to_dict()
    assert d["ok"] is True
    assert "runtimes" in d
    assert "agents" in d
    assert all("runtime" in r for r in d["runtimes"])
