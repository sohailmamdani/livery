from __future__ import annotations

from pathlib import Path

import frontmatter
import pytest

from livery.dispatch import (
    compose_walkie_prompt,
    prepare_walkie_turn,
)


def _make_workspace_with_peers(tmp_path: Path) -> Path:
    """Build a minimal workspace with two hired peers — proposer + critic."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "livery.toml").write_text('name = "ws"\n')

    for peer, runtime in (("proposer", "claude_code"), ("critic", "codex")):
        agent_dir = root / "agents" / peer
        agent_dir.mkdir(parents=True)
        agent_md = frontmatter.Post(
            f"{peer} agent",
            id=peer,
            name=peer.title(),
            runtime=runtime,
            cwd=str(tmp_path),
        )
        (agent_dir / "agent.md").write_text(frontmatter.dumps(agent_md) + "\n")
        (agent_dir / "AGENTS.md").write_text(
            f"# {peer.title()}\n\nYou are {peer}; debate with conviction.\n"
        )
    return root


# -----------------------------------------------------------------------------
# compose_walkie_prompt — three-layer prompt
# -----------------------------------------------------------------------------


def test_compose_walkie_prompt_has_identity_briefing_and_task(tmp_path):
    walkie_path = tmp_path / "walkie-talkie" / "topic.md"
    prompt = compose_walkie_prompt(
        peer="critic",
        other_peer="proposer",
        agents_md="# Critic\n\nYou push back.",
        walkie_path=walkie_path,
        turn_n=3,
        briefing="The question: should we ship option 3?",
    )
    # Layer 1: identity
    assert "critic" in prompt
    assert "BEGIN AGENTS.md" in prompt
    assert "You push back" in prompt
    # Layer 2: briefing
    assert "BEGIN BRIEFING" in prompt
    assert "should we ship option 3" in prompt
    # Layer 3: task
    assert "Take Turn 3" in prompt
    assert str(walkie_path) in prompt
    assert "Append your turn" in prompt
    assert "SIGNED:" in prompt


def test_compose_walkie_prompt_includes_ticket_when_present(tmp_path):
    walkie_path = tmp_path / "walkie-talkie" / "topic.md"
    prompt = compose_walkie_prompt(
        peer="critic", other_peer="proposer",
        agents_md="# Critic", walkie_path=walkie_path, turn_n=1,
        briefing=None,
        ticket_md="# Ticket: pick a path\n\nProposed: option 3.\n",
    )
    assert "BEGIN TICKET" in prompt
    assert "Proposed: option 3" in prompt


def test_compose_walkie_prompt_omits_briefing_section_when_none(tmp_path):
    walkie_path = tmp_path / "walkie-talkie" / "topic.md"
    prompt = compose_walkie_prompt(
        peer="critic", other_peer="proposer",
        agents_md="# Critic", walkie_path=walkie_path, turn_n=1,
        briefing=None, ticket_md=None,
    )
    assert "BEGIN BRIEFING" not in prompt
    assert "BEGIN TICKET" not in prompt


# -----------------------------------------------------------------------------
# prepare_walkie_turn — writes prompt + attempt JSON
# -----------------------------------------------------------------------------


def test_prepare_walkie_turn_writes_prompt_and_attempt(tmp_path):
    from livery.attempts import attempts_dir, load_attempt, AttemptStatus

    root = _make_workspace_with_peers(tmp_path)
    walkie_path = root / "walkie-talkie" / "topic.md"
    walkie_path.parent.mkdir()
    walkie_path.write_text("dummy walkie content\n")

    prep = prepare_walkie_turn(
        root=root,
        walkie_path=walkie_path,
        peer="critic",
        other_peer="proposer",
        turn_n=1,
        briefing="should we build option 3?",
    )

    # Prompt lives under the workspace, not /tmp
    assert prep.prompt_path.is_file()
    assert ".livery/walkie-talkie/prompts" in str(prep.prompt_path)
    prompt = prep.prompt_path.read_text()
    assert "critic" in prompt
    assert "should we build option 3?" in prompt

    # Attempt JSON was written
    assert prep.attempt_path is not None
    attempt = load_attempt(prep.attempt_path)
    assert attempt.status == AttemptStatus.PREPARED
    assert attempt.assignee == "critic"
    assert attempt.runtime == "codex"
    assert attempt.ticket_id.startswith("walkie-topic-t001")


def test_prepare_walkie_turn_rejects_unknown_peer(tmp_path):
    root = _make_workspace_with_peers(tmp_path)
    walkie_path = root / "walkie-talkie" / "topic.md"
    walkie_path.parent.mkdir()
    walkie_path.write_text("dummy\n")

    with pytest.raises(ValueError) as ei:
        prepare_walkie_turn(
            root=root, walkie_path=walkie_path,
            peer="nobody", other_peer="critic", turn_n=1,
        )
    assert "not a hired agent" in str(ei.value)


def test_prepare_walkie_turn_embeds_ticket_markdown(tmp_path):
    root = _make_workspace_with_peers(tmp_path)
    walkie_path = root / "walkie-talkie" / "topic.md"
    walkie_path.parent.mkdir()
    walkie_path.write_text("dummy\n")

    prep = prepare_walkie_turn(
        root=root, walkie_path=walkie_path,
        peer="proposer", other_peer="critic", turn_n=2,
        ticket_md="# Ticket: pick option\n\nbody here.\n",
    )
    prompt = prep.prompt_path.read_text()
    assert "BEGIN TICKET" in prompt
    assert "pick option" in prompt


# -----------------------------------------------------------------------------
# Controller — end-to-end with a stubbed runtime
# -----------------------------------------------------------------------------


def _seed_auto_walkie(root: Path, *, with_briefing: bool = True) -> Path:
    """Create an auto-mode walkie with proposer + critic declared."""
    from livery.walkie import new_walkie
    return new_walkie(
        workspace_root=root,
        topic="should-we-build-it",
        peers=["proposer", "critic"],
        briefing="The question: is option 3 worth it?" if with_briefing else None,
    )


def _append_turn_to_walkie(walkie_path: Path, *, peer: str, n: int, sign: bool = False) -> None:
    """Helper: simulate what a peer's runtime would do — append a turn
    just above the protocol section."""
    text = walkie_path.read_text()
    marker = "<!-- LIVERY-WALKIE-TALKIE PROTOCOL"
    idx = text.find(marker)
    new_block = (
        f"\n## Turn {n} — {peer} — 2026-05-13T12:00:0{n}Z\n\n"
        f"Position of {peer} at turn {n}.\n"
    )
    if sign:
        new_block += f"\nSIGNED: {peer} @ 2026-05-13T12:00:0{n}Z\n"
    new_block += "\n"
    walkie_path.write_text(text[:idx] + new_block + text[idx:])


def test_controller_step_calls_runtime_and_detects_advance(tmp_path, monkeypatch):
    """One controller step: peer is selected, dispatch runs (stubbed),
    walkie file advances by one turn → ControllerStep reports advanced=True."""
    import subprocess as real_sub
    from livery import walkie_controller as wc

    root = _make_workspace_with_peers(tmp_path)
    walkie_path = _seed_auto_walkie(root)

    # Stub Popen: instead of running a real runtime, append the turn
    # directly to the walkie file (simulating what the peer's runtime
    # would do) and return a fake proc that immediately succeeds.
    class _FakeProc:
        def __init__(self, peer: str, n: int):
            self.pid = 99999
            self._peer = peer
            self._n = n
            self._waited = False
        def wait(self, timeout=None):
            if not self._waited:
                _append_turn_to_walkie(walkie_path, peer=self._peer, n=self._n)
                self._waited = True
            return 0
        def kill(self):
            pass

    next_turn_state = {"n": 1, "peer": "proposer"}

    def fake_popen(cmd, shell=True):
        proc = _FakeProc(next_turn_state["peer"], next_turn_state["n"])
        return proc

    monkeypatch.setattr(wc.subprocess, "Popen", fake_popen)

    step = wc.controller_step(
        workspace_root=root,
        walkie_path=walkie_path,
        declared_peers=["proposer", "critic"],
        briefing=None,
        ticket_md=None,
    )
    assert step.peer == "proposer"
    assert step.turn_n == 1
    assert step.advanced is True
    assert step.exit_code == 0
    assert step.locked_after is False


def test_controller_detects_stall_when_runtime_does_not_append(tmp_path, monkeypatch):
    """If the peer's runtime exits cleanly but doesn't append a turn,
    the controller flags this as a stall (advanced=False)."""
    from livery import walkie_controller as wc

    root = _make_workspace_with_peers(tmp_path)
    walkie_path = _seed_auto_walkie(root)

    class _LazyProc:
        pid = 99998
        def wait(self, timeout=None): return 0
        def kill(self): pass

    monkeypatch.setattr(wc.subprocess, "Popen", lambda *a, **kw: _LazyProc())

    step = wc.controller_step(
        workspace_root=root, walkie_path=walkie_path,
        declared_peers=["proposer", "critic"],
        briefing=None, ticket_md=None,
    )
    assert step.advanced is False
    assert step.exit_code == 0


def test_controller_loop_runs_until_locked(tmp_path, monkeypatch):
    """Full loop: alternating peers, each appending + the second one
    signing on the final turn so the walkie locks and the loop stops."""
    from livery import walkie_controller as wc

    root = _make_workspace_with_peers(tmp_path)
    walkie_path = _seed_auto_walkie(root)

    # Scripted scenario: 3 turns total.
    #   Turn 1: proposer appends (no sign)
    #   Turn 2: critic appends + SIGNS
    #   Turn 3: proposer appends + SIGNS  → locked
    script = iter([
        ("proposer", 1, False),
        ("critic", 2, True),
        ("proposer", 3, True),
    ])

    class _ScriptedProc:
        pid = 99997
        def __init__(self):
            self._peer, self._n, self._sign = next(script)
        def wait(self, timeout=None):
            _append_turn_to_walkie(
                walkie_path, peer=self._peer, n=self._n, sign=self._sign,
            )
            return 0
        def kill(self):
            pass

    monkeypatch.setattr(wc.subprocess, "Popen", lambda *a, **kw: _ScriptedProc())

    result = wc.run_controller(
        workspace_root=root,
        walkie_path=walkie_path,
        max_turns=10,
    )

    assert result.ok is True
    assert result.locked is True
    assert len(result.steps) == 3
    assert "signed" in result.stopped_reason


def test_controller_loop_stops_on_stall(tmp_path, monkeypatch):
    """A peer that doesn't append → loop stops with a stall reason."""
    from livery import walkie_controller as wc

    root = _make_workspace_with_peers(tmp_path)
    walkie_path = _seed_auto_walkie(root)

    class _LazyProc:
        pid = 99996
        def wait(self, timeout=None): return 0
        def kill(self): pass

    monkeypatch.setattr(wc.subprocess, "Popen", lambda *a, **kw: _LazyProc())

    result = wc.run_controller(
        workspace_root=root, walkie_path=walkie_path, max_turns=5,
    )
    assert result.ok is False
    assert "stalled" in result.stopped_reason
    assert len(result.steps) == 1  # stops after the first stalled turn


def test_controller_requires_declared_peers(tmp_path):
    """A walkie without declared peers in frontmatter can't be auto-run."""
    from livery import walkie_controller as wc
    from livery.walkie import new_walkie

    root = _make_workspace_with_peers(tmp_path)
    walkie_path = new_walkie(workspace_root=root, topic="manual")  # no peers

    with pytest.raises(ValueError) as ei:
        wc.run_controller(workspace_root=root, walkie_path=walkie_path)
    assert "peers" in str(ei.value)
