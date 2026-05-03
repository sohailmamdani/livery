"""`livery upgrade-workspace` — refresh framework-managed scaffolding.

After a Livery upgrade (`uv tool upgrade livery`), existing workspaces
may be missing files that newer Livery versions scaffold, or may have
stale framework-managed content. This command compares the workspace's
current state to what `livery init` would produce today and offers to
update the framework-managed parts only.

Hard guardrails:

- **Never touches user content.** `livery.toml`, `agents/`, `tickets/`,
  and the user-editable sections of CLAUDE.md / AGENTS.md (everything
  outside the LIVERY-MANAGED markers) are off-limits regardless.
- **Dry-run by default.** Pass `--apply` to actually write changes.
- **Skill files only created if missing.** Existing skill files aren't
  overwritten — users may have customized them. The upgrade adds new
  skills shipped by the framework.

The framework-managed parts are:

- The `LIVERY-MANAGED` block at the top of each engine's convention
  file (CLAUDE.md, AGENTS.md, etc.) — fully regenerated to match
  current shipped content.
- Skill / slash-command files for each engine declared in
  `livery.toml`'s `cos_engines` — added when missing.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .cos_engines import (
    COS_ENGINES,
    convention_files_for,
    find_managed_block,
    resolve_engines,
    wrap_managed,
)

# When a legacy workspace (no `cos_engines` field in livery.toml) is upgraded,
# this is the engine list it migrates onto. "Full benefits" — every engine
# Livery currently ships with. Users can trim afterward by editing
# livery.toml; subsequent upgrades respect whatever they leave there.
ALL_SUPPORTED_ENGINES: list[str] = list(COS_ENGINES.keys())
from .init import (
    COS_MANAGED_BLOCK,
    COS_USER_TEMPLATE,
    NEW_TICKET_SKILL,
    TICKET_SLASH,
)


class Action(Enum):
    SKIP = "skip"          # nothing to do
    CREATE = "create"      # file missing → write fresh
    REFRESH = "refresh"    # managed block exists but content drifted → rewrite block only
    INSERT = "insert"      # convention file exists with no markers → prepend a managed block
    MIGRATE = "migrate"    # legacy livery.toml gets a `cos_engines` field appended
    WARN = "warn"          # framework-managed file customized; skip without --force


@dataclass(slots=True)
class PlanItem:
    path: Path
    action: Action
    reason: str
    new_content: str | None = None  # full file content to write, when action != SKIP/WARN


@dataclass(slots=True)
class UpgradePlan:
    workspace_root: Path
    cos_engines: list[str]
    items: list[PlanItem]

    @property
    def has_changes(self) -> bool:
        return any(i.action != Action.SKIP for i in self.items)


def _read_workspace_meta(root: Path) -> tuple[list[str], str, str, bool]:
    """Pull cos_engines + name + description from livery.toml.

    Returns (engine_ids, name, description, is_legacy).

    `is_legacy` is True when livery.toml has no `cos_engines` field — these
    workspaces predate v0.5.0 and need a one-time migration. For them we
    return ALL_SUPPORTED_ENGINES so upgrade-workspace scaffolds files for
    every engine the framework currently supports ("full benefits"), and
    the caller writes `cos_engines` back to livery.toml so the migration
    is one-time.
    """
    toml_path = root / "livery.toml"
    name = root.name
    description = ""
    engines: list[str] = []
    is_legacy = True

    if toml_path.is_file():
        raw = tomllib.loads(toml_path.read_text())
        name = str(raw.get("name") or root.name)
        description = str(raw.get("description") or "")
        raw_engines = raw.get("cos_engines")
        if isinstance(raw_engines, list) and raw_engines:
            engines = resolve_engines([str(e) for e in raw_engines])
            is_legacy = False

    if not engines:
        engines = list(ALL_SUPPORTED_ENGINES)

    return engines, name, description, is_legacy


def _plan_toml_migration(toml_path: Path, engines: list[str]) -> PlanItem:
    """Append `cos_engines = [...]` to a legacy livery.toml.

    TOML quirk: top-level keys must come *before* any [section] tables.
    livery-im-style configs already have a [telegram] table, so simple
    append-to-end would put the new key inside that table. We insert
    before the first `[section]` line if one exists, else append at end.
    """
    body = toml_path.read_text()
    lines = body.splitlines(keepends=True)

    insert_at = len(lines)
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        # Match a top-level table header. `[[array]]` headers count too.
        if stripped.startswith("[") and not stripped.startswith("[["):
            insert_at = i
            break
        if stripped.startswith("[["):
            insert_at = i
            break

    quoted = ", ".join(f'"{e}"' for e in engines)
    new_lines = [
        "\n",
        "# Added by `livery upgrade-workspace` migrating a legacy workspace.\n"
        "# Edit this list to control which CoS engines this workspace targets.\n"
        "# Removing an engine here means subsequent `upgrade-workspace` runs\n"
        "# stop scaffolding files for it (existing files are not deleted).\n",
        f"cos_engines = [{quoted}]\n",
        "\n",
    ]

    new_body = "".join(lines[:insert_at] + new_lines + lines[insert_at:])

    return PlanItem(
        path=toml_path,
        action=Action.MIGRATE,
        reason=f"legacy workspace — migrating to declare cos_engines = [{quoted}]",
        new_content=new_body,
    )


def _refresh_in_place(content: str, fresh_managed_block: str) -> str:
    """Return `content` with its LIVERY-MANAGED block replaced by the current
    framework block (or a fresh block prepended if no markers were present).
    """
    fresh_wrapped = wrap_managed(fresh_managed_block)
    block_range = find_managed_block(content)
    if block_range is None:
        return fresh_wrapped + "\n" + content
    start, end = block_range
    return content[:start] + fresh_wrapped.rstrip() + content[end:]


def _plan_convention_file(
    path: Path,
    fresh_managed_block: str,
    workspace_name: str,
    workspace_description: str,
    sibling_paths: list[Path],
) -> PlanItem:
    """Decide what to do with a convention file (CLAUDE.md, AGENTS.md, ...).

    When the file is missing AND another convention file (sibling) already
    exists on disk, mirror the sibling's user content into the new file —
    with the framework's managed block refreshed to current. This is what
    makes "add Codex to an existing claude_code-only workspace" produce an
    AGENTS.md that actually has the user's CLAUDE.md customizations, rather
    than a bare template.
    """
    if not path.exists():
        # Look for a sibling convention file already on disk to mirror from.
        # `sibling_paths` is every convention file path the workspace targets;
        # we exclude `path` itself and pick the first one that exists.
        mirror_source = next(
            (p for p in sibling_paths if p != path and p.is_file()),
            None,
        )
        if mirror_source is not None:
            mirrored = _refresh_in_place(mirror_source.read_text(), fresh_managed_block)
            return PlanItem(
                path=path,
                action=Action.CREATE,
                reason=f"convention file missing — mirroring from {mirror_source.name} (framework block refreshed)",
                new_content=mirrored,
            )

        # No sibling to mirror — write the fresh template (init-style).
        user_body = COS_USER_TEMPLATE.format(
            name=workspace_name,
            description=workspace_description or "(Describe your workspace here.)",
        )
        full = wrap_managed(fresh_managed_block) + "\n" + user_body
        return PlanItem(
            path=path,
            action=Action.CREATE,
            reason="convention file missing — will create with fresh framework block + user-editable template",
            new_content=full,
        )

    current = path.read_text()
    block_range = find_managed_block(current)
    fresh_wrapped = wrap_managed(fresh_managed_block)

    if block_range is None:
        # Pre-existing user-owned file; prepend a managed block.
        new = fresh_wrapped + "\n" + current
        return PlanItem(
            path=path,
            action=Action.INSERT,
            reason="no LIVERY-MANAGED markers found — will prepend framework block above existing content",
            new_content=new,
        )

    start, end = block_range
    existing_block = current[start:end] + "\n"  # include trailing newline for compare
    if existing_block == fresh_wrapped:
        return PlanItem(path=path, action=Action.SKIP, reason="framework block already current")

    new = current[:start] + fresh_wrapped.rstrip() + current[end:]
    return PlanItem(
        path=path,
        action=Action.REFRESH,
        reason="framework block out of date — will rewrite (user content outside markers preserved)",
        new_content=new,
    )


def _plan_skill_file(path: Path, content: str) -> PlanItem:
    """Decide what to do with a framework-shipped skill / command file."""
    if not path.exists():
        return PlanItem(
            path=path,
            action=Action.CREATE,
            reason="skill missing — will create",
            new_content=content,
        )
    if path.read_text() == content:
        return PlanItem(path=path, action=Action.SKIP, reason="up to date")
    return PlanItem(
        path=path,
        action=Action.WARN,
        reason="exists but differs from current shipped version — likely customized; skipping (use --force to overwrite)",
    )


def compute_plan(root: Path) -> UpgradePlan:
    """Build the full upgrade plan for `root`. Pure: no file writes."""
    engine_ids, name, description, is_legacy = _read_workspace_meta(root)
    items: list[PlanItem] = []

    # Legacy workspace: gets a one-time migration to declare cos_engines.
    # We list this first so dry-run output makes the migration obvious.
    toml_path = root / "livery.toml"
    if is_legacy and toml_path.is_file():
        items.append(_plan_toml_migration(toml_path, engine_ids))

    convention_filenames = convention_files_for(engine_ids)
    convention_paths = [root / fn for fn in convention_filenames]
    for path in convention_paths:
        items.append(_plan_convention_file(
            path, COS_MANAGED_BLOCK, name, description, convention_paths,
        ))

    for eid in engine_ids:
        engine = COS_ENGINES[eid]
        if engine.commands_dir:
            items.append(_plan_skill_file(
                root / engine.commands_dir / "ticket.md",
                TICKET_SLASH,
            ))
        if engine.skills_dir:
            items.append(_plan_skill_file(
                root / engine.skills_dir / "new-ticket" / "SKILL.md",
                NEW_TICKET_SKILL,
            ))

    return UpgradePlan(workspace_root=root, cos_engines=engine_ids, items=items)


def _user_content_score(path: Path) -> tuple[int, float]:
    """Score how 'user-edited' a convention file looks.

    Returns (substantive_chars, mtime). Higher tuple wins.

    `substantive_chars` is the byte-length of everything *outside* the
    LIVERY-MANAGED block AND outside the framework's bare user template
    (`COS_USER_TEMPLATE` content). Files that contain only the bare
    template score 0 — they have no user content. mtime is the tiebreaker
    when two files have identical user content (rare; usually the result
    of a recent sync).
    """
    try:
        text = path.read_text()
    except OSError:
        return (0, 0.0)

    # Strip the managed block (whatever's between LIVERY-MANAGED markers).
    block_range = find_managed_block(text)
    if block_range is not None:
        start, end = block_range
        outside_managed = text[:start] + text[end:]
    else:
        outside_managed = text

    # Strip whitespace-only differences for a stable comparison.
    normalized = "".join(outside_managed.split())

    # Compare against the bare template's normalized form. If the file's
    # outside-managed content matches the template byte-for-byte (ignoring
    # whitespace), it's a freshly-scaffolded file with no user edits.
    template_render = COS_USER_TEMPLATE.format(name="", description="")
    template_normalized = "".join(template_render.split())

    if normalized == template_normalized or template_normalized in normalized:
        substantive = max(0, len(normalized) - len(template_normalized))
    else:
        substantive = len(normalized)

    return (substantive, path.stat().st_mtime)


def compute_sync_plan(root: Path, source_filename: str | None = None) -> UpgradePlan:
    """Plan to mirror user content from one convention file to all its siblings.

    `source_filename` (e.g. "CLAUDE.md") picks the canonical file explicitly.
    If omitted, the framework picks automatically: the file with the most
    user content (outside the LIVERY-MANAGED block, ignoring the bare
    template) wins. mtime breaks ties.

    This deliberately ignores mtime as the primary signal. Mtime is creation
    time as much as edit time; a freshly-scaffolded file with the bare
    template would score newer than a long-edited file and clobber it.
    Content size is a much better proxy for "this is the canonical version."

    Skips entirely (returns no items) when fewer than two convention files
    exist — there's nothing to sync between.
    """
    engine_ids, _, _, _ = _read_workspace_meta(root)
    convention_paths = [root / fn for fn in convention_files_for(engine_ids)]
    existing = [p for p in convention_paths if p.is_file()]

    if len(existing) < 2:
        return UpgradePlan(workspace_root=root, cos_engines=engine_ids, items=[])

    if source_filename is not None:
        source = root / source_filename
        if source not in existing:
            available = ", ".join(p.name for p in existing)
            raise ValueError(
                f"--from {source_filename!r} doesn't match an existing convention file. "
                f"Available: {available}"
            )
    else:
        # Pick the file with the most user content. Ties broken by mtime.
        source = max(existing, key=_user_content_score)

    desired = _refresh_in_place(source.read_text(), COS_MANAGED_BLOCK)

    items: list[PlanItem] = []
    for target in existing:
        if target == source:
            continue
        current = target.read_text()
        if current == desired:
            items.append(PlanItem(
                path=target,
                action=Action.SKIP,
                reason=f"already in sync with {source.name}",
            ))
        else:
            items.append(PlanItem(
                path=target,
                action=Action.REFRESH,
                reason=f"syncing user content from {source.name} (and refreshing framework block)",
                new_content=desired,
            ))

    return UpgradePlan(workspace_root=root, cos_engines=engine_ids, items=items)


def apply_plan(plan: UpgradePlan, force: bool = False) -> list[PlanItem]:
    """Execute the plan. Returns the items that were actually written."""
    written: list[PlanItem] = []
    for item in plan.items:
        if item.action == Action.SKIP:
            continue
        if item.action == Action.WARN and not force:
            continue
        if item.new_content is None:
            # WARN with --force still has no content — skip; the warn case
            # without content means there's no canonical replacement to write.
            continue
        item.path.parent.mkdir(parents=True, exist_ok=True)
        item.path.write_text(item.new_content)
        written.append(item)
    return written
