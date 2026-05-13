from __future__ import annotations

from datetime import datetime, timezone

import pytest

from livery.walkie import (
    FRONTMATTER_MARKER,
    WALKIE_PROTOCOL_RULES,
    list_walkies,
    new_walkie,
    parse_walkie,
    walkie_dir,
)


# -----------------------------------------------------------------------------
# new_walkie — file scaffolding
# -----------------------------------------------------------------------------


def test_new_walkie_creates_file_with_protocol_baked_in(tmp_path):
    path = new_walkie(workspace_root=tmp_path, topic="dispatch lifecycle")
    assert path.exists()
    text = path.read_text()
    # Frontmatter marker is present so the framework can identify these
    assert f"livery: {FRONTMATTER_MARKER}" in text
    # Protocol rules are inline — single source of truth for both AIs
    assert "Protocol — read before every turn" in text
    assert "Append, never prepend" in text
    assert "SIGNED:" in text


def test_new_walkie_filename_is_slugified(tmp_path):
    path = new_walkie(workspace_root=tmp_path, topic="Dispatch Lifecycle!!")
    assert path.name == "dispatch-lifecycle.md"


def test_new_walkie_creates_walkie_dir(tmp_path):
    new_walkie(workspace_root=tmp_path, topic="test")
    assert walkie_dir(tmp_path).is_dir()


def test_new_walkie_refuses_to_overwrite(tmp_path):
    new_walkie(workspace_root=tmp_path, topic="topic-x")
    with pytest.raises(FileExistsError):
        new_walkie(workspace_root=tmp_path, topic="topic-x")


def test_new_walkie_with_opener_writes_turn_one(tmp_path):
    path = new_walkie(
        workspace_root=tmp_path,
        topic="t",
        opener="Here's my proposal: do X.",
        initiator="claude-code",
    )
    text = path.read_text()
    assert "## Turn 1 — claude-code — " in text
    assert "Here's my proposal: do X." in text


def test_new_walkie_no_opener_skips_turn_one(tmp_path):
    path = new_walkie(workspace_root=tmp_path, topic="t")
    text = path.read_text()
    assert "## Turn 1" not in text


def test_new_walkie_records_started_timestamp(tmp_path):
    fixed = datetime(2026, 5, 7, 12, 34, 56, tzinfo=timezone.utc)
    path = new_walkie(workspace_root=tmp_path, topic="t", when=fixed)
    text = path.read_text()
    assert "started: 2026-05-07T12:34:56Z" in text


def test_new_walkie_quotes_tricky_topic_in_yaml(tmp_path):
    """A topic with a colon in it must not break frontmatter parsing —
    the YAML scalar gets quoted."""
    path = new_walkie(workspace_root=tmp_path, topic="auth: rewrite plan")
    parsed = parse_walkie(path)
    assert parsed.topic == "auth: rewrite plan"


# -----------------------------------------------------------------------------
# parse_walkie — turns + signatures + locked state
# -----------------------------------------------------------------------------


def test_parse_empty_walkie_has_no_turns(tmp_path):
    path = new_walkie(workspace_root=tmp_path, topic="t")
    parsed = parse_walkie(path)
    assert parsed.turns == []
    assert parsed.signatures == []
    assert parsed.is_locked is False
    assert parsed.next_turn_n == 1


def test_parse_walkie_with_opener_has_one_turn(tmp_path):
    path = new_walkie(
        workspace_root=tmp_path, topic="t", opener="hi", initiator="claude",
    )
    parsed = parse_walkie(path)
    assert len(parsed.turns) == 1
    assert parsed.turns[0].peer == "claude"
    assert parsed.turns[0].n == 1
    assert "hi" in parsed.turns[0].body
    assert parsed.next_turn_n == 2


def test_parse_walkie_counts_multiple_appended_turns(tmp_path):
    path = new_walkie(workspace_root=tmp_path, topic="t", opener="round 1", initiator="claude")
    # Simulate the peer appending their turn
    appended = (
        "\n## Turn 2 — codex — 2026-05-07T12:35:30Z\n\n"
        "round 2 — i disagree on point 3 because...\n\n"
    )
    # Insert before the protocol section to mimic real-world usage
    text = path.read_text()
    marker = "<!-- LIVERY-WALKIE-TALKIE PROTOCOL"
    idx = text.find(marker)
    new_text = text[:idx] + appended + text[idx:]
    path.write_text(new_text)

    parsed = parse_walkie(path)
    assert len(parsed.turns) == 2
    assert [t.n for t in parsed.turns] == [1, 2]
    assert parsed.peers == {"claude", "codex"}
    assert parsed.next_turn_n == 3


def test_parse_walkie_detects_signatures(tmp_path):
    path = new_walkie(workspace_root=tmp_path, topic="t", opener="round 1", initiator="claude")
    text = path.read_text()
    appended = (
        "\n## Turn 2 — codex — 2026-05-07T12:35:30Z\n\n"
        "agree.\n\nSIGNED: codex @ 2026-05-07T12:35:30Z\n\n"
        "## Turn 3 — claude — 2026-05-07T12:36:00Z\n\n"
        "agree too.\n\nSIGNED: claude @ 2026-05-07T12:36:00Z\n\n"
    )
    marker = "<!-- LIVERY-WALKIE-TALKIE PROTOCOL"
    idx = text.find(marker)
    path.write_text(text[:idx] + appended + text[idx:])

    parsed = parse_walkie(path)
    signed = {s.peer for s in parsed.signatures}
    assert signed == {"claude", "codex"}


def test_parse_walkie_locked_only_when_all_peers_signed(tmp_path):
    path = new_walkie(workspace_root=tmp_path, topic="t", opener="round 1", initiator="claude")
    text = path.read_text()
    # Only codex signs; claude has not
    appended = (
        "\n## Turn 2 — codex — 2026-05-07T12:35:30Z\n\n"
        "agree.\n\nSIGNED: codex @ 2026-05-07T12:35:30Z\n\n"
    )
    marker = "<!-- LIVERY-WALKIE-TALKIE PROTOCOL"
    idx = text.find(marker)
    path.write_text(text[:idx] + appended + text[idx:])

    parsed = parse_walkie(path)
    # claude has taken a turn but not signed → not locked
    assert parsed.is_locked is False


def test_parse_walkie_locked_when_both_sign(tmp_path):
    path = new_walkie(workspace_root=tmp_path, topic="t", opener="r1", initiator="claude")
    text = path.read_text()
    appended = (
        "\n## Turn 2 — codex — 2026-05-07T12:35:30Z\n\n"
        "ok.\n\nSIGNED: codex @ 2026-05-07T12:35:30Z\n\n"
        "## Turn 3 — claude — 2026-05-07T12:36:00Z\n\n"
        "ok.\n\nSIGNED: claude @ 2026-05-07T12:36:00Z\n\n"
    )
    marker = "<!-- LIVERY-WALKIE-TALKIE PROTOCOL"
    idx = text.find(marker)
    path.write_text(text[:idx] + appended + text[idx:])

    parsed = parse_walkie(path)
    assert parsed.is_locked is True


# -----------------------------------------------------------------------------
# list_walkies
# -----------------------------------------------------------------------------


def test_list_walkies_empty(tmp_path):
    assert list_walkies(tmp_path) == []


def test_list_walkies_returns_all_files(tmp_path):
    new_walkie(workspace_root=tmp_path, topic="topic-a")
    new_walkie(workspace_root=tmp_path, topic="topic-b")
    walkies = list_walkies(tmp_path)
    assert {w.path.stem for w in walkies} == {"topic-a", "topic-b"}


def test_list_walkies_sorted_recent_first(tmp_path):
    early = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    late = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)
    new_walkie(workspace_root=tmp_path, topic="early-t", when=early)
    new_walkie(workspace_root=tmp_path, topic="late-t", when=late)
    walkies = list_walkies(tmp_path)
    assert walkies[0].path.stem == "late-t"
    assert walkies[1].path.stem == "early-t"


# -----------------------------------------------------------------------------
# Auto-mode metadata: briefing, peers, ticket reference
# -----------------------------------------------------------------------------


def test_new_walkie_with_briefing_writes_section(tmp_path):
    path = new_walkie(
        workspace_root=tmp_path,
        topic="t",
        briefing="The question is: build option 3 now, or later?\n\nContext: ...",
    )
    text = path.read_text()
    assert "## Briefing" in text
    assert "build option 3 now, or later?" in text
    # Briefing comes before the protocol section
    assert text.index("## Briefing") < text.index("LIVERY-WALKIE-TALKIE PROTOCOL")


def test_new_walkie_with_peers_records_in_frontmatter(tmp_path):
    path = new_walkie(
        workspace_root=tmp_path, topic="t",
        peers=["proposer", "critic"],
    )
    parsed = parse_walkie(path)
    assert parsed.declared_peers == ["proposer", "critic"]


def test_new_walkie_with_ticket_records_in_frontmatter(tmp_path):
    path = new_walkie(
        workspace_root=tmp_path, topic="t",
        ticket_id="2026-05-13-001-x",
    )
    parsed = parse_walkie(path)
    assert parsed.ticket_id == "2026-05-13-001-x"


def test_parse_legacy_walkie_has_no_auto_metadata(tmp_path):
    """A manual-mode walkie has no peers/ticket in frontmatter; parser
    returns None for both."""
    path = new_walkie(workspace_root=tmp_path, topic="t")
    parsed = parse_walkie(path)
    assert parsed.declared_peers is None
    assert parsed.ticket_id is None


# -----------------------------------------------------------------------------
# decide_next_peer
# -----------------------------------------------------------------------------


def test_decide_next_peer_picks_first_when_no_turns(tmp_path):
    from livery.walkie import decide_next_peer
    path = new_walkie(workspace_root=tmp_path, topic="t", peers=["proposer", "critic"])
    walkie = parse_walkie(path)
    assert decide_next_peer(walkie, ["proposer", "critic"]) == "proposer"


def test_decide_next_peer_alternates(tmp_path):
    """After proposer takes Turn 1, next is critic."""
    from livery.walkie import decide_next_peer
    path = new_walkie(
        workspace_root=tmp_path, topic="t",
        peers=["proposer", "critic"],
        opener="my pitch", initiator="proposer",
    )
    walkie = parse_walkie(path)
    assert decide_next_peer(walkie, ["proposer", "critic"]) == "critic"


def test_decide_next_peer_rejects_single_peer(tmp_path):
    from livery.walkie import decide_next_peer
    path = new_walkie(workspace_root=tmp_path, topic="t")
    walkie = parse_walkie(path)
    with pytest.raises(ValueError):
        decide_next_peer(walkie, ["only-peer"])


def test_decide_next_peer_handles_external_interjection(tmp_path):
    """If the last turn was by someone outside the declared peers,
    fall back to the first declared peer who hasn't gone yet."""
    from livery.walkie import decide_next_peer
    path = new_walkie(
        workspace_root=tmp_path, topic="t",
        peers=["proposer", "critic"],
        opener="surprise turn", initiator="someone-else",
    )
    walkie = parse_walkie(path)
    # Neither proposer nor critic has taken a turn yet → start with proposer
    assert decide_next_peer(walkie, ["proposer", "critic"]) == "proposer"
