from __future__ import annotations

from pathlib import Path

import pytest

from livery.paths_safety import (
    PathContainmentError,
    assert_path_contained,
    sanitize_path_component,
)


# -----------------------------------------------------------------------------
# sanitize_path_component
# -----------------------------------------------------------------------------


def test_sanitize_passes_clean_input_through():
    assert sanitize_path_component("ticket-001-fix-x") == "ticket-001-fix-x"
    assert sanitize_path_component("agent_id") == "agent_id"
    assert sanitize_path_component("v1.2.3") == "v1.2.3"


def test_sanitize_replaces_disallowed_with_underscore():
    assert sanitize_path_component("ticket id with spaces") == "ticket_id_with_spaces"
    assert sanitize_path_component("path/with/slashes") == "path_with_slashes"
    assert sanitize_path_component("name?query=x") == "name_query_x"


def test_sanitize_strips_leading_dots():
    """Leading dots could create hidden directories or escape via `..`."""
    assert sanitize_path_component("..") == "x"  # falls back when fully stripped
    assert sanitize_path_component(".") == "x"
    assert sanitize_path_component("...etc") == "etc"
    assert sanitize_path_component(".env") == "env"
    assert sanitize_path_component("..hidden") == "hidden"


def test_sanitize_handles_traversal_attempts():
    """Path-traversal payloads are neutralized."""
    assert "/" not in sanitize_path_component("../../etc/passwd")
    assert ".." not in sanitize_path_component("../escape")
    # Note: middle-of-string `..` like `a..b` survives as `a..b` because it's
    # not a leading dot. That's still safe — `a..b` is a literal directory
    # name, not a traversal token.
    assert sanitize_path_component("a..b") == "a..b"


def test_sanitize_handles_control_characters_and_nulls():
    assert sanitize_path_component("name\x00null") == "name_null"
    assert sanitize_path_component("name\nlinebreak") == "name_linebreak"
    assert sanitize_path_component("name\ttab") == "name_tab"


def test_sanitize_returns_fallback_when_input_fully_stripped():
    # Empty input → fallback
    assert sanitize_path_component("") == "x"
    # Pure dots strip to empty → fallback
    assert sanitize_path_component("...") == "x"
    # Disallowed-only stays as underscores (not empty, no fallback)
    assert sanitize_path_component("///") == "___"


def test_sanitize_idempotent():
    samples = [
        "normal",
        "weird name with spaces",
        "../traversal",
        ".env",
        "",
        "a..b",
    ]
    for s in samples:
        once = sanitize_path_component(s)
        twice = sanitize_path_component(once)
        assert once == twice, f"{s!r} not idempotent: {once!r} → {twice!r}"


def test_sanitize_rejects_non_string():
    with pytest.raises(TypeError):
        sanitize_path_component(123)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        sanitize_path_component(None)  # type: ignore[arg-type]


def test_sanitize_custom_fallback():
    assert sanitize_path_component("", fallback="default") == "default"
    assert sanitize_path_component("...", fallback="agent") == "agent"


# -----------------------------------------------------------------------------
# assert_path_contained
# -----------------------------------------------------------------------------


def test_contained_accepts_strict_subdirectory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    sub = root / "sub"
    resolved = assert_path_contained(sub, root)
    assert resolved == sub.resolve()


def test_contained_accepts_nested_subdirectory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    deep = root / "a" / "b" / "c"
    resolved = assert_path_contained(deep, root)
    assert resolved == deep.resolve()


def test_contained_rejects_path_equal_to_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PathContainmentError) as ei:
        assert_path_contained(root, root)
    assert "equals" in str(ei.value)


def test_contained_rejects_sibling(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    sibling = tmp_path / "sibling"
    with pytest.raises(PathContainmentError) as ei:
        assert_path_contained(sibling, root)
    assert "not contained" in str(ei.value)


def test_contained_rejects_parent_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    escape = root / ".." / "escape"
    with pytest.raises(PathContainmentError):
        assert_path_contained(escape, root)


def test_contained_rejects_absolute_path_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = Path("/tmp")
    with pytest.raises(PathContainmentError):
        assert_path_contained(outside, root)


def test_contained_resolves_symlinks_before_checking(tmp_path):
    """A symlink that points outside root is rejected even if its name is inside."""
    root = tmp_path / "root"
    root.mkdir()
    outside_target = tmp_path / "outside"
    outside_target.mkdir()

    sneaky = root / "looks-inside"
    sneaky.symlink_to(outside_target)

    with pytest.raises(PathContainmentError):
        assert_path_contained(sneaky, root)


def test_contained_works_when_path_does_not_yet_exist(tmp_path):
    """Worktrees haven't been created yet when we check — must work for non-existent paths."""
    root = tmp_path / "root"
    root.mkdir()
    not_yet = root / "future-worktree"
    # No mkdir — path doesn't exist
    assert not not_yet.exists()
    resolved = assert_path_contained(not_yet, root)
    assert resolved == not_yet.resolve()
