"""Read-side counterpart to dispatch.py.

Where `dispatch.py` *prepares* and *launches* dispatches, this module
*observes* their on-disk artifacts. Used by `livery dispatch status` and
`livery dispatch tail` to answer "is my dispatch still running, and
what's it doing?" without needing to remember the `/tmp/...` path.

Pure data — rendering happens in cli.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

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
    DONE = "done"        # DISPATCH_SUMMARY block present in the output
    ACTIVE = "active"    # no summary yet, but file was written recently
    STALE = "stale"      # no summary and file hasn't moved in a while


@dataclass(slots=True)
class DispatchView:
    path: Path                 # absolute path to the .out file
    label: str                 # everything between "livery-dispatch-" and ".out"
    state: DispatchState
    age_seconds: int           # since last mtime
    size_bytes: int
    last_line: str             # last non-blank line of the tail
    summary_excerpt: list[str] = field(default_factory=list)  # up to 5 lines from the DISPATCH_SUMMARY block


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


def list_dispatches(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[DispatchView]:
    """Scan `output_dir` for livery-dispatch-*.out files. Returns most-recent first."""
    if not output_dir.is_dir():
        return []
    views: list[DispatchView] = []
    now = datetime.now(timezone.utc).timestamp()
    for out_path in output_dir.glob("livery-dispatch-*.out"):
        try:
            stat = out_path.stat()
        except OSError:
            continue
        age_seconds = max(0, int(now - stat.st_mtime))
        last_line, summary = _read_tail(out_path)
        views.append(DispatchView(
            path=out_path,
            label=_parse_label(out_path),
            state=_classify(age_seconds, summary),
            age_seconds=age_seconds,
            size_bytes=stat.st_size,
            last_line=last_line,
            summary_excerpt=summary,
        ))
    views.sort(key=lambda v: v.age_seconds)
    return views


def find_dispatch(query: str, output_dir: Path = DEFAULT_OUTPUT_DIR) -> DispatchView:
    """Resolve a query to a single DispatchView. Raises ValueError on no/multi match.

    Match is a case-sensitive substring on the label (everything between
    `livery-dispatch-` and `.out` in the filename — i.e., `<ticket-id>-<assignee>`).
    """
    matches = [v for v in list_dispatches(output_dir) if query in v.label]
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
