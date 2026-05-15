"""`livery onboard` — stateful guided setup.

Runs a three-step check (runtimes → workspace → agents) and offers to fix
anything missing. Idempotent: safe to re-run at any point in the setup
process; each step detects where you are and either reports or offers to
do the next thing.

The onboard flow does its own prompting rather than shelling out to
`livery init` / `livery hire` — that lets us add pedagogical framing
between steps without touching the CLI commands themselves.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from .cos_engines import COS_ENGINES, resolve_engines
from .doctor import run_doctor
from .hire import SUGGESTED_MODELS, SUPPORTED_RUNTIMES, hire_agent
from .hooks import install_hooks
from .init import init_workspace
from .paths import find_root


def _section(title: str) -> None:
    typer.echo()
    typer.echo(f"── {title} " + "─" * max(0, 60 - len(title) - 4))


def _ok(text: str) -> None:
    typer.echo(f"  ✓ {text}")


def _warn(text: str) -> None:
    typer.echo(f"  ! {text}", err=True)


def _check_runtimes() -> bool:
    """Show runtime status. Returns True if at least one runtime is reachable."""
    _section("Step 1 of 3: Runtimes")
    report = run_doctor(workspace_root=None)
    reachable = [r for r in report.runtimes if r.ok]
    if reachable:
        _ok(f"{len(reachable)} runtime(s) reachable: {', '.join(r.runtime for r in reachable)}")
        unreachable = [r for r in report.runtimes if not r.ok]
        if unreachable:
            typer.echo(f"    (not reachable: {', '.join(r.runtime for r in unreachable)} — fine, you only need one)")
        return True
    _warn("No runtimes reachable. You need at least one of:")
    for r in report.runtimes:
        typer.echo(f"      - {r.runtime}")
    typer.echo("    Install one before continuing, then re-run `livery onboard`.")
    return False


def _check_workspace(cwd: Path) -> Optional[Path]:
    """Return the workspace root. Offers to create one if the user isn't in one yet."""
    _section("Step 2 of 3: Workspace")
    try:
        root = find_root(cwd)
    except RuntimeError:
        root = None

    if root is not None:
        _ok(f"You're in a workspace at {root}")
        return root

    typer.echo("You're not currently inside a Livery workspace.")
    typer.echo("")
    typer.echo("A workspace is a directory that acts as your CoS's operating")
    typer.echo("context — where tickets live, where conventions live, and where")
    typer.echo("you hire agents to work in other directories. Use one workspace")
    typer.echo("per operational context; link project repos back to it when useful.")
    typer.echo("See docs/patterns.md for details.")
    typer.echo("")

    if not typer.confirm(f"Create a new workspace here ({cwd})?", default=False):
        typer.echo("OK. cd to a fresh directory and re-run `livery onboard` when ready.")
        return None

    name = typer.prompt("Workspace name", default=cwd.name)
    description = typer.prompt("One-line description", default="")

    typer.echo(f"Supported runtimes: {', '.join(SUPPORTED_RUNTIMES)} (or blank to skip)")
    default_runtime_in = typer.prompt("Default runtime (optional)", default="").strip()
    default_runtime = default_runtime_in if default_runtime_in in SUPPORTED_RUNTIMES else None
    if default_runtime_in and default_runtime is None:
        _warn(f"'{default_runtime_in}' is not a supported runtime — leaving unset.")

    telegram_chat_id = typer.prompt("Telegram chat id (optional, blank to skip)", default="").strip() or None
    telegram_token_file = None
    if telegram_chat_id:
        telegram_token_file = typer.prompt(
            "Telegram bot token .env path",
            default="~/.claude/channels/telegram/.env",
        ).strip() or None

    typer.echo(
        "CoS engine: which tool(s) will you open in this workspace to orchestrate?"
    )
    for eid, engine in COS_ENGINES.items():
        typer.echo(f"  - {eid:<12} → {engine.label} (scaffolds {engine.primary_convention_file})")
    typer.echo("  - both         → claude_code + codex (back-compat alias)")
    typer.echo("  Pass multiple comma-separated, e.g. claude_code,pi")
    cos_engine_raw = typer.prompt("CoS engine(s)", default="both").strip()
    try:
        resolved = resolve_engines(cos_engine_raw)
        cos_engine = ",".join(resolved)
    except ValueError as e:
        _warn(f"{e} — defaulting to 'both'.")
        cos_engine = "both"

    try:
        created = init_workspace(
            target=cwd,
            name=name,
            description=description,
            default_runtime=default_runtime,
            telegram_chat_id=telegram_chat_id,
            telegram_token_file=telegram_token_file,
            cos_engine=cos_engine,
        )
    except FileExistsError as e:
        _warn(str(e))
        return None

    _ok(f"Initialized workspace '{name}' at {cwd}")
    for p in created:
        typer.echo(f"    + {p.relative_to(cwd)}")

    # If multiple convention files were scaffolded and we're in a git repo,
    # offer the pre-commit sync-cos hook. Same prompt as `livery init`.
    cos_files = [n for n in ("CLAUDE.md", "AGENTS.md") if (cwd / n).exists()]
    if len(cos_files) > 1 and (cwd / ".git").is_dir():
        typer.echo("")
        typer.echo(
            f"You scaffolded {len(cos_files)} convention files ({', '.join(cos_files)})."
        )
        typer.echo(
            "A pre-commit hook can keep them in sync — every `git commit` would run"
        )
        typer.echo(
            "`livery sync-cos --apply` and re-stage any changes it produced."
        )
        if typer.confirm("Install the pre-commit hook now?", default=True):
            try:
                results = install_hooks(cwd)
                for r in results:
                    typer.echo(f"    [{r.status.value}] {r.path.relative_to(cwd)}")
            except FileNotFoundError as e:
                _warn(str(e))

    return cwd


def _list_agents(root: Path) -> list[str]:
    agents_dir = root / "agents"
    if not agents_dir.is_dir():
        return []
    return sorted(d.name for d in agents_dir.iterdir() if d.is_dir() and (d / "agent.md").is_file())


def _check_agents(root: Path) -> bool:
    """Return True if agents exist (or were just hired), False if user declined."""
    _section("Step 3 of 3: Agents")
    existing = _list_agents(root)
    if existing:
        _ok(f"{len(existing)} agent(s) hired: {', '.join(existing)}")
        return True

    typer.echo("No agents hired yet.")
    typer.echo("")
    typer.echo("An agent is a role — a researcher, a writer, a lead dev. Each agent")
    typer.echo("works in its own `cwd` (a code repo, a writing folder, etc.). The")
    typer.echo("agent's cwd is NOT the workspace — keep coordination and work in")
    typer.echo("separate directories.")
    typer.echo("")

    if not typer.confirm("Hire your first agent now?", default=True):
        typer.echo("OK. Run `livery hire <agent-id>` when ready.")
        return False

    agent_id = typer.prompt("Agent id (short, lowercase, hyphenated — e.g. 'writer')").strip()
    if not agent_id:
        _warn("Empty id — skipping agent creation.")
        return False

    name = typer.prompt("Human-friendly name", default=agent_id.replace("-", " ").title())
    role = typer.prompt("One-line role (what do they do, for whom?)")

    typer.echo(f"Supported runtimes: {', '.join(SUPPORTED_RUNTIMES)}")
    while True:
        runtime = typer.prompt("Runtime", default="claude_code").strip()
        if runtime in SUPPORTED_RUNTIMES:
            break
        _warn(f"'{runtime}' is not supported. Try again.")

    suggested = SUGGESTED_MODELS.get(runtime)
    model_label = "Model" + (f" [suggested: {suggested}]" if suggested else " (required for this runtime)")
    model_in = typer.prompt(model_label, default=suggested or "").strip()
    model = model_in or None

    cwd_raw = typer.prompt("Working directory (absolute path — NOT this workspace)").strip()
    agent_cwd = Path(cwd_raw).expanduser().resolve()
    if not agent_cwd.exists():
        _warn(f"{agent_cwd} does not exist yet. Create it before dispatching, or re-hire later.")
    elif not (agent_cwd / ".git").exists():
        _warn(f"{agent_cwd} is not a git repo — worktree dispatch won't work without git.")

    reports_to = typer.prompt("Reports to", default="cos").strip() or "cos"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        created = hire_agent(
            root=root,
            agent_id=agent_id,
            name=name,
            runtime=runtime,
            model=model,
            cwd=str(agent_cwd),
            reports_to=reports_to,
            role=role,
            hired=today,
        )
    except (FileExistsError, ValueError) as e:
        _warn(str(e))
        return False

    _ok(f"Hired '{agent_id}' ({name}) on {runtime}")
    for p in created:
        typer.echo(f"    + {p.relative_to(root)}")
    return True


def _next_steps(root: Path, agents_exist: bool) -> None:
    _section("Next steps")
    # Each engine that has its convention file present in the workspace is
    # ready to be opened — show all of them so the user knows their options.
    available: list[tuple[str, str]] = []  # (label, invocation hint)
    for engine in COS_ENGINES.values():
        for fname in engine.convention_filenames:
            if (root / fname).exists():
                available.append((engine.label, f"`{engine.invocation}` (auto-loads {fname})"))
                break

    if len(available) == 0:
        typer.echo(f"1. No CoS convention files found at {root}; run `livery init` first.")
    elif len(available) == 1:
        label, hint = available[0]
        typer.echo(f"1. Open your CoS here: cd {root} && {hint}")
    else:
        typer.echo(f"1. Open your CoS here: cd {root}")
        for label, hint in available:
            typer.echo(f"   - {label}: {hint}")
    typer.echo("")
    if agents_exist:
        typer.echo("2. Ask your CoS to help flesh out each agent's AGENTS.md:")
        typer.echo("     \"Help me write agents/<id>/AGENTS.md\"")
        typer.echo("   It will ask the right questions to turn the stub into a real system prompt.")
        typer.echo("")
        typer.echo("3. File your first ticket — either conversationally with the CoS or directly:")
        typer.echo("     livery ticket new --title \"...\" --assignee <agent-id|cos>")
    else:
        typer.echo("2. Hire your first agent when you're ready: livery hire <id>")
    typer.echo("")
    typer.echo("See docs/first-setup.md for a full walkthrough, docs/patterns.md for")
    typer.echo("worked examples, and docs/runtimes.md for how tool use works per runtime.")


def run_onboarding(cwd: Optional[Path] = None) -> int:
    """Top-level onboarding flow. Returns exit code (0 success, 1 blocker)."""
    if cwd is None:
        cwd = Path.cwd()

    typer.echo("Welcome to Livery. Let's get you set up.")

    if not _check_runtimes():
        return 1

    root = _check_workspace(cwd)
    if root is None:
        return 0  # user declined or workspace creation failed; not a hard error

    _check_agents(root)
    _next_steps(root, bool(_list_agents(root)))
    return 0
