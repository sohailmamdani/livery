from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from livery.dispatch_view import (
    ACTIVE_THRESHOLD_SECONDS,
    DispatchState,
    SUMMARY_BEGIN,
    SUMMARY_END,
    find_dispatch,
    humanize_age,
    list_dispatches,
)


def _write_output(
    dir: Path,
    *,
    ticket: str,
    assignee: str,
    body: str,
    age_seconds: int = 0,
) -> Path:
    """Write a livery-dispatch-<ticket>-<assignee>.out file with `body`, then
    rewind its mtime by `age_seconds`."""
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / f"livery-dispatch-{ticket}-{assignee}.out"
    path.write_text(body)
    if age_seconds:
        now = time.time()
        os.utime(path, (now - age_seconds, now - age_seconds))
    return path


def test_list_dispatches_empty(tmp_path):
    assert list_dispatches(tmp_path) == []


def test_list_dispatches_classifies_done(tmp_path):
    body = (
        "Some output...\n\n"
        f"{SUMMARY_BEGIN}\n"
        "Ticket: 2026-04-22-001-x\n"
        "Status: done\n"
        "Summary: Did the thing.\n"
        f"{SUMMARY_END}\n"
    )
    _write_output(tmp_path, ticket="2026-04-22-001-x", assignee="research", body=body, age_seconds=600)

    views = list_dispatches(tmp_path)
    assert len(views) == 1
    v = views[0]
    assert v.state == DispatchState.DONE
    assert v.label == "2026-04-22-001-x-research"
    # Summary excerpt is grabbed
    assert any("Status: done" in line for line in v.summary_excerpt)


def test_list_dispatches_classifies_active(tmp_path):
    """No summary marker, file written recently → active."""
    _write_output(tmp_path, ticket="t1", assignee="a", body="working...\n", age_seconds=10)
    views = list_dispatches(tmp_path)
    assert views[0].state == DispatchState.ACTIVE


def test_list_dispatches_classifies_stale(tmp_path):
    """No summary marker, file is old → stale (probably crashed)."""
    _write_output(
        tmp_path, ticket="t1", assignee="a",
        body="never finished...\n",
        age_seconds=ACTIVE_THRESHOLD_SECONDS + 60,
    )
    views = list_dispatches(tmp_path)
    assert views[0].state == DispatchState.STALE


def test_list_dispatches_sorts_most_recent_first(tmp_path):
    _write_output(tmp_path, ticket="old", assignee="a", body="x", age_seconds=1000)
    _write_output(tmp_path, ticket="new", assignee="a", body="x", age_seconds=10)
    _write_output(tmp_path, ticket="middle", assignee="a", body="x", age_seconds=200)

    views = list_dispatches(tmp_path)
    labels = [v.label for v in views]
    assert labels == ["new-a", "middle-a", "old-a"]


def test_last_line_captures_tail(tmp_path):
    _write_output(tmp_path, ticket="t", assignee="a", body="line one\nline two\nfinal line\n")
    v = list_dispatches(tmp_path)[0]
    assert v.last_line == "final line"


def test_last_line_skips_blank_lines(tmp_path):
    _write_output(tmp_path, ticket="t", assignee="a", body="real content\n\n\n")
    v = list_dispatches(tmp_path)[0]
    assert v.last_line == "real content"


def test_summary_excerpt_capped_at_5_lines(tmp_path):
    extra = "\n".join(f"line {i}" for i in range(20))
    body = f"prelude\n{SUMMARY_BEGIN}\n{extra}\n{SUMMARY_END}\n"
    _write_output(tmp_path, ticket="t", assignee="a", body=body)
    v = list_dispatches(tmp_path)[0]
    assert len(v.summary_excerpt) == 5


def test_find_dispatch_unique_match(tmp_path):
    _write_output(tmp_path, ticket="2026-04-22-001-x", assignee="research", body="x")
    _write_output(tmp_path, ticket="2026-04-22-002-y", assignee="qa", body="x")
    v = find_dispatch("research", tmp_path)
    assert "research" in v.label


def test_find_dispatch_no_match_raises(tmp_path):
    _write_output(tmp_path, ticket="t", assignee="a", body="x")
    with pytest.raises(ValueError) as ei:
        find_dispatch("nothere", tmp_path)
    assert "No dispatch matching" in str(ei.value)


def test_find_dispatch_multi_match_raises(tmp_path):
    _write_output(tmp_path, ticket="2026-04-22-001-x", assignee="research", body="x")
    _write_output(tmp_path, ticket="2026-04-22-002-x", assignee="research", body="x")
    with pytest.raises(ValueError) as ei:
        find_dispatch("research", tmp_path)
    assert "Multiple" in str(ei.value)


def test_humanize_age():
    assert humanize_age(45) == "45s"
    assert humanize_age(120) == "2m"
    assert humanize_age(3600) == "1h"
    assert humanize_age(3 * 86400) == "3d"


def test_list_dispatches_ignores_non_dispatch_files(tmp_path):
    """Files that don't match the livery-dispatch-*.out pattern are skipped."""
    (tmp_path / "random.txt").write_text("nope")
    (tmp_path / "livery-dispatch-real-a.out").write_text("yes")
    views = list_dispatches(tmp_path)
    assert len(views) == 1
    assert views[0].label == "real-a"


# -----------------------------------------------------------------------------
# Attempt-backed views + the three compatibility rules from the signed plan
# -----------------------------------------------------------------------------


def _make_workspace(tmp_path: Path, with_livery_toml: bool = True) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    if with_livery_toml:
        (root / "livery.toml").write_text('name = "ws"\n')
    return root


def _stub_attempt_for(workspace_root: Path, *, ticket_id: str, assignee: str, output_path: Path):
    """Helper: write an attempt JSON at the canonical location, return it."""
    from livery.attempts import (
        SCHEMA_VERSION,
        AttemptStatus,
        DispatchAttempt,
        attempt_id_for,
        now_iso,
        write_attempt,
    )
    attempt = DispatchAttempt(
        schema_version=SCHEMA_VERSION,
        attempt_id=attempt_id_for(ticket_id, assignee),
        ticket_id=ticket_id,
        assignee=assignee,
        runtime="codex",
        model="gpt-5-codex",
        workspace_root=str(workspace_root),
        agent_cwd="/tmp/repo",
        worktree_path=None,
        prompt_path=str(output_path).replace(".out", ".txt"),
        output_path=str(output_path),
        command="codex exec ...",
        pid=None,
        started_at=now_iso(),
        finished_at=None,
        exit_code=None,
        status=AttemptStatus.PREPARED,
        failure_class=None,
        failure_detail=None,
        summary_excerpt=[],
        hooks={},
        hook_warnings=[],
    )
    write_attempt(attempt, workspace_root)
    return attempt


def test_compat_rule_new_dispatches_attempt_json_canonical(tmp_path):
    """Compatibility rule 1: new dispatches → attempt JSON canonical."""
    from livery.attempts import AttemptStatus

    workspace = _make_workspace(tmp_path)
    output_dir = tmp_path / "tmp"
    output_dir.mkdir()
    out_path = output_dir / "livery-dispatch-2026-05-07-001-x-research.out"

    attempt = _stub_attempt_for(
        workspace,
        ticket_id="2026-05-07-001-x",
        assignee="research",
        output_path=out_path,
    )
    # No /tmp output file written yet — this is a freshly-prepped attempt.

    views = list_dispatches(output_dir, workspace_root=workspace)
    assert len(views) == 1
    v = views[0]
    # Attempt-backed view: has attempt metadata, label from attempt
    assert v.attempt is not None
    assert v.attempt.attempt_id == attempt.attempt_id
    assert v.label == "2026-05-07-001-x-research"
    assert v.attempt.status == AttemptStatus.PREPARED


def test_compat_rule_old_dispatches_output_scanning_only(tmp_path):
    """Compatibility rule 2: old / manually-launched (no attempt) → output scanning."""
    workspace = _make_workspace(tmp_path)
    output_dir = tmp_path / "tmp"
    output_dir.mkdir()
    # Manually-launched dispatch — only output file, no attempt JSON
    out_path = output_dir / "livery-dispatch-legacy-001-old-agent.out"
    out_path.write_text("some output\n")

    views = list_dispatches(output_dir, workspace_root=workspace)
    assert len(views) == 1
    v = views[0]
    # No attempt → fall back to legacy state-from-file
    assert v.attempt is None
    assert v.label == "legacy-001-old-agent"


def test_compat_rule_both_exist_attempt_wins(tmp_path):
    """Compatibility rule 3: both attempt and /tmp output exist for same label →
    attempt wins; output tail fills missing summary/last-line."""
    workspace = _make_workspace(tmp_path)
    output_dir = tmp_path / "tmp"
    output_dir.mkdir()
    out_path = output_dir / "livery-dispatch-2026-05-07-001-x-research.out"

    # Both: attempt record AND output file
    _stub_attempt_for(
        workspace,
        ticket_id="2026-05-07-001-x",
        assignee="research",
        output_path=out_path,
    )
    out_path.write_text(
        "working...\n"
        "=== DISPATCH_SUMMARY ===\n"
        "Status: done\n"
        "Summary: did the thing\n"
        "=== END DISPATCH_SUMMARY ===\n"
    )

    views = list_dispatches(output_dir, workspace_root=workspace)

    # Only ONE view for this label — attempt wins, /tmp gets deduplicated.
    matching = [v for v in views if v.label == "2026-05-07-001-x-research"]
    assert len(matching) == 1
    v = matching[0]
    # Attempt is the source
    assert v.attempt is not None
    # But the output tail filled in the summary excerpt
    assert any("Status: done" in line for line in v.summary_excerpt)


def test_inference_prepared_with_summary_displays_succeeded(tmp_path):
    """Read-time inference: PREPARED + DISPATCH_SUMMARY in output → display SUCCEEDED."""
    from livery.attempts import AttemptStatus

    workspace = _make_workspace(tmp_path)
    output_dir = tmp_path / "tmp"
    output_dir.mkdir()
    out_path = output_dir / "livery-dispatch-2026-05-07-001-x-research.out"

    _stub_attempt_for(
        workspace,
        ticket_id="2026-05-07-001-x",
        assignee="research",
        output_path=out_path,
    )
    out_path.write_text(
        "=== DISPATCH_SUMMARY ===\nStatus: done\n=== END DISPATCH_SUMMARY ===\n"
    )

    views = list_dispatches(output_dir, workspace_root=workspace)
    v = views[0]
    # Stored status remains PREPARED (we never write back at status time)
    assert v.attempt.status == AttemptStatus.PREPARED
    # But inferred_status is SUCCEEDED for display
    assert v.inferred_status == AttemptStatus.SUCCEEDED


def test_inference_prepared_no_output_stays_prepared(tmp_path):
    """Read-time inference: PREPARED + no output file → display PREPARED (waiting on user)."""
    from livery.attempts import AttemptStatus

    workspace = _make_workspace(tmp_path)
    output_dir = tmp_path / "tmp"
    output_dir.mkdir()
    out_path = output_dir / "livery-dispatch-2026-05-07-001-x-research.out"
    # Note: no out_path.write_text — output file doesn't exist

    _stub_attempt_for(
        workspace,
        ticket_id="2026-05-07-001-x",
        assignee="research",
        output_path=out_path,
    )

    views = list_dispatches(output_dir, workspace_root=workspace)
    v = views[0]
    # Inference returns PREPARED (no output to base SUCCEEDED on)
    assert v.inferred_status == AttemptStatus.PREPARED


def test_inference_only_fires_for_prepared_status(tmp_path):
    """Read-time inference: a SUCCEEDED attempt is not re-inferred (status sticks)."""
    from livery.attempts import (
        AttemptStatus,
        attempt_id_for,
        attempts_dir,
        load_attempt,
        write_attempt,
    )

    workspace = _make_workspace(tmp_path)
    output_dir = tmp_path / "tmp"
    output_dir.mkdir()
    out_path = output_dir / "livery-dispatch-2026-05-07-001-x-research.out"

    attempt = _stub_attempt_for(
        workspace,
        ticket_id="2026-05-07-001-x",
        assignee="research",
        output_path=out_path,
    )
    # Simulate a finished dispatch — status is SUCCEEDED, not PREPARED
    attempt.status = AttemptStatus.SUCCEEDED
    write_attempt(attempt, workspace)

    out_path.write_text(
        "=== DISPATCH_SUMMARY ===\nStatus: done\n=== END DISPATCH_SUMMARY ===\n"
    )

    views = list_dispatches(output_dir, workspace_root=workspace)
    v = views[0]
    # No inference because status was already SUCCEEDED (not PREPARED)
    assert v.attempt.status == AttemptStatus.SUCCEEDED
    assert v.inferred_status is None


def test_list_dispatches_no_workspace_falls_back_to_tmp_only(tmp_path):
    """workspace_root=None → only /tmp scan, no attempts read. Old behavior."""
    output_dir = tmp_path / "tmp"
    output_dir.mkdir()
    (output_dir / "livery-dispatch-foo-bar.out").write_text("hi\n")

    views = list_dispatches(output_dir, workspace_root=None)
    assert len(views) == 1
    assert views[0].attempt is None
