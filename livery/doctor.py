"""`livery doctor` — report on runtime availability and workspace health.

Two passes:

1. **Runtimes**: for each supported runtime, is its CLI on PATH (or local
   HTTP endpoint reachable)?
2. **Workspace** (only if invoked inside one): for each hired agent, does
   its `cwd` exist, is it a git repo, and is its runtime usable?

Returns structured data so the caller can render as text or JSON. Network
checks (LM Studio, Ollama) use stdlib `urllib` with a short timeout — no
external deps.
"""

from __future__ import annotations

import shutil
import socket
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import frontmatter


# Mapping: runtime id → CLI binary name on PATH. `None` means "no binary,
# HTTP-only check" (lm_studio).
RUNTIME_BINARIES: dict[str, Optional[str]] = {
    "codex": "codex",
    "claude_code": "claude",
    "cursor": "cursor-agent",
    "ollama": "ollama",
    "lm_studio": None,
}

# Mapping: runtime id → local HTTP endpoint to ping. Only populated for
# runtimes that expose one.
RUNTIME_HTTP_ENDPOINTS: dict[str, str] = {
    "lm_studio": "http://localhost:1234/v1/models",
    "ollama": "http://localhost:11434/api/tags",
}


@dataclass(slots=True)
class RuntimeStatus:
    runtime: str
    binary: Optional[str]
    binary_path: Optional[str]
    http_endpoint: Optional[str]
    http_reachable: Optional[bool]  # None if no HTTP check applies
    ok: bool
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentStatus:
    agent_id: str
    runtime: str
    cwd: str
    cwd_exists: bool
    cwd_is_git: bool
    runtime_ok: bool
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.cwd_exists and self.runtime_ok


@dataclass(slots=True)
class DoctorReport:
    runtimes: list[RuntimeStatus]
    agents: list[AgentStatus]
    workspace_root: Optional[str]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.runtimes) and all(a.ok for a in self.agents)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "workspace_root": self.workspace_root,
            "runtimes": [asdict(r) for r in self.runtimes],
            "agents": [asdict(a) for a in self.agents],
        }


def _http_reachable(url: str, timeout: float = 1.5) -> bool:
    """Return True if `url` responds with any HTTP status. Errors → False.

    We don't care about the status code — a 200 OR a 401 both mean "something
    is listening." What we want to rule out is "nothing on this port."
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout):  # noqa: S310
            return True
    except urllib.error.HTTPError:
        # Got a response, even if it's an error — the server is up.
        return True
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
        return False


def check_runtime(runtime: str) -> RuntimeStatus:
    """Probe a single runtime. Safe to call even if the runtime name is unknown."""
    binary = RUNTIME_BINARIES.get(runtime, "__unknown__")
    http_endpoint = RUNTIME_HTTP_ENDPOINTS.get(runtime)
    notes: list[str] = []

    if binary == "__unknown__":
        return RuntimeStatus(
            runtime=runtime,
            binary=None,
            binary_path=None,
            http_endpoint=None,
            http_reachable=None,
            ok=False,
            notes=[f"unknown runtime '{runtime}'"],
        )

    binary_path: Optional[str] = None
    if binary is not None:
        found = shutil.which(binary)
        if found:
            binary_path = found
        else:
            notes.append(f"`{binary}` not on PATH")

    http_reachable: Optional[bool] = None
    if http_endpoint is not None:
        http_reachable = _http_reachable(http_endpoint)
        if not http_reachable:
            notes.append(f"endpoint {http_endpoint} unreachable")

    # OK logic: if both a binary and an endpoint are expected, either is
    # sufficient (e.g. ollama's CLI means you *could* run it; a live endpoint
    # means it's already running). If only one check applies, that one must
    # pass.
    if binary is not None and http_endpoint is not None:
        ok = binary_path is not None or bool(http_reachable)
    elif binary is not None:
        ok = binary_path is not None
    else:
        ok = bool(http_reachable)

    return RuntimeStatus(
        runtime=runtime,
        binary=binary,
        binary_path=binary_path,
        http_endpoint=http_endpoint,
        http_reachable=http_reachable,
        ok=ok,
        notes=notes,
    )


def check_all_runtimes() -> list[RuntimeStatus]:
    return [check_runtime(r) for r in RUNTIME_BINARIES.keys()]


def check_workspace_agents(root: Path, runtime_status: dict[str, RuntimeStatus]) -> list[AgentStatus]:
    """Inspect each agent in `root/agents/*/agent.md` and report status."""
    agents_dir = root / "agents"
    results: list[AgentStatus] = []
    if not agents_dir.is_dir():
        return results

    for agent_dir in sorted(agents_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent_md = agent_dir / "agent.md"
        if not agent_md.is_file():
            continue
        try:
            post = frontmatter.load(agent_md)
        except Exception as e:  # malformed yaml, etc.
            results.append(
                AgentStatus(
                    agent_id=agent_dir.name,
                    runtime="?",
                    cwd="?",
                    cwd_exists=False,
                    cwd_is_git=False,
                    runtime_ok=False,
                    notes=[f"failed to parse agent.md: {e}"],
                )
            )
            continue

        agent_id = str(post.get("id") or agent_dir.name)
        runtime = str(post.get("runtime") or "?")
        cwd_raw = post.get("cwd")
        cwd_str = str(cwd_raw) if cwd_raw else ""
        cwd_path = Path(cwd_str).expanduser() if cwd_str else None

        notes: list[str] = []
        cwd_exists = bool(cwd_path and cwd_path.exists())
        cwd_is_git = bool(cwd_path and (cwd_path / ".git").exists())

        if not cwd_str:
            notes.append("no cwd set in agent.md")
        elif not cwd_exists:
            notes.append(f"cwd does not exist: {cwd_str}")
        elif not cwd_is_git:
            notes.append(f"cwd is not a git repo: {cwd_str}")

        rt = runtime_status.get(runtime)
        runtime_ok = bool(rt and rt.ok)
        if rt is None:
            notes.append(f"runtime '{runtime}' unknown")
        elif not rt.ok:
            notes.append(f"runtime '{runtime}' unavailable: {'; '.join(rt.notes) or 'no details'}")

        results.append(
            AgentStatus(
                agent_id=agent_id,
                runtime=runtime,
                cwd=cwd_str,
                cwd_exists=cwd_exists,
                cwd_is_git=cwd_is_git,
                runtime_ok=runtime_ok,
                notes=notes,
            )
        )

    return results


def run_doctor(workspace_root: Optional[Path] = None) -> DoctorReport:
    """Top-level entry: probe every runtime, and if given a workspace root, every agent."""
    runtimes = check_all_runtimes()
    runtime_map = {r.runtime: r for r in runtimes}
    agents: list[AgentStatus] = []
    if workspace_root is not None:
        agents = check_workspace_agents(workspace_root, runtime_map)
    return DoctorReport(
        runtimes=runtimes,
        agents=agents,
        workspace_root=str(workspace_root) if workspace_root else None,
    )
