"""Direct advisory conversations with hired agents.

Talk is intentionally lighter than dispatch and less formal than
Walkie-Talkie. It lets the operator ask a hired agent a direct question
from the current harness, stores the exchange in an append-only transcript,
and returns the agent's reply to the caller.

This is advisory by default: the prompt tells the agent not to modify files
or run an implementation workflow. Real work still belongs in tickets and
dispatch attempts.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from .dispatch import WORKER_DISCOVERY_HINT
from .paths_safety import sanitize_path_component


TALK_DIR_NAME = "talk"
TALK_RUNTIME_DIR_NAME = "talk"
FRONTMATTER_MARKER = "talk"
DEFAULT_TIMEOUT_SECONDS = 600


@dataclass(slots=True)
class TalkTranscript:
    path: Path
    session_id: str
    agent_id: str
    started: str | None
    updated: str | None
    message_count: int
    last_speaker: str | None


@dataclass(slots=True)
class TalkResult:
    session_id: str
    agent_id: str
    transcript_path: Path
    prompt_path: Path
    output_path: Path
    command: str
    exit_code: int
    reply: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def talk_dir(workspace_root: Path) -> Path:
    return workspace_root / TALK_DIR_NAME


def _safe_session_id(session_id: str) -> str:
    return sanitize_path_component(session_id.strip(), fallback="talk")


def _validate_agent_id(agent_id: str) -> str:
    safe = sanitize_path_component(agent_id.strip(), fallback="agent")
    if safe != agent_id:
        raise ValueError(
            f"Invalid agent id {agent_id!r}: expected a single safe path component."
        )
    return agent_id


def _transcript_path(workspace_root: Path, session_id: str) -> Path:
    return talk_dir(workspace_root) / f"{_safe_session_id(session_id)}.md"


def _message_header(speaker: str, timestamp: str) -> str:
    return f"## {speaker} - {timestamp}"


def _count_message_headers(body: str) -> tuple[int, str | None]:
    count = 0
    last_speaker: str | None = None
    for line in body.splitlines():
        if line.startswith("## ") and " - " in line:
            count += 1
            last_speaker = line[3:].split(" - ", 1)[0].strip() or None
    return count, last_speaker


def _load_agent(workspace_root: Path, agent_id: str) -> tuple[frontmatter.Post, str]:
    agent_id = _validate_agent_id(agent_id)
    agent_dir = workspace_root / "agents" / agent_id
    agent_md_path = agent_dir / "agent.md"
    agents_md_path = agent_dir / "AGENTS.md"
    if not agent_md_path.exists():
        raise ValueError(
            f"Agent '{agent_id}' is not hired: missing {agent_md_path}. "
            f"Run `livery hire {agent_id}` first."
        )
    if not agents_md_path.exists():
        raise ValueError(f"Agent '{agent_id}' missing system prompt: {agents_md_path}")
    return frontmatter.load(agent_md_path), agents_md_path.read_text()


def _new_transcript(
    *,
    workspace_root: Path,
    session_id: str,
    agent_id: str,
    timestamp: str,
) -> Path:
    target = _transcript_path(workspace_root, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(
        f"# Talk: {session_id}\n\n",
        livery=FRONTMATTER_MARKER,
        session=session_id,
        agent=agent_id,
        started=timestamp,
        updated=timestamp,
    )
    target.write_text(frontmatter.dumps(post) + "\n")
    return target


def load_transcript(path: Path) -> TalkTranscript:
    post = frontmatter.load(path)
    if post.get("livery") != FRONTMATTER_MARKER:
        raise ValueError(f"{path} is not a Livery talk transcript")
    count, last_speaker = _count_message_headers(post.content)
    return TalkTranscript(
        path=path,
        session_id=str(post.get("session") or path.stem),
        agent_id=str(post.get("agent") or ""),
        started=str(post.get("started")) if post.get("started") else None,
        updated=str(post.get("updated")) if post.get("updated") else None,
        message_count=count,
        last_speaker=last_speaker,
    )


def list_transcripts(workspace_root: Path) -> list[TalkTranscript]:
    target = talk_dir(workspace_root)
    if not target.is_dir():
        return []
    transcripts: list[TalkTranscript] = []
    for path in sorted(target.glob("*.md")):
        try:
            transcripts.append(load_transcript(path))
        except ValueError:
            continue
    return sorted(
        transcripts,
        key=lambda transcript: transcript.updated or transcript.started or "",
        reverse=True,
    )


def resolve_transcript(workspace_root: Path, query: str) -> Path:
    exact = _transcript_path(workspace_root, query)
    if exact.exists():
        return exact
    matches = [
        transcript.path
        for transcript in list_transcripts(workspace_root)
        if query in transcript.session_id or query in transcript.path.stem
    ]
    if not matches:
        raise FileNotFoundError(f"No talk transcript matching {query!r}.")
    if len(matches) > 1:
        names = ", ".join(path.stem for path in matches)
        raise ValueError(f"Multiple talk transcripts match {query!r}: {names}.")
    return matches[0]


def append_message(path: Path, *, speaker: str, body: str, timestamp: str) -> None:
    post = frontmatter.load(path)
    post["updated"] = timestamp
    message = f"{_message_header(speaker, timestamp)}\n\n{body.rstrip()}\n"
    content = post.content.rstrip()
    post.content = f"{content}\n\n{message}\n"
    path.write_text(frontmatter.dumps(post) + "\n")


def build_talk_prompt(
    *,
    agent_id: str,
    agents_md: str,
    transcript_path: Path,
    transcript_text: str,
    latest_message: str,
) -> str:
    return "\n".join(
        [
            f'You are acting as the "{agent_id}" agent in a Livery Talk session.',
            "",
            "This is a direct advisory conversation with the operator, not a ticket dispatch.",
            "Do not modify files, create commits, install dependencies, launch long-running",
            "processes, or otherwise implement work from this prompt. If the operator asks",
            "for implementation or state changes, explain that the work should be turned into",
            "a Livery ticket and dispatched explicitly.",
            "",
            "Answer the operator's latest message directly and concisely. Push back when you",
            "are confident the operator is approaching the problem incorrectly.",
            "",
            "---BEGIN AGENTS.md---",
            "",
            agents_md.rstrip(),
            "",
            "---END AGENTS.md---",
            "",
            WORKER_DISCOVERY_HINT.rstrip(),
            "",
            f"Transcript file: {transcript_path}",
            "",
            "---BEGIN TALK TRANSCRIPT---",
            "",
            transcript_text.rstrip(),
            "",
            "---END TALK TRANSCRIPT---",
            "",
            "---BEGIN LATEST OPERATOR MESSAGE---",
            latest_message.rstrip(),
            "---END LATEST OPERATOR MESSAGE---",
            "",
            "Reply once, then stop.",
            "",
        ]
    )


def ensure_talk_runtime_dir(workspace_root: Path) -> Path:
    livery_dir = workspace_root / ".livery"
    livery_dir.mkdir(parents=True, exist_ok=True)
    gitignore = livery_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else "# Livery runtime state. Not for git.\n"
    lines = existing.splitlines()
    for entry in ("dispatch/", "logs/", f"{TALK_RUNTIME_DIR_NAME}/"):
        if entry not in lines:
            lines.append(entry)
    gitignore.write_text("\n".join(lines).rstrip() + "\n")
    target = livery_dir / TALK_RUNTIME_DIR_NAME / "prompts"
    target.mkdir(parents=True, exist_ok=True)
    return target


def build_talk_runtime_command(
    *,
    runtime: str,
    model: str | None,
    effort: str | None = None,
    cwd: str,
    prompt_path: Path,
    output_path: Path,
) -> str:
    cwd_q = shlex.quote(cwd)
    prompt_q = shlex.quote(str(prompt_path))
    output_q = shlex.quote(str(output_path))

    if runtime in {"codex", "codex_local"}:
        parts = ["codex", "exec"]
        if model:
            parts += ["--model", model]
        if effort:
            parts += ["--config", f'model_reasoning_effort="{effort}"']
        parts += ["--cd", cwd, "--skip-git-repo-check", "-"]
        quoted = " ".join(shlex.quote(part) for part in parts)
        return f"{quoted} < {prompt_q} > {output_q} 2>&1"

    if runtime in {"claude_code", "claude"}:
        parts = ["claude", "-p"]
        if model:
            parts += ["--model", model]
        if effort:
            parts += ["--effort", effort]
        quoted = " ".join(shlex.quote(part) for part in parts)
        return f"cd {cwd_q} && {quoted} < {prompt_q} > {output_q} 2>&1"

    if runtime in {"cursor", "cursor_agent"}:
        parts = ["cursor-agent", "--print"]
        if model:
            parts += ["--model", model]
        quoted = " ".join(shlex.quote(part) for part in parts)
        return f"cd {cwd_q} && {quoted} < {prompt_q} > {output_q} 2>&1"

    if runtime in {"lm_studio", "mlx", "ollama"}:
        if not model:
            raise ValueError(
                f"{runtime} runtime requires an explicit model in agent.md"
            )
        livery_root = str(Path(__file__).resolve().parent.parent)
        parts = [
            "uv", "run", "--directory", livery_root,
            "python", "-m", "livery.runtimes.lm_studio",
            "--model", model,
            "--verbose",
        ]
        if runtime == "ollama":
            parts += ["--url", "http://localhost:11434/v1"]
        quoted = " ".join(shlex.quote(part) for part in parts)
        return f"{quoted} < {prompt_q} > {output_q} 2>&1"

    raise NotImplementedError(
        f"Runtime '{runtime}' not supported for talk. Implemented: codex, claude_code, cursor, lm_studio."
    )


def _run_shell_command(command: str, *, output_path: Path, timeout_seconds: int) -> int:
    try:
        completed = subprocess.run(  # noqa: S602 - command is built by Livery runtime adapters
            command,
            shell=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        output_path.write_text(
            f"[talk] runtime timed out after {timeout_seconds}s\n"
        )
        return 124
    return int(completed.returncode)


def run_talk_turn(
    *,
    workspace_root: Path,
    agent_id: str,
    message: str,
    session_id: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> TalkResult:
    if not message.strip():
        raise ValueError("talk message cannot be empty")

    agent_post, agents_md = _load_agent(workspace_root, agent_id)
    runtime = str(agent_post.get("runtime") or "codex")
    model = agent_post.get("model")
    effort = agent_post.get("effort")
    cwd = agent_post.get("cwd")
    if not cwd:
        raise ValueError(f"Agent '{agent_id}' has no cwd in agent.md")

    session = _safe_session_id(session_id or agent_id)
    timestamp = now_iso()
    transcript_path = _transcript_path(workspace_root, session)
    if not transcript_path.exists():
        transcript_path = _new_transcript(
            workspace_root=workspace_root,
            session_id=session,
            agent_id=agent_id,
            timestamp=timestamp,
        )
    else:
        existing = load_transcript(transcript_path)
        if existing.agent_id and existing.agent_id != agent_id:
            raise ValueError(
                f"Talk session '{session}' belongs to agent '{existing.agent_id}', "
                f"not '{agent_id}'. Pick a different --session."
            )

    append_message(transcript_path, speaker="operator", body=message, timestamp=timestamp)
    transcript_text = transcript_path.read_text()

    prompt_dir = ensure_talk_runtime_dir(workspace_root)
    prompt_stamp = timestamp.replace("-", "").replace(":", "")
    prompt_path = prompt_dir / f"{session}-{agent_id}-{prompt_stamp}.txt"
    output_path = Path("/tmp") / f"livery-talk-{session}-{agent_id}-{prompt_stamp}.out"
    prompt_path.write_text(
        build_talk_prompt(
            agent_id=agent_id,
            agents_md=agents_md,
            transcript_path=transcript_path,
            transcript_text=transcript_text,
            latest_message=message,
        )
    )
    command = build_talk_runtime_command(
        runtime=runtime,
        model=str(model) if model else None,
        effort=str(effort) if effort else None,
        cwd=str(cwd),
        prompt_path=prompt_path,
        output_path=output_path,
    )

    exit_code = _run_shell_command(
        command,
        output_path=output_path,
        timeout_seconds=timeout_seconds,
    )
    reply = output_path.read_text().strip() if output_path.exists() else ""
    if exit_code == 0 and reply:
        append_message(
            transcript_path,
            speaker=agent_id,
            body=reply,
            timestamp=now_iso(),
        )

    return TalkResult(
        session_id=session,
        agent_id=agent_id,
        transcript_path=transcript_path,
        prompt_path=prompt_path,
        output_path=output_path,
        command=command,
        exit_code=exit_code,
        reply=reply,
    )
