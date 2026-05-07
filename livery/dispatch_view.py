"""Read-side counterpart to dispatch.py.

Where `dispatch.py` *prepares* and *launches* dispatches, this module
*observes* their on-disk artifacts. Used by `livery dispatch status` and
`livery dispatch tail` to answer "is my dispatch still running, and
what's it doing?" without needing to remember the `/tmp/...` path.

Two data sources, one unified view:

1. **Attempt JSON** at `<workspace>/.livery/dispatch/attempts/*.json`
   (since v0.9). Canonical metadata when present — pid, lifecycle
   timestamps, structured failure class, etc.
2. **`/tmp/livery-dispatch-*.out`** output files (the historical scheme).
   Fallback for legacy and manually-launched dispatches.

Compatibility contract (from the Claude+Codex signed plan):
- new dispatches → attempt JSON canonical
- old / manually-launched (no attempt JSON) → output scanning
- both exist for same label → JSON wins; output tail fills missing
  summary/last-line on the JSON-derived view

Pure data — rendering happens in cli.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .attempts import (
    AttemptStatus,
    DispatchAttempt,
    list_attempts,
)

DEFAULT_OUTPUT_DIR = Path("/tmp")
SUMMARY_BEGIN = "=== DISPATCH_SUMMARY ==="
SUMMARY_END = "=== END DISPATCH_SUMMARY ==="

# Tail buffer for parsing — large enough to catch the summary block,
# small enough to avoid loading multi-MB output files.
TAIL_BYTES = 8192

# Window where a dispatch with no DISPATCH_SUMMARY is considered "active"
# (still writing) vs "stale" (probably crashed or stuck). Models can
# pause for a long time on a single thought; 5 minutes is forgiving.
ACTIVE_THRESHOLD_SECONDS = 300


class DispatchState(Enum):
    """Legacy file-scanning-only state classification. Kept for /tmp fallback
    views. Newer attempt-backed views carry an AttemptStatus instead via
    `DispatchView.attempt.status` or `inferred_status`."""

    DONE = "done"        # DISPATCH_SUMMARY block present in the output
    ACTIVE = "active"    # no summary yet, but file was written recently
    STALE = "stale"      # no summary and file hasn't moved in a while


@dataclass(slots=True)
class DispatchView:
    path: Path | None          # output file path; None if attempt has no output yet
    label: str                 # canonical "<ticket-id>-<assignee>" identifier
    state: DispatchState
    age_seconds: int           # since last mtime (output file or attempt write)
    size_bytes: int
    last_line: str             # last non-blank line of the tail
    summary_excerpt: list[str] = field(default_factory=list)  # up to 5 lines from the DISPATCH_SUMMARY block

    # Attempt-backed extras (v0.9+). When `attempt is not None`, prefer it
    # for status / pid / failure_class display; the legacy `state` field
    # remains a coarse fallback.
    attempt: DispatchAttempt | None = None
    inferred_status: AttemptStatus | None = None
    """Read-time status inference for PREPARED attempts based on their
    output file. None when no inference applied. Display-only — never
    written back to the attempt JSON."""


def _parse_label(out_path: Path) -> str:
    name = out_path.name
    prefix, suffix = "livery-dispatch-", ".out"
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix):-len(suffix)]
    return out_path.stem


def _read_tail(path: Path) -> tuple[str, list[str]]:
    """Read the trailing TAIL_BYTES of the file. Returns (last_line, summary_excerpt).

    The summary excerpt is up to the first 5 non-blank lines inside the
    DISPATCH_SUMMARY block. Never returns the full output — keeps memory bounded.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read()
    except OSError:
        return "", []

    text = raw.decode("utf-8", errors="replace")
    non_blank = [line for line in text.splitlines() if line.strip()]
    last_line = non_blank[-1] if non_blank else ""

    summary: list[str] = []
    begin_idx = text.find(SUMMARY_BEGIN)
    if begin_idx != -1:
        end_idx = text.find(SUMMARY_END, begin_idx)
        if end_idx == -1:
            end_idx = len(text)
        block = text[begin_idx + len(SUMMARY_BEGIN):end_idx].splitlines()
        summary = [line for line in block if line.strip()][:5]

    return last_line, summary


def _classify(age_seconds: int, summary_excerpt: list[str]) -> DispatchState:
    if summary_excerpt:
        return DispatchState.DONE
    if age_seconds <= ACTIVE_THRESHOLD_SECONDS:
        return DispatchState.ACTIVE
    return DispatchState.STALE


def infer_displayed_status(
    attempt: DispatchAttempt,
    *,
    output_path: Path | None = None,
    age_seconds: int | None = None,
    summary_present: bool | None = None,
) -> AttemptStatus:
    """Decide what status to show for `attempt` given its output file's state.

    Pure read-time inference — never writes back to disk. Only fires for
    PREPARED attempts; everything else returns the attempt's own status.

    PREPARED + summary present → SUCCEEDED (`dispatch prep` mode where the
        user manually ran the command and the agent finished cleanly)
    PREPARED + output exists but stale (no summary, mtime > threshold)
        → STALE (probably crashed or operator forgot it)
    PREPARED + no output / fresh output → PREPARED (waiting for human)
    """
    if attempt.status != AttemptStatus.PREPARED:
        return attempt.status

    if output_path is None:
        # Caller didn't provide signals — derive from path on attempt
        try:
            output_path = Path(attempt.output_path)
        except (TypeError, ValueError):
            return AttemptStatus.PREPARED

    if not output_path.is_file():
        return AttemptStatus.PREPARED

    if summary_present is None or age_seconds is None:
        # Compute on demand
        last_line, summary = _read_tail(output_path)
        summary_present = bool(summary)
        try:
            mtime = output_path.stat().st_mtime
            now = datetime.now(timezone.utc).timestamp()
            age_seconds = max(0, int(now - mtime))
        except OSError:
            age_seconds = 0

    if summary_present:
        return AttemptStatus.SUCCEEDED
    if age_seconds > ACTIVE_THRESHOLD_SECONDS:
        return AttemptStatus.STALE
    return AttemptStatus.PREPARED


def _state_from_attempt_status(status: AttemptStatus) -> DispatchState:
    """Map the richer AttemptStatus down to legacy DispatchState for the
    `state` field. Lossy on purpose — the caller has the richer info via
    `view.attempt.status` if they want it."""
    if status in (AttemptStatus.SUCCEEDED, AttemptStatus.FAILED, AttemptStatus.BLOCKED, AttemptStatus.CANCELLED):
        return DispatchState.DONE
    if status == AttemptStatus.STALE:
        return DispatchState.STALE
    return DispatchState.ACTIVE


def _view_from_attempt(attempt: DispatchAttempt, attempt_path: Path) -> DispatchView:
    """Build a DispatchView from an attempt JSON record. Output file content
    fills in last_line / summary_excerpt when available; if the file is
    missing, those stay empty. Status is inferred for PREPARED records."""
    output_path = Path(attempt.output_path)
    has_output = output_path.is_file()

    if has_output:
        try:
            mtime = output_path.stat().st_mtime
            size = output_path.stat().st_size
        except OSError:
            mtime = attempt_path.stat().st_mtime
            size = 0
        last_line, summary = _read_tail(output_path)
    else:
        # No output file — fall back to attempt JSON's mtime + summary_excerpt
        try:
            mtime = attempt_path.stat().st_mtime
        except OSError:
            mtime = datetime.now(timezone.utc).timestamp()
        size = 0
        last_line = ""
        summary = list(attempt.summary_excerpt)

    now = datetime.now(timezone.utc).timestamp()
    age_seconds = max(0, int(now - mtime))

    inferred = None
    if attempt.status == AttemptStatus.PREPARED:
        inferred = infer_displayed_status(
            attempt,
            output_path=output_path if has_output else None,
            age_seconds=age_seconds,
            summary_present=bool(summary),
        )

    effective_status = inferred or attempt.status

    return DispatchView(
        path=output_path if has_output else None,
        label=f"{attempt.ticket_id}-{attempt.assignee}",
        state=_state_from_attempt_status(effective_status),
        age_seconds=age_seconds,
        size_bytes=size,
        last_line=last_line,
        summary_excerpt=summary,
        attempt=attempt,
        inferred_status=inferred,
    )


def list_dispatches(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    workspace_root: Path | None = None,
) -> list[DispatchView]:
    """Return DispatchViews for every dispatch the framework can see.

    When `workspace_root` is provided, attempt JSON records are read first
    and become the canonical source. The /tmp output directory is then
    scanned for any /tmp-only labels (legacy or manually-launched dispatches
    with no attempt) and those are appended.

    When `workspace_root` is None, only /tmp output scanning runs (the
    old behavior). Most callers should pass workspace_root.

    Sorted most-recent-first.
    """
    views: list[DispatchView] = []
    seen_labels: set[str] = set()

    # Pass 1: attempt-backed views, when we have a workspace.
    if workspace_root is not None:
        for attempt in list_attempts(workspace_root):
            from .attempts import attempts_dir
            attempt_path = attempts_dir(workspace_root) / f"{attempt.attempt_id}.json"
            view = _view_from_attempt(attempt, attempt_path)
            views.append(view)
            seen_labels.add(view.label)

    # Pass 2: /tmp output-file scanning. Labels already covered by an
    # attempt are skipped (attempt wins per the compatibility contract).
    if output_dir.is_dir():
        now = datetime.now(timezone.utc).timestamp()
        for out_path in output_dir.glob("livery-dispatch-*.out"):
            label = _parse_label(out_path)
            if label in seen_labels:
                continue
            try:
                stat = out_path.stat()
            except OSError:
                continue
            age_seconds = max(0, int(now - stat.st_mtime))
            last_line, summary = _read_tail(out_path)
            views.append(DispatchView(
                path=out_path,
                label=label,
                state=_classify(age_seconds, summary),
                age_seconds=age_seconds,
                size_bytes=stat.st_size,
                last_line=last_line,
                summary_excerpt=summary,
            ))
            seen_labels.add(label)

    views.sort(key=lambda v: v.age_seconds)
    return views


def find_dispatch(
    query: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    workspace_root: Path | None = None,
) -> DispatchView:
    """Resolve a query to a single DispatchView. Raises ValueError on no/multi match.

    Match is a case-sensitive substring on the label (`<ticket-id>-<assignee>`).
    `workspace_root`, if given, gets attempt-backed views included in the search.
    """
    matches = [v for v in list_dispatches(output_dir, workspace_root=workspace_root) if query in v.label]
    if not matches:
        raise ValueError(
            f"No dispatch matching {query!r} in {output_dir}"
        )
    if len(matches) > 1:
        labels = ", ".join(v.label for v in matches)
        raise ValueError(
            f"Multiple dispatches match {query!r}: {labels}. Be more specific."
        )
    return matches[0]


def humanize_age(seconds: int) -> str:
    """'45s', '12m', '3h', '2d' — pick the largest unit ≥ 1."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"
