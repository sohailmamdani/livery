from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import frontmatter
from typer.testing import CliRunner

from livery.cli import app


def _make_livery_root(tmp_path: Path) -> Path:
    root = tmp_path / "livery"
    (root / "tickets").mkdir(parents=True)
    (root / "livery").mkdir()
    (root / "livery" / "__init__.py").write_text("")
    (root / "pyproject.toml").write_text("[project]\nname = 'test-livery'\n")
    # git init so commit works
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    return root


def test_ticket_close_flips_status_appends_thread_and_commits(tmp_path, monkeypatch):
    root = _make_livery_root(tmp_path)
    ticket_id = "2026-04-19-050-close-me"
    ticket_post = frontmatter.Post(
        "## Description\n\nDo the thing.\n\n## Thread\n\n### 2026-04-19T00:00:00Z — user\nDo the thing.\n",
        id=ticket_id,
        title="Close me",
        assignee="cos",
        status="open",
        created="2026-04-19T00:00:00Z",
        updated="2026-04-19T00:00:00Z",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True)

    monkeypatch.chdir(root)
    runner = CliRunner()
    with patch("livery.cli.send_message") as fake_send:
        result = runner.invoke(
            app,
            ["ticket", "close", ticket_id, "-s", "Done by tests.", "--no-push"],
        )

    assert result.exit_code == 0, result.stdout
    reloaded = frontmatter.load(ticket_path)
    assert reloaded["status"] == "done"
    assert "Done by tests." in reloaded.content
    assert "— cos" in reloaded.content
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline", "-1"],
        capture_output=True, text=True, check=True,
    )
    assert f"Close ticket {ticket_id}" in log.stdout
    assert "Close me" in log.stdout
    fake_send.assert_called_once()


def test_ticket_close_noop_if_already_done(tmp_path, monkeypatch):
    root = _make_livery_root(tmp_path)
    ticket_id = "2026-04-19-051-already"
    ticket_post = frontmatter.Post(
        "## Description\n\nAlready.\n",
        id=ticket_id,
        title="Already",
        assignee="cos",
        status="done",
        created="x",
        updated="x",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    monkeypatch.chdir(root)
    runner = CliRunner()
    result = runner.invoke(app, ["ticket", "close", ticket_id])

    assert result.exit_code == 1
    combined = result.stdout + result.stderr
    assert "already" in combined and "done" in combined


def test_ticket_close_with_status_cancelled(tmp_path, monkeypatch):
    """`livery ticket close --status cancelled` flips status correctly + uses Cancel verb in commit."""
    root = _make_livery_root(tmp_path)
    ticket_id = "2026-04-19-052-cancel-me"
    ticket_post = frontmatter.Post(
        "## Description\n\nCancel me.\n",
        id=ticket_id,
        title="Cancel me",
        assignee="cos",
        status="open",
        created="2026-04-19T00:00:00Z",
        updated="2026-04-19T00:00:00Z",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "init"],
        check=True, capture_output=True,
    )

    monkeypatch.chdir(root)
    runner = CliRunner()
    with patch("livery.cli.send_message") as fake_send:
        result = runner.invoke(
            app,
            ["ticket", "close", ticket_id, "--status", "cancelled",
             "-s", "Folded into the new schema.", "--no-push"],
        )

    assert result.exit_code == 0, result.stdout + result.stderr
    reloaded = frontmatter.load(ticket_path)
    assert reloaded["status"] == "cancelled"
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline", "-1"],
        capture_output=True, text=True, check=True,
    )
    assert f"Cancel ticket {ticket_id}" in log.stdout
    # Telegram message uses past-tense terminal status
    fake_send.assert_called_once()
    sent_text = fake_send.call_args[0][0]
    assert "cancelled" in sent_text


def test_ticket_close_rejects_invalid_status(tmp_path, monkeypatch):
    root = _make_livery_root(tmp_path)
    ticket_id = "2026-04-19-053-invalid-close"
    ticket_post = frontmatter.Post(
        "## Description\n\nx\n",
        id=ticket_id, title="x", assignee="cos", status="open",
        created="x", updated="x",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    monkeypatch.chdir(root)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ticket", "close", ticket_id, "--status", "in-progress"],
    )

    assert result.exit_code == 1
    assert "must be one of" in (result.stdout + result.stderr)
    # Ticket should NOT have been modified
    reloaded = frontmatter.load(ticket_path)
    assert reloaded["status"] == "open"


def test_ticket_close_already_cancelled_is_noop(tmp_path, monkeypatch):
    """Re-closing an already-terminal ticket (any terminal status) is rejected."""
    root = _make_livery_root(tmp_path)
    ticket_id = "2026-04-19-054-already-cancelled"
    ticket_post = frontmatter.Post(
        "## Description\n\nx\n",
        id=ticket_id, title="x", assignee="cos", status="cancelled",
        created="x", updated="x",
    )
    ticket_path = root / "tickets" / f"{ticket_id}.md"
    ticket_path.write_text(frontmatter.dumps(ticket_post) + "\n")

    monkeypatch.chdir(root)
    runner = CliRunner()
    result = runner.invoke(app, ["ticket", "close", ticket_id])

    assert result.exit_code == 1
    assert "already" in (result.stdout + result.stderr)
