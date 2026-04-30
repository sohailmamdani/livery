from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import frontmatter
import pytest

from livery.status import compute_status, _parse_iso


@pytest.fixture(autouse=True)
def fake_runtimes_ok(monkeypatch):
    """Make run_doctor cheap and deterministic — every runtime "ok"."""
    from livery import doctor

    monkeypatch.setattr(doctor.shutil, "which", lambda b: f"/usr/local/bin/{b}")
    monkeypatch.setattr(doctor, "_http_reachable", lambda *a, **kw: True)


def _make_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "agents").mkdir(parents=True)
    (root / "tickets").mkdir()
    (root / "livery.toml").write_text('name = "ws"\n')
    return root


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write_ticket(
    root: Path,
    *,
    ticket_id: str,
    title: str,
    assignee: str,
    status: str,
    days_old: int,
    days_since_update: int | None = None,
    blocked_on: str | None = None,
) -> None:
    created = (_now() - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    update_age = days_since_update if days_since_update is not None else 0
    updated = (_now() - timedelta(days=update_age)).strftime("%Y-%m-%dT%H:%M:%SZ")
    metadata: dict[str, object] = {
        "id": ticket_id,
        "title": title,
        "assignee": assignee,
        "status": status,
        "created": created,
        "updated": updated,
    }
    if blocked_on:
        metadata["blocked_on"] = blocked_on
    post = frontmatter.Post("body", **metadata)
    (root / "tickets" / f"{ticket_id}.md").write_text(frontmatter.dumps(post) + "\n")


def test_compute_status_empty_workspace(tmp_path):
    root = _make_workspace(tmp_path)
    report = compute_status(root)
    assert report.workspace_name == "ws"
    assert report.open_by_assignee == {}
    assert report.stale_tickets == []
    assert report.blocked_tickets == []
    assert report.recently_closed == []


def test_open_by_assignee_counts_and_sorts_descending(tmp_path):
    root = _make_workspace(tmp_path)
    _write_ticket(root, ticket_id="a", title="t", assignee="lead-dev", status="open", days_old=1)
    _write_ticket(root, ticket_id="b", title="t", assignee="research", status="open", days_old=1)
    _write_ticket(root, ticket_id="c", title="t", assignee="research", status="open", days_old=1)
    _write_ticket(root, ticket_id="d", title="t", assignee="research", status="open", days_old=1)
    _write_ticket(root, ticket_id="e", title="t", assignee="qa", status="done", days_old=1)

    report = compute_status(root)
    # research has 3 open, lead-dev has 1, qa is done so not counted
    assert list(report.open_by_assignee.items()) == [("research", 3), ("lead-dev", 1)]


def test_oldest_open_per_assignee(tmp_path):
    root = _make_workspace(tmp_path)
    _write_ticket(root, ticket_id="new", title="t", assignee="lead-dev", status="open", days_old=2)
    _write_ticket(root, ticket_id="old", title="t", assignee="lead-dev", status="open", days_old=15)
    _write_ticket(root, ticket_id="newer", title="t", assignee="lead-dev", status="open", days_old=1)
    report = compute_status(root)
    assert report.oldest_open_per_assignee["lead-dev"] == 15


def test_stale_tickets_threshold(tmp_path):
    root = _make_workspace(tmp_path)
    _write_ticket(root, ticket_id="fresh", title="fresh", assignee="x", status="open", days_old=3)
    _write_ticket(root, ticket_id="stale", title="stale", assignee="x", status="open", days_old=10)
    _write_ticket(root, ticket_id="staler", title="staler", assignee="x", status="open", days_old=20)

    report = compute_status(root, stale_days=7)
    ids = [t.id for t in report.stale_tickets]
    # Stale tickets sorted oldest-first
    assert ids == ["staler", "stale"]


def test_stale_threshold_configurable(tmp_path):
    root = _make_workspace(tmp_path)
    _write_ticket(root, ticket_id="medium", title="t", assignee="x", status="open", days_old=5)
    report = compute_status(root, stale_days=3)
    assert any(t.id == "medium" for t in report.stale_tickets)


def test_blocked_via_status_field(tmp_path):
    root = _make_workspace(tmp_path)
    _write_ticket(root, ticket_id="b", title="b", assignee="x", status="blocked", days_old=2)
    report = compute_status(root)
    assert [t.id for t in report.blocked_tickets] == ["b"]
    # Blocked tickets do NOT also appear in stale (they're a different bucket)
    assert "b" not in [t.id for t in report.stale_tickets]


def test_blocked_via_blocked_on_field(tmp_path):
    root = _make_workspace(tmp_path)
    _write_ticket(
        root, ticket_id="b", title="b", assignee="x",
        status="open", days_old=2, blocked_on="waiting on Airtable schema PR",
    )
    report = compute_status(root)
    assert len(report.blocked_tickets) == 1
    assert report.blocked_tickets[0].blocked_on == "waiting on Airtable schema PR"


def test_blocked_takes_precedence_over_stale(tmp_path):
    """A ticket that's both old AND blocked should appear in blocked, not stale."""
    root = _make_workspace(tmp_path)
    _write_ticket(
        root, ticket_id="old-blocked", title="t", assignee="x",
        status="open", days_old=30, blocked_on="external dep",
    )
    report = compute_status(root)
    assert "old-blocked" in [t.id for t in report.blocked_tickets]
    assert "old-blocked" not in [t.id for t in report.stale_tickets]


def test_recently_closed_default_limit(tmp_path):
    root = _make_workspace(tmp_path)
    for i in range(8):
        _write_ticket(
            root, ticket_id=f"closed-{i:02d}", title=f"t{i}", assignee="x",
            status="done", days_old=i + 1, days_since_update=i,
        )
    report = compute_status(root)
    # Default limit is 5, sorted by updated desc
    assert len(report.recently_closed) == 5
    assert report.recently_closed[0].id == "closed-00"


def test_recently_closed_full_when_no_limit(tmp_path):
    root = _make_workspace(tmp_path)
    for i in range(8):
        _write_ticket(
            root, ticket_id=f"closed-{i:02d}", title=f"t{i}", assignee="x",
            status="done", days_old=i + 1, days_since_update=i,
        )
    report = compute_status(root, recent_closed_limit=None)
    assert len(report.recently_closed) == 8


def test_runtimes_count(tmp_path):
    root = _make_workspace(tmp_path)
    report = compute_status(root)
    # fake_runtimes_ok fixture makes everything reachable; doctor returns 5 runtimes
    assert report.runtimes_total == 5
    assert report.runtimes_ok == 5


def test_parse_iso_handles_z_suffix():
    out = _parse_iso("2026-04-21T10:00:00Z")
    assert out is not None
    assert out.tzinfo is not None


def test_parse_iso_handles_bare_date():
    out = _parse_iso("2026-04-21")
    assert out is not None
    assert out.year == 2026 and out.month == 4 and out.day == 21


def test_parse_iso_returns_none_on_garbage():
    assert _parse_iso("not a date") is None
    assert _parse_iso(None) is None
