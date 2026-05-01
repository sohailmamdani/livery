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
