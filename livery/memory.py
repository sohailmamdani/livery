"""File-backed workspace memory.

Memory entries are plain markdown files under ``<workspace>/memory/``.
They are intentionally boring: git-trackable, reviewable, and searchable
without a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import frontmatter


MEMORY_TYPE_DIRS: dict[str, str] = {
    "decision": "decisions",
    "lesson": "lessons",
    "preference": "preferences",
}
VALID_MEMORY_TYPES: tuple[str, ...] = tuple(MEMORY_TYPE_DIRS)


@dataclass(slots=True, frozen=True)
class MemoryEntry:
    id: str
    title: str
    type: str
    scope: str
    source_ticket: str | None
    created: str
    updated: str
    path: Path
    content: str


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "memory"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_memory_type(memory_type: str) -> str:
    normalized = memory_type.strip().lower()
    if normalized not in MEMORY_TYPE_DIRS:
        expected = ", ".join(VALID_MEMORY_TYPES)
        raise ValueError(f"memory type must be one of: {expected}")
    return normalized


def memory_scaffold_paths(root: Path) -> list[Path]:
    return [
        root / "memory" / dirname / ".gitkeep"
        for dirname in MEMORY_TYPE_DIRS.values()
    ]


def validate_memory_scaffold(root: Path) -> None:
    memory_root = root / "memory"
    if memory_root.exists() and not memory_root.is_dir():
        raise RuntimeError(f"{memory_root} exists but is not a directory.")
    for dirname in MEMORY_TYPE_DIRS.values():
        category = memory_root / dirname
        if category.exists() and not category.is_dir():
            raise RuntimeError(f"{category} exists but is not a directory.")


def ensure_memory_scaffold(root: Path) -> list[Path]:
    """Create the memory directory skeleton where it is safe to do so.

    Existing memory files are left alone. The returned paths are the
    ``.gitkeep`` sentinels newly written by this call.
    """
    validate_memory_scaffold(root)
    created: list[Path] = []
    for path in memory_scaffold_paths(root):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("")
            created.append(path)
    return created


def _memory_dir(root: Path, memory_type: str) -> Path:
    return root / "memory" / MEMORY_TYPE_DIRS[normalize_memory_type(memory_type)]


def _next_memory_id(root: Path, memory_type: str, title: str, now: str) -> str:
    date = now[:10]
    directory = _memory_dir(root, memory_type)
    counters: list[int] = []
    for path in directory.glob(f"{date}-*.md"):
        match = re.match(rf"{re.escape(date)}-(\d+)-", path.name)
        if match:
            counters.append(int(match.group(1)))
    counter = (max(counters) + 1) if counters else 1
    return f"{date}-{counter:03d}-{_slugify(title)}"


def _load_entry(path: Path) -> MemoryEntry:
    post = frontmatter.load(path)
    memory_type = str(post.get("type") or path.parent.name.rstrip("s"))
    return MemoryEntry(
        id=str(post.get("id") or path.stem),
        title=str(post.get("title") or path.stem),
        type=memory_type,
        scope=str(post.get("scope") or "workspace"),
        source_ticket=(
            str(post.get("source_ticket"))
            if post.get("source_ticket") is not None
            else None
        ),
        created=str(post.get("created") or ""),
        updated=str(post.get("updated") or ""),
        path=path,
        content=post.content,
    )


def create_memory_entry(
    *,
    root: Path,
    memory_type: str,
    title: str,
    body: str,
    scope: str = "workspace",
    source_ticket: str | None = None,
) -> MemoryEntry:
    memory_type = normalize_memory_type(memory_type)
    ensure_memory_scaffold(root)
    now = _now_iso()
    entry_id = _next_memory_id(root, memory_type, title, now)
    path = _memory_dir(root, memory_type) / f"{entry_id}.md"

    metadata: dict[str, str] = {
        "id": entry_id,
        "title": title,
        "type": memory_type,
        "scope": scope,
        "created": now,
        "updated": now,
    }
    if source_ticket:
        metadata["source_ticket"] = source_ticket

    note = body.strip() or "(none)"
    post = frontmatter.Post(f"## Note\n\n{note}\n", **metadata)
    path.write_text(frontmatter.dumps(post) + "\n")
    return _load_entry(path)


def iter_memory_entries(
    root: Path,
    memory_type: str | None = None,
) -> list[MemoryEntry]:
    validate_memory_scaffold(root)
    types = [normalize_memory_type(memory_type)] if memory_type else list(VALID_MEMORY_TYPES)
    entries: list[MemoryEntry] = []
    for type_name in types:
        directory = _memory_dir(root, type_name)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            entries.append(_load_entry(path))
    return sorted(entries, key=lambda entry: (entry.created, entry.id))


def search_memory_entries(
    root: Path,
    query: str,
    memory_type: str | None = None,
) -> list[MemoryEntry]:
    needle = query.lower()
    matches: list[MemoryEntry] = []
    for entry in iter_memory_entries(root, memory_type=memory_type):
        haystack = "\n".join(
            [
                entry.id,
                entry.title,
                entry.type,
                entry.scope,
                entry.source_ticket or "",
                entry.content,
            ]
        ).lower()
        if needle in haystack:
            matches.append(entry)
    return matches


def find_memory_entries(root: Path, query: str) -> list[MemoryEntry]:
    needle = query.lower()
    matches: list[MemoryEntry] = []
    for entry in iter_memory_entries(root):
        if (
            needle in entry.id.lower()
            or needle in entry.title.lower()
            or needle in entry.path.stem.lower()
        ):
            matches.append(entry)
    return matches
