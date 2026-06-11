from __future__ import annotations

import frontmatter
from typer.testing import CliRunner

from livery.cli import app
from livery.init import init_workspace


def test_memory_add_creates_markdown_entry(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    init_workspace(target=workspace, name="ws")
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "add",
            "--type",
            "lesson",
            "--title",
            "Review dispatch output before closing",
            "--body",
            "Always read the dispatch summary before closing delegated work.",
            "--source-ticket",
            "2026-06-10-001-dispatch-review",
        ],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    rel = result.stdout.strip()
    assert rel.startswith("memory/lessons/")
    assert rel.endswith("-review-dispatch-output-before-closing.md")

    post = frontmatter.load(workspace / rel)
    assert post["type"] == "lesson"
    assert post["title"] == "Review dispatch output before closing"
    assert post["scope"] == "workspace"
    assert post["source_ticket"] == "2026-06-10-001-dispatch-review"
    assert "Always read the dispatch summary" in post.content


def test_memory_list_search_and_show(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    init_workspace(target=workspace, name="ws")
    monkeypatch.chdir(workspace)

    add_result = CliRunner().invoke(
        app,
        [
            "memory",
            "add",
            "--type",
            "decision",
            "--title",
            "Use worktrees for agent edits",
            "--body",
            "Engineering agents should dispatch with --worktree.",
        ],
    )
    assert add_result.exit_code == 0, add_result.stdout + add_result.stderr

    list_result = CliRunner().invoke(app, ["memory", "list"])
    assert list_result.exit_code == 0, list_result.stdout + list_result.stderr
    assert "decision" in list_result.stdout
    assert "Use worktrees for agent edits" in list_result.stdout

    search_result = CliRunner().invoke(app, ["memory", "search", "worktree"])
    assert search_result.exit_code == 0, search_result.stdout + search_result.stderr
    assert "Use worktrees for agent edits" in search_result.stdout

    show_result = CliRunner().invoke(app, ["memory", "show", "worktrees"])
    assert show_result.exit_code == 0, show_result.stdout + show_result.stderr
    assert "type: decision" in show_result.stdout
    assert "Engineering agents should dispatch" in show_result.stdout


def test_memory_rejects_unknown_type(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    init_workspace(target=workspace, name="ws")
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "add",
            "--type",
            "fact",
            "--title",
            "Nope",
            "--body",
            "Nope",
        ],
    )

    assert result.exit_code == 1
    assert "memory type must be one of" in result.stderr


def test_memory_search_empty_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    init_workspace(target=workspace, name="ws")
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(app, ["memory", "search", "missing"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "(no matches)" in result.stdout
