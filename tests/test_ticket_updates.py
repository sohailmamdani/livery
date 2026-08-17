from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import frontmatter
import pytest

from livery.attempts import (
    SCHEMA_VERSION,
    AttemptStatus,
    DispatchAttempt,
    attempt_id_for,
    mark_finished,
    mark_running,
    write_attempt,
)
from livery.ticket_updates import append_ticket_update


def _ticket(root: Path, ticket_id: str = "2026-08-17-001-record-work") -> Path:
    (root / "tickets").mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(
        "## Description\n\nRecord the work.\n\n## Thread\n\n"
        "### 2026-08-17T10:00:00Z — user\nStart.\n",
        id=ticket_id,
        title="Record work",
        assignee="lead-dev",
        status="open",
        created="2026-08-17T10:00:00Z",
        updated="2026-08-17T10:00:00Z",
    )
    path = root / "tickets" / f"{ticket_id}.md"
    path.write_text(frontmatter.dumps(post) + "\n")
    return path


def _attempt(root: Path, ticket_id: str) -> DispatchAttempt:
    return DispatchAttempt(
        schema_version=SCHEMA_VERSION,
        attempt_id=attempt_id_for(ticket_id, "lead-dev"),
        ticket_id=ticket_id,
        assignee="lead-dev",
        runtime="codex",
        model="gpt-5.6-sol",
        workspace_root=str(root),
        agent_cwd="/tmp/repo",
        worktree_path="/tmp/repo-lead-dev-twork",
        prompt_path="/tmp/prompt.txt",
        output_path="/tmp/output.txt",
        command="codex exec ...",
        pid=None,
        started_at="2026-08-17T10:01:00Z",
        finished_at=None,
        exit_code=None,
        status=AttemptStatus.PREPARED,
        failure_class=None,
        failure_detail=None,
        summary_excerpt=[],
        hooks={},
        hook_warnings=[],
    )


def test_append_ticket_update_adds_distilled_thread_entry(tmp_path):
    path = _ticket(tmp_path)
    update = append_ticket_update(
        workspace_root=tmp_path,
        ticket_path=path,
        actor="cos",
        kind="decision",
        message="Use one locked append API so fan-out agents cannot lose updates.",
        timestamp="2026-08-17T10:05:00Z",
    )

    reloaded = frontmatter.load(path)
    assert update.appended is True
    assert reloaded["updated"] == "2026-08-17T10:05:00Z"
    assert "— cos — decision" in reloaded.content
    assert "one locked append API" in reloaded.content


def test_append_ticket_update_is_idempotent_by_event_id(tmp_path):
    path = _ticket(tmp_path)
    kwargs = dict(
        workspace_root=tmp_path,
        ticket_path=path,
        actor="livery",
        kind="dispatch",
        message="Prepared the dispatch.",
        timestamp="2026-08-17T10:05:00Z",
        event_id="dispatch:abc:prepared",
    )

    first = append_ticket_update(**kwargs)
    second = append_ticket_update(**kwargs)

    text = path.read_text()
    assert first.appended is True
    assert second.appended is False
    assert text.count("Prepared the dispatch.") == 1
    assert text.count("livery-event:dispatch:abc:prepared") == 1


def test_append_ticket_update_stays_inside_thread_before_later_section(tmp_path):
    path = _ticket(tmp_path)
    post = frontmatter.load(path)
    post.content += "\n## Acceptance\n\n- Tests pass.\n"
    path.write_text(frontmatter.dumps(post) + "\n")

    append_ticket_update(
        workspace_root=tmp_path,
        ticket_path=path,
        actor="lead-dev",
        kind="progress",
        message="The implementation is complete.",
        timestamp="2026-08-17T10:06:00Z",
    )

    text = frontmatter.load(path).content
    assert text.index("The implementation is complete.") < text.index("## Acceptance")


def test_concurrent_ticket_updates_do_not_overwrite_each_other(tmp_path):
    path = _ticket(tmp_path)

    def add(index: int) -> None:
        append_ticket_update(
            workspace_root=tmp_path,
            ticket_path=path,
            actor=f"agent-{index}",
            kind="progress",
            message=f"Completed milestone {index}.",
            timestamp=f"2026-08-17T10:{index:02d}:00Z",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add, range(20)))

    text = path.read_text()
    for index in range(20):
        assert text.count(f"Completed milestone {index}.") == 1


def test_append_ticket_update_rejects_noise_without_kind(tmp_path):
    path = _ticket(tmp_path)
    with pytest.raises(ValueError, match="kind must be one of"):
        append_ticket_update(
            workspace_root=tmp_path,
            ticket_path=path,
            actor="cos",
            kind="command-log",
            message="pytest output",
            timestamp="2026-08-17T10:05:00Z",
        )


def test_attempt_lifecycle_is_mirrored_and_summary_is_distilled(tmp_path):
    ticket_id = "2026-08-17-001-record-work"
    path = _ticket(tmp_path, ticket_id)
    attempt = _attempt(tmp_path, ticket_id)

    write_attempt(attempt, tmp_path)
    write_attempt(attempt, tmp_path)  # repeated persistence stays idempotent
    mark_running(attempt, pid=1234, workspace_root=tmp_path)
    mark_finished(
        attempt,
        exit_code=0,
        workspace_root=tmp_path,
        summary_excerpt=[
            f"Ticket: {ticket_id}",
            "Status: done",
            "Summary: Added the shared update path and lifecycle mirroring.",
            "Files touched: livery/ticket_updates.py, livery/attempts.py",
            "Tests run: uv run pytest tests/test_ticket_updates.py (pass)",
            "Pushback / flags for sohail: none",
        ],
    )

    text = path.read_text()
    assert text.count("Prepared dispatch") == 1
    assert text.count("started work in dispatch") == 1
    assert text.count("completed dispatch") == 1
    assert "Added the shared update path" in text
    assert "- Files: livery/ticket_updates.py" in text
    assert "- Tests: uv run pytest" in text
    assert "Pushback / flags for sohail" not in text
    assert "codex exec ..." not in text


def test_blocked_summary_never_writes_a_success_event(tmp_path):
    ticket_id = "2026-08-17-001-record-work"
    path = _ticket(tmp_path, ticket_id)
    attempt = _attempt(tmp_path, ticket_id)
    attempt.status = AttemptStatus.RUNNING
    write_attempt(attempt, tmp_path)

    mark_finished(
        attempt,
        exit_code=0,
        workspace_root=tmp_path,
        summary_excerpt=["Status: blocked", "Summary: Needs production credentials."],
    )
    attempt.status = AttemptStatus.BLOCKED
    attempt.failure_detail = "agent reported work blocked"
    write_attempt(attempt, tmp_path)

    text = path.read_text()
    assert text.count("was blocked in dispatch") == 1
    assert "Needs production credentials" in text
    assert "completed dispatch" not in text


def test_pseudo_ticket_attempt_does_not_create_ticket(tmp_path):
    attempt = _attempt(tmp_path, "schedule-evening-brief")
    write_attempt(attempt, tmp_path)
    assert not (tmp_path / "tickets").exists()
