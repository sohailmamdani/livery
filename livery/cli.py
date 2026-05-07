from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import frontmatter
import typer

from .dispatch import prepare_dispatch, prepare_fan_out
from .dispatch_view import (
    DispatchState,
    find_dispatch,
    humanize_age,
    list_dispatches,
)
from .doctor import run_doctor
from .hire import SUGGESTED_MODELS, SUPPORTED_RUNTIMES, hire_agent
from .init import (
    SUPPORTED_COS_ENGINES,
    SkillCollisionResolution,
    init_workspace,
)
from .onboard import run_onboarding
from .paths import find_root
from .status import DEFAULT_RECENT_CLOSED_LIMIT, DEFAULT_STALE_DAYS, compute_status
from .hooks import HookStatus, install_hooks, uninstall_hooks
from .upgrade import Action, apply_plan, compute_plan, compute_sync_plan
from .telegram import (
    DEFAULT_LIVERY_BOT_COMMANDS,
    send_message,
    set_my_commands,
)

def _resolve_version() -> str:
    """Read the package version from installed metadata (best path) or pyproject (dev fallback)."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("livery")
    except (ImportError, Exception):
        # Fall through to dev-tree fallback below.
        pass
    try:
        import tomllib

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if pyproject.is_file():
            return tomllib.loads(pyproject.read_text())["project"]["version"]
    except Exception:
        pass
    return "unknown"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"livery {_resolve_version()}")
        raise typer.Exit()


app = typer.Typer(no_args_is_help=True, help="Livery — local AI company orchestration")
ticket_app = typer.Typer(no_args_is_help=True, help="Manage tickets")
dispatch_app = typer.Typer(no_args_is_help=True, help="Dispatch tickets to registered agents")
telegram_app = typer.Typer(no_args_is_help=True, help="Manage Telegram bot integration")
app.add_typer(ticket_app, name="ticket")
app.add_typer(dispatch_app, name="dispatch")
app.add_typer(telegram_app, name="telegram")


@app.callback()
def _root(
    version: Optional[bool] = typer.Option(
        None, "--version", "-v",
        callback=_version_callback, is_eager=True,
        help="Print the installed Livery version and exit.",
    ),
) -> None:
    """Livery — local AI company orchestration."""
    return None


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "ticket"


def _next_counter(root: Path, today: str) -> int:
    nums = []
    for p in (root / "tickets").glob(f"{today}-*.md"):
        m = re.match(rf"{today}-(\d+)-", p.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_ticket(root: Path, query: str) -> Path:
    tickets = root / "tickets"
    matches = sorted({*tickets.glob(f"*{query}*.md")})
    if not matches:
        typer.echo(f"No ticket matching '{query}'", err=True)
        raise typer.Exit(1)
    if len(matches) > 1:
        typer.echo(f"Multiple matches for '{query}':", err=True)
        for m in matches:
            typer.echo(f"  {m.stem}", err=True)
        raise typer.Exit(1)
    return matches[0]


@ticket_app.command("new")
def ticket_new(
    title: str = typer.Option(..., "--title", "-t", prompt=True, help="One-line imperative title"),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="Agent id, 'cos', or blank for unassigned"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Paragraph stating the goal"),
    context: Optional[str] = typer.Option(None, "--context", help="Optional links/constraints/prior decisions"),
) -> None:
    """Create a new ticket."""
    root = find_root()
    now = _now_iso()
    today = now[:10]
    counter = _next_counter(root, today)
    slug = _slugify(title)
    ticket_id = f"{today}-{counter:03d}-{slug}"
    path = root / "tickets" / f"{ticket_id}.md"

    if description is None:
        edited = typer.edit("\n# One paragraph stating the goal.\n")
        description = (edited or "").strip()

    body_parts = [
        "## Description\n",
        f"{description or '(none)'}\n",
    ]
    if context:
        body_parts.append(f"\n## Context\n\n{context}\n")
    body_parts.append(f"\n## Thread\n\n### {now} — user\n{description or '(see description)'}\n")

    post = frontmatter.Post(
        "".join(body_parts),
        id=ticket_id,
        title=title,
        assignee=assignee,
        status="open",
        created=now,
        updated=now,
    )
    path.write_text(frontmatter.dumps(post) + "\n")
    typer.echo(str(path.relative_to(root)))


@ticket_app.command("list")
def ticket_list(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="Filter by assignee"),
) -> None:
    """List tickets, optionally filtered."""
    root = find_root()
    rows = []
    for path in sorted((root / "tickets").glob("*.md")):
        post = frontmatter.load(path)
        if status and post.get("status") != status:
            continue
        if assignee and post.get("assignee") != assignee:
            continue
        rows.append((
            str(post.get("status", "?")),
            str(post.get("assignee") or "-"),
            str(post.get("id", path.stem)),
            str(post.get("title", "")),
        ))
    if not rows:
        typer.echo("(no tickets)")
        return
    for status_, assignee_, id_, title_ in rows:
        typer.echo(f"{status_:<10} {assignee_:<10} {id_}  {title_}")


@ticket_app.command("show")
def ticket_show(query: str) -> None:
    """Print a ticket's contents. `query` matches anywhere in the filename."""
    root = find_root()
    path = _find_ticket(root, query)
    typer.echo(path.read_text())


@ticket_app.command("close")
def ticket_close(
    query: str = typer.Argument(..., help="Ticket id or slug fragment"),
    summary: Optional[str] = typer.Option(None, "--summary", "-s", help="Closing summary to append to Thread"),
    status: str = typer.Option(
        "done", "--status",
        help="Terminal status to set. One of done, closed, cancelled, abandoned, wontfix.",
    ),
    telegram: bool = typer.Option(True, "--telegram/--no-telegram", help="Send Telegram notification on close"),
    push: bool = typer.Option(True, "--push/--no-push", help="git push after commit"),
) -> None:
    """Close a ticket: flip status→<terminal>, append optional CoS summary, git commit, ping Telegram.

    Default `--status done` is the regular happy-path close. For tickets you
    decided not to do, pass `--status cancelled` (or abandoned/wontfix). All
    of those are recognized as terminal by `livery status` and excluded from
    the open queue.
    """
    from .status import TERMINAL_STATUSES

    if status not in TERMINAL_STATUSES:
        typer.echo(
            f"--status must be one of {sorted(TERMINAL_STATUSES)}; got {status!r}",
            err=True,
        )
        raise typer.Exit(1)

    root = find_root()
    path = _find_ticket(root, query)
    post = frontmatter.load(path)
    now = _now_iso()

    if post.get("status") in TERMINAL_STATUSES:
        typer.echo(
            f"{post.get('id', path.stem)} is already {post.get('status')!r} (no-op)",
            err=True,
        )
        raise typer.Exit(1)

    post["status"] = status
    post["updated"] = now

    if summary:
        appended = post.content.rstrip() + f"\n\n### {now} — cos\n{summary}\n"
        post.content = appended

    path.write_text(frontmatter.dumps(post) + "\n")

    rel = str(path.relative_to(root))
    ticket_id = str(post.get("id", path.stem))
    title = str(post.get("title", ""))

    # Verb in commit + Telegram messages reflects the actual terminal status,
    # so log readers can tell "Cancel ticket X" from "Close ticket X" at a glance.
    # `verb` is imperative (commit subject); `past` is past-participle (Telegram + echo).
    verb_by_status = {
        "done": ("Close", "done"),
        "closed": ("Close", "closed"),
        "cancelled": ("Cancel", "cancelled"),
        "abandoned": ("Abandon", "abandoned"),
        "wontfix": ("Wontfix", "wontfix"),
    }
    verb, past = verb_by_status.get(status, (status.capitalize(), status))

    subprocess.run(["git", "-C", str(root), "add", rel], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", f"{verb} ticket {ticket_id}: {title}"],
        check=True,
    )

    if push:
        push_result = subprocess.run(
            ["git", "-C", str(root), "push"],
            capture_output=True,
            text=True,
        )
        if push_result.returncode != 0:
            typer.echo(f"push failed (non-fatal): {push_result.stderr.strip()}", err=True)

    if telegram:
        text_lines = [f"{ticket_id} {past}: {title}"]
        if summary:
            text_lines.append("")
            text_lines.append(summary)
        try:
            send_message("\n".join(text_lines))
        except Exception as e:
            typer.echo(f"Telegram send failed (non-fatal): {e}", err=True)

    typer.echo(f"{past.capitalize()}: {rel}")


@dispatch_app.command("prep")
def dispatch_prep(
    query: str = typer.Argument(..., help="Ticket id or slug fragment"),
    worktree: bool = typer.Option(
        False, "--worktree", "-w",
        help="Create a git worktree at <agent-cwd-parent>/<repo>-t<suffix> on branch ticket-<id>",
    ),
    output_dir: Path = typer.Option(
        Path("/tmp"), "--output-dir",
        help="Where to write the prompt file + capture file",
    ),
) -> None:
    """Prepare a ticket dispatch: compose prompt, optionally create worktree, print runtime command."""
    root = find_root()
    path = _find_ticket(root, query)
    try:
        prep = prepare_dispatch(
            root=root,
            ticket_path=path,
            output_dir=output_dir,
            make_worktree=worktree,
        )
    except (ValueError, NotImplementedError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    typer.echo(f"ticket:   {prep.ticket_id}")
    typer.echo(f"assignee: {prep.assignee}")
    typer.echo(f"runtime:  {prep.runtime}")
    typer.echo(f"model:    {prep.model or '(runtime default)'}")
    typer.echo(f"cwd:      {prep.cwd}")
    typer.echo(f"prompt:   {prep.prompt_path}")
    typer.echo(f"output:   {prep.output_path}")
    typer.echo()
    typer.echo("Run this (e.g. via Bash with run_in_background):")
    typer.echo(prep.command)


@dispatch_app.command("fan-out")
def dispatch_fan_out(
    query: str = typer.Argument(..., help="Ticket id or slug fragment"),
    to: str = typer.Option(..., "--to", help="Comma-separated list of agent ids to dispatch the ticket to"),
    worktree: bool = typer.Option(
        True, "--worktree/--no-worktree", "-w",
        help="Create a separate git worktree per agent. Default on — collisions otherwise.",
    ),
    output_dir: Path = typer.Option(
        Path("/tmp"), "--output-dir",
        help="Where to write prompt + capture files",
    ),
    run: bool = typer.Option(
        False, "--run", help="Launch all dispatches in parallel now and wait for completion",
    ),
) -> None:
    """Dispatch the same ticket to multiple agents in parallel.

    Each agent gets its own worktree, prompt file, and output file. Prints
    the N shell commands by default; pass --run to execute them in parallel.
    """
    import subprocess
    import time

    root = find_root()
    path = _find_ticket(root, query)
    assignees = [a.strip() for a in to.split(",") if a.strip()]

    try:
        preps = prepare_fan_out(
            root=root,
            ticket_path=path,
            output_dir=output_dir,
            make_worktree=worktree,
            assignees=assignees,
        )
    except (ValueError, NotImplementedError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    typer.echo(f"ticket: {preps[0].ticket_id}")
    typer.echo(f"fanning out to {len(preps)} agents:")
    typer.echo()
    for prep in preps:
        typer.echo(f"  {prep.assignee}  ({prep.runtime}, {prep.model or 'runtime default'})")
        typer.echo(f"    cwd:    {prep.cwd}")
        typer.echo(f"    prompt: {prep.prompt_path}")
        typer.echo(f"    output: {prep.output_path}")
        typer.echo(f"    cmd:    {prep.command}")
        typer.echo()

    if not run:
        typer.echo("Run these in parallel (bash):")
        for prep in preps:
            typer.echo(f"  {prep.command} &")
        typer.echo("  wait")
        typer.echo()
        typer.echo("Or re-run with --run to launch them now.")
        return

    # --run mode: launch all via Popen, wait for completion. Update each
    # attempt record as the subprocess transitions states (RUNNING when
    # launched, SUCCEEDED/FAILED on exit) so `dispatch status` and future
    # cancellation logic can find live PIDs without re-scanning.
    from .attempts import (
        AttemptStatus,
        FailureClass,
        load_attempt,
        mark_finished,
        mark_running,
    )

    procs: dict[str, subprocess.Popen] = {}
    prep_by_assignee: dict[str, object] = {}
    for prep in preps:
        p = subprocess.Popen(prep.command, shell=True)  # noqa: S602 — command comes from build_runtime_command
        procs[prep.assignee] = p
        prep_by_assignee[prep.assignee] = prep
        typer.echo(f"  [launched] {prep.assignee} (pid={p.pid})")

        # Mark the attempt RUNNING with this PID. Best-effort; failures
        # here shouldn't kill the dispatch.
        if prep.attempt_path:
            try:
                attempt = load_attempt(prep.attempt_path)
                mark_running(attempt, pid=p.pid, workspace_root=root)
            except Exception as e:
                typer.echo(f"  (warn) couldn't update attempt for {prep.assignee}: {e}", err=True)

    typer.echo()
    typer.echo("Waiting for completion (Ctrl+C aborts all)...")

    remaining = dict(procs)
    start = time.time()
    try:
        while remaining:
            time.sleep(0.5)
            done = [name for name, p in remaining.items() if p.poll() is not None]
            for name in done:
                p = remaining.pop(name)
                elapsed = int(time.time() - start)
                status_label = "ok" if p.returncode == 0 else f"exit={p.returncode}"
                typer.echo(f"  [done] {name} ({status_label}, {elapsed}s)")

                prep = prep_by_assignee.get(name)
                if prep is not None and getattr(prep, "attempt_path", None):
                    try:
                        attempt = load_attempt(prep.attempt_path)
                        mark_finished(attempt, exit_code=p.returncode or 0, workspace_root=root)
                    except Exception as e:
                        typer.echo(f"  (warn) couldn't finalize attempt for {name}: {e}", err=True)
    except KeyboardInterrupt:
        typer.echo("Aborting — terminating subprocesses...", err=True)
        for p in remaining.values():
            p.terminate()
        for p in remaining.values():
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        # Mark the killed attempts as cancelled so dispatch status doesn't
        # show them as still running forever.
        for name, p in remaining.items():
            prep = prep_by_assignee.get(name)
            if prep is not None and getattr(prep, "attempt_path", None):
                try:
                    attempt = load_attempt(prep.attempt_path)
                    attempt.status = AttemptStatus.CANCELLED
                    attempt.failure_class = FailureClass.RUNTIME_ERROR
                    attempt.failure_detail = "operator aborted with Ctrl+C"
                    from .attempts import write_attempt as _wa, now_iso as _now
                    attempt.finished_at = _now()
                    _wa(attempt, root)
                except Exception:
                    pass
        raise typer.Exit(130)

    any_failed = any(p.returncode != 0 for p in procs.values())
    if any_failed:
        typer.echo("\nOne or more dispatches exited non-zero. See output files above.", err=True)
        raise typer.Exit(1)


@dispatch_app.command("status")
def dispatch_status(
    output_dir: Path = typer.Option(
        Path("/tmp"), "--output-dir",
        help="Where dispatch artifacts live (must match what `dispatch prep` wrote).",
    ),
    since_minutes: Optional[int] = typer.Option(
        None, "--since-minutes",
        help="Only show dispatches whose output file was written in the last N minutes.",
    ),
) -> None:
    """Roll-up of every dispatch artifact in <output_dir>.

    Shows whether each dispatch finished (its output contains a
    `=== DISPATCH_SUMMARY ===` block), is still active (recent file
    activity, no summary yet), or went stale (file hasn't moved in a
    while and never produced a summary — usually a crash).
    """
    import sys

    views = list_dispatches(output_dir)
    if since_minutes is not None:
        cutoff = since_minutes * 60
        views = [v for v in views if v.age_seconds <= cutoff]

    if not views:
        typer.echo(f"No dispatch artifacts in {output_dir}.")
        return

    use_color = sys.stdout.isatty()

    def c(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if use_color else text

    GREEN, YELLOW, RED, DIM, BOLD = "32", "33", "31", "2", "1"
    icon_for = {
        DispatchState.DONE: (c("✓", GREEN), GREEN),
        DispatchState.ACTIVE: (c("●", YELLOW), YELLOW),
        DispatchState.STALE: (c("✗", RED), RED),
    }

    typer.echo(c(f"Dispatch artifacts in {output_dir}:", BOLD))
    typer.echo()

    for v in views:
        icon, color = icon_for[v.state]
        age = humanize_age(v.age_seconds)
        typer.echo(f"  {icon} {v.label}  {c(f'[{v.state.value}, {age} ago]', color)}")
        if v.state == DispatchState.DONE and v.summary_excerpt:
            for line in v.summary_excerpt[:3]:
                typer.echo(c(f"      {line}", DIM))
        elif v.last_line:
            shown = v.last_line[:120] + ("…" if len(v.last_line) > 120 else "")
            typer.echo(c(f"      last: {shown}", DIM))

    typer.echo()
    typer.echo(c("(`livery dispatch tail <query>` to follow one)", DIM))


@dispatch_app.command("tail")
def dispatch_tail(
    query: str = typer.Argument(
        ...,
        help="Substring matching the dispatch — ticket id, assignee, or both. Must match exactly one.",
    ),
    output_dir: Path = typer.Option(Path("/tmp"), "--output-dir"),
    follow: bool = typer.Option(
        False, "-f", "--follow",
        help="Tail -f the file (blocks until Ctrl+C). Default is one-shot.",
    ),
    lines: int = typer.Option(
        20, "-n", "--lines",
        help="How many trailing lines to print on a one-shot tail.",
    ),
) -> None:
    """Print (or follow) the output of a specific dispatch.

    Resolves the dispatch via substring match against the filename's
    `<ticket-id>-<assignee>` label. Errors if zero or multiple match.
    """
    import subprocess

    try:
        view = find_dispatch(query, output_dir)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    typer.echo(f"# {view.path}\n", err=True)
    if follow:
        # Hand off to `tail -f`: portable enough on macOS / Linux, simpler
        # than reimplementing inotify, and Ctrl+C signaling works as expected.
        subprocess.run(["tail", "-n", str(lines), "-f", str(view.path)])
    else:
        result = subprocess.run(
            ["tail", "-n", str(lines), str(view.path)],
            capture_output=True, text=True,
        )
        typer.echo(result.stdout, nl=False)


@app.command("init")
def init(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Workspace name (defaults to current dir name)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="One-line description"),
    path: Path = typer.Option(Path("."), "--path", "-p", help="Target directory (defaults to cwd)"),
    default_runtime: Optional[str] = typer.Option(None, "--default-runtime", help=f"One of: {', '.join(SUPPORTED_RUNTIMES)}"),
    telegram_chat_id: Optional[str] = typer.Option(None, "--telegram-chat-id", help="Telegram chat id for ticket-close pings"),
    telegram_token_file: Optional[str] = typer.Option(None, "--telegram-token-file", help="Path to .env with TELEGRAM_BOT_TOKEN"),
    cos_engine: str = typer.Option(
        "both", "--cos-engine",
        help=f"Which CoS convention file(s) to scaffold. One of: {', '.join(SUPPORTED_COS_ENGINES)}. "
             "'claude_code' writes CLAUDE.md, 'codex' writes AGENTS.md, 'both' writes both.",
    ),
    interactive: bool = typer.Option(
        True, "--interactive/--no-interactive",
        help="Prompt for missing fields (default on). Use --no-interactive for scripts.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing livery.toml"),
) -> None:
    """Scaffold a new Livery workspace in the current (or specified) directory."""
    import sys

    target = path.resolve()

    # Only prompt if interactive mode is on AND stdin is a TTY. Scripts
    # piping input shouldn't get stuck on prompts.
    should_prompt = interactive and sys.stdin.isatty()

    if name is None:
        default_name = target.name
        if should_prompt:
            name = typer.prompt("Workspace name", default=default_name)
        else:
            name = default_name

    if description is None:
        if should_prompt:
            description = typer.prompt("One-line description", default="")
        else:
            description = ""

    if default_runtime is None and should_prompt:
        typer.echo(f"Supported runtimes: {', '.join(SUPPORTED_RUNTIMES)} (or blank to skip)")
        answer = typer.prompt("Default runtime", default="")
        answer = answer.strip()
        if answer:
            if answer not in SUPPORTED_RUNTIMES:
                typer.echo(f"  '{answer}' is not a supported runtime — leaving unset.", err=True)
            else:
                default_runtime = answer

    if telegram_chat_id is None and should_prompt:
        answer = typer.prompt("Telegram chat id (blank to skip)", default="").strip()
        if answer:
            telegram_chat_id = answer

    if telegram_token_file is None and should_prompt and telegram_chat_id:
        answer = typer.prompt(
            "Telegram bot token .env path",
            default="~/.claude/channels/telegram/.env",
        ).strip()
        if answer:
            telegram_token_file = answer

    def _interactive_collision_callback(path: Path) -> SkillCollisionResolution:
        """Interactive prompt for a colliding skill/command file at `path`.

        The user's existing skill/command might serve a real purpose (e.g.,
        a pre-existing `/ticket` command from another tool). Default
        offer is rename — let the user keep their thing functional under
        a different name — with skip and overwrite as alternatives.
        """
        rel = path.relative_to(target)
        typer.echo("")
        typer.echo(
            f"Found existing {rel} that's not Livery-managed. Your version may "
            "still be useful — what should I do?"
        )
        typer.echo("  [r]ename your version to a new name (keeps it functional)")
        typer.echo("  [s]kip — leave yours alone, don't install Livery's")
        typer.echo("  [o]verwrite — replace yours with Livery's (destructive)")

        while True:
            choice = typer.prompt("[r/s/o]", default="r").strip().lower()
            if choice in ("r", "rename"):
                new_name = typer.prompt(
                    f"  New name for your existing {rel.name} (e.g. 'my-ticket')"
                ).strip()
                if not new_name:
                    typer.echo("  Empty name — pick again.", err=True)
                    continue
                return SkillCollisionResolution.rename(new_name)
            if choice in ("s", "skip"):
                return SkillCollisionResolution.skip()
            if choice in ("o", "overwrite"):
                if typer.confirm(
                    f"  Confirm: overwrite your {rel} with Livery's version? "
                    "(your existing content will be lost)",
                    default=False,
                ):
                    return SkillCollisionResolution.overwrite()
                continue
            typer.echo(f"  '{choice}' isn't one of r/s/o. Try again.", err=True)

    try:
        result = init_workspace(
            target=target,
            name=name,
            description=description or "",
            default_runtime=default_runtime,
            telegram_chat_id=telegram_chat_id,
            telegram_token_file=telegram_token_file,
            cos_engine=cos_engine,
            overwrite=force,
            skill_collision_callback=(
                _interactive_collision_callback if should_prompt else None
            ),
        )
    except (FileExistsError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    typer.echo(f"Initialized workspace '{name}' at {target}")
    for p in result.created:
        typer.echo(f"  + {p.relative_to(target)}")
    for p in result.appended:
        typer.echo(f"  ✱ {p.relative_to(target)}  (Livery template written; previous content carried over below)")
    for orig, renamed in result.backed_up:
        typer.echo(
            f"  ↪ {orig.relative_to(target)}  →  {renamed.relative_to(target)}  "
            "(your existing version, kept functional under new name)"
        )
    for path, reason in result.skipped:
        typer.echo(f"  ⚠ {path.relative_to(target)}  skipped — {reason}", err=True)
    typer.echo()

    # If the workspace has multiple CoS convention files and we're inside a
    # git repo, offer to install the pre-commit sync-cos hook. Skip silently
    # in non-interactive mode (scripts) and when no git repo exists yet.
    cos_files = [n for n in ("CLAUDE.md", "AGENTS.md") if (target / n).exists()]
    if (
        len(cos_files) > 1
        and should_prompt
        and (target / ".git").is_dir()
    ):
        typer.echo(
            f"You scaffolded {len(cos_files)} convention files ({', '.join(cos_files)})."
        )
        typer.echo(
            "A pre-commit hook can keep them in sync automatically — every `git commit`"
        )
        typer.echo(
            "would run `livery sync-cos --apply` and re-stage any changes it produced."
        )
        if typer.confirm("Install the pre-commit hook now?", default=True):
            try:
                results = install_hooks(target)
                for r in results:
                    typer.echo(f"  [{r.status.value}] {r.path.relative_to(target)}")
            except FileNotFoundError as e:
                typer.echo(f"  (skipped: {e})", err=True)
            typer.echo()

    cos_hint = " or ".join(cos_files) if cos_files else "your CoS file"
    typer.echo(f"Next: `livery hire <agent-id>` to scaffold your first agent, or edit {cos_hint}.")
    if len(cos_files) > 1 and not (target / ".git" / "hooks" / "pre-commit").is_file():
        typer.echo("Tip: `livery install-hooks` adds a pre-commit hook that keeps your")
        typer.echo("     convention files in sync automatically.")


def _prompt_runtime(default: Optional[str]) -> str:
    typer.echo("Supported runtimes: " + ", ".join(SUPPORTED_RUNTIMES))
    while True:
        choice = typer.prompt("runtime", default=default or "codex")
        if choice in SUPPORTED_RUNTIMES:
            return choice
        typer.echo(f"  '{choice}' is not a supported runtime. Try again.", err=True)


@app.command("hire")
def hire(
    agent_id: str = typer.Argument(..., help="Short id — becomes the agents/<id>/ directory name"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Human-friendly name (e.g. 'Research Analyst')"),
    role: Optional[str] = typer.Option(None, "--role", "-r", help="One-line role description"),
    runtime: Optional[str] = typer.Option(None, "--runtime", help=f"One of: {', '.join(SUPPORTED_RUNTIMES)}"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model id (runtime-specific)"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", "-c", help="Directory the agent works in"),
    reports_to: Optional[str] = typer.Option(None, "--reports-to", help="Who the agent reports to (default: cos)"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing agent directory"),
) -> None:
    """Scaffold a new agent under agents/<id>/.

    Interactive for any field not passed as a flag. Writes agent.md
    (structured frontmatter) and AGENTS.md (stub with section headers you
    fill in with your CoS).
    """
    root = find_root()

    if not name:
        name = typer.prompt("Human-friendly name", default=agent_id.replace("-", " ").title())
    if not role:
        role = typer.prompt("One-line role (what do they do, for whom?)")
    if not runtime:
        runtime = _prompt_runtime(default=None)
    if runtime not in SUPPORTED_RUNTIMES:
        typer.echo(f"Unsupported runtime '{runtime}'.", err=True)
        raise typer.Exit(1)
    if not model:
        suggested = SUGGESTED_MODELS.get(runtime)
        prompt_label = "Model" + (f" [suggested: {suggested}]" if suggested else " (required)")
        model = typer.prompt(prompt_label, default=suggested or "")
        model = model.strip() or None
    if not cwd:
        cwd_input = typer.prompt("Working directory (absolute path)")
        cwd = Path(cwd_input).expanduser()
    else:
        cwd = cwd.expanduser()
    cwd_resolved = cwd.resolve()
    if not cwd_resolved.exists():
        typer.echo(f"warning: cwd {cwd_resolved} does not exist yet", err=True)
    elif not (cwd_resolved / ".git").exists():
        typer.echo(f"warning: cwd {cwd_resolved} is not a git repo (worktree dispatch won't work)", err=True)
    if not reports_to:
        reports_to = typer.prompt("Reports to", default="cos")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        created = hire_agent(
            root=root,
            agent_id=agent_id,
            name=name,
            runtime=runtime,
            model=model,
            cwd=str(cwd_resolved),
            reports_to=reports_to,
            role=role,
            hired=today,
            overwrite=force,
        )
    except (FileExistsError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    typer.echo(f"Hired '{agent_id}' ({name}) on {runtime}")
    for path in created:
        typer.echo(f"  + {path.relative_to(root)}")
    typer.echo()
    typer.echo(f"Next: open agents/{agent_id}/AGENTS.md with your CoS and flesh out the system prompt.")


@app.command("onboard")
def onboard() -> None:
    """Guided setup: check runtimes, create a workspace, hire your first agent.

    Safe to re-run — detects where you are in the process and picks up from
    there. Skips steps you've already completed.
    """
    exit_code = run_onboarding()
    raise typer.Exit(exit_code)


@app.command("status")
def status(
    stale_days: int = typer.Option(
        DEFAULT_STALE_DAYS, "--stale-days",
        help=f"How many days an open ticket can age before it's flagged stale (default {DEFAULT_STALE_DAYS}).",
    ),
    full: bool = typer.Option(
        False, "--full",
        help="Show every closed ticket instead of just the most recent few.",
    ),
) -> None:
    """At-a-glance dashboard for the workspace.

    Groups open tickets by assignee, surfaces stale and blocked tickets,
    and shows recent closes. Companion to `livery ticket list` — that's
    the raw cut, this is the human-readable rollup.
    """
    import sys

    root = find_root()
    closed_limit = None if full else DEFAULT_RECENT_CLOSED_LIMIT
    report = compute_status(root, stale_days=stale_days, recent_closed_limit=closed_limit)

    use_color = sys.stdout.isatty()

    def c(text: str, code: str) -> str:
        if not use_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    YELLOW, GREEN, RED, DIM, BOLD = "33", "32", "31", "2", "1"

    def short_id(ticket_id: str) -> str:
        """Trim full ticket id (date-counter-slug) down to date-counter for display."""
        parts = ticket_id.split("-")
        if len(parts) >= 4 and parts[0].isdigit():
            return "-".join(parts[:4])
        return ticket_id

    typer.echo(c(report.workspace_name, BOLD) + c(f"  ({report.workspace_root})", DIM))
    if report.last_commit is not None:
        when, subject = report.last_commit
        delta_days = (datetime.now(timezone.utc) - when).days
        when_str = "today" if delta_days == 0 else f"{delta_days}d ago"
        truncated = subject if len(subject) <= 70 else subject[:67] + "..."
        typer.echo(c(f"Last commit: {when_str} — {truncated}", DIM))
    typer.echo()

    # Open by assignee
    if report.open_by_assignee:
        typer.echo(c("Open tickets by assignee:", BOLD))
        for assignee, count in report.open_by_assignee.items():
            oldest = report.oldest_open_per_assignee.get(assignee)
            oldest_str = f"  oldest: {oldest}d" if oldest is not None else ""
            typer.echo(f"  {assignee:<18} {count:>3}{c(oldest_str, DIM)}")
        typer.echo()
    else:
        typer.echo(c("No open tickets.", DIM))
        typer.echo()

    # Stale
    if report.stale_tickets:
        typer.echo(c(f"Stale (open ≥ {report.stale_days}d):", YELLOW) + c("  [oldest first]", DIM))
        for t in report.stale_tickets:
            age = f"{t.age_days}d"
            line = (
                f"  {c('⚠', YELLOW)} {short_id(t.id):<14}  "
                f"{t.assignee:<14} {t.title}  {c(f'({age})', DIM)}"
            )
            typer.echo(line)
        typer.echo()

    # Blocked
    if report.blocked_tickets:
        typer.echo(c("Blocked:", RED) + c("  [status: blocked or blocked_on: set]", DIM))
        for t in report.blocked_tickets:
            reason = f"  blocked_on: {t.blocked_on}" if t.blocked_on else ""
            age = f"({t.age_days}d)" if t.age_days is not None else ""
            line = (
                f"  {c('■', RED)} {short_id(t.id):<14}  "
                f"{t.assignee:<14} {t.title}{c(reason, DIM)}  {c(age, DIM)}"
            )
            typer.echo(line)
        typer.echo()

    # Recently closed
    if report.recently_closed:
        label = "All closed:" if full else f"Recently closed (last {len(report.recently_closed)}):"
        typer.echo(c(label, GREEN))
        for t in report.recently_closed:
            typer.echo(f"  {c('✓', GREEN)} {short_id(t.id):<14}  {t.title}")
        if not full and len(report.recently_closed) >= DEFAULT_RECENT_CLOSED_LIMIT:
            typer.echo(c("  (use --full for the full closed list)", DIM))
        typer.echo()

    # Runtimes
    runtime_color = GREEN if report.runtimes_ok == report.runtimes_total else YELLOW
    typer.echo(c(f"Runtimes: {report.runtimes_ok}/{report.runtimes_total} ok", runtime_color))


@app.command("install-hooks")
def install_hooks_cmd(
    uninstall: bool = typer.Option(
        False, "--uninstall",
        help="Remove Livery-managed hooks from .git/hooks/ instead of installing.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Overwrite pre-existing user-written hooks. By default they're left alone.",
    ),
) -> None:
    """Install Livery's git hooks into the workspace.

    Currently installs:
      - pre-commit — runs `livery sync-cos --apply` before each commit and
        re-stages any convention files the sync touched. Keeps CLAUDE.md
        and AGENTS.md from drifting silently.

    Hooks are not auto-installed by `livery init` or `upgrade-workspace` —
    .git/hooks/ is your territory, and this is an opt-in. Re-run any time
    to refresh; safe to remove with `--uninstall`.
    """
    root = find_root()
    try:
        if uninstall:
            results = uninstall_hooks(root)
        else:
            results = install_hooks(root, force=force)
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    if not results:
        typer.echo("Nothing to do.")
        return

    for r in results:
        rel = r.path.relative_to(root)
        typer.echo(f"  [{r.status.value}] {rel}")
        if r.detail:
            typer.echo(f"           — {r.detail}")

    has_skipped = any(r.status == HookStatus.SKIPPED for r in results)
    if has_skipped and not force and not uninstall:
        typer.echo(
            "\nSome hooks weren't touched because they look user-written. "
            "Pass --force to overwrite them.",
            err=True,
        )


@app.command("sync-cos")
def sync_cos(
    source: Optional[str] = typer.Option(
        None, "--from",
        help="Convention file to use as source (e.g. CLAUDE.md). Default: file with the most user content.",
    ),
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually write changes (default: dry-run preview only).",
    ),
) -> None:
    """Mirror user content from one convention file to all its siblings.

    Useful when you've edited CLAUDE.md and want AGENTS.md (and any other
    sibling convention file) to reflect the same changes — or vice versa.
    Source defaults to whichever sibling has the most user content (so a
    freshly-scaffolded template file can never overwrite a long-edited
    one). Override with `--from CLAUDE.md` if you need a specific source.

    The framework's LIVERY-MANAGED block on every target is refreshed to
    current as part of the rewrite. Files outside the convention-file set
    (livery.toml, agents/, tickets/, skills) are untouched.
    """
    root = find_root()
    try:
        plan = compute_sync_plan(root, source_filename=source)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    typer.echo(f"Workspace: {root}")
    if not plan.items:
        typer.echo("Nothing to sync — fewer than two convention files in this workspace.")
        return

    label = {
        Action.SKIP: "ok    ",
        Action.REFRESH: "sync  ",
    }
    for item in plan.items:
        rel = item.path.relative_to(root)
        typer.echo(f"  [{label.get(item.action, item.action.value)}] {rel}")
        if item.action != Action.SKIP:
            typer.echo(f"           — {item.reason}")

    if not plan.has_changes:
        typer.echo("\nAll convention files already in sync.")
        return

    if not apply:
        typer.echo("\n(dry-run; pass --apply to make changes)")
        return

    written = apply_plan(plan)
    typer.echo(f"\nSynced {len(written)} file(s).")


@app.command("upgrade-workspace")
def upgrade_workspace(
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually write changes (default: dry-run preview only).",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Overwrite framework-managed files that look user-customized. Use with care.",
    ),
) -> None:
    """Refresh framework-managed scaffolding without touching user content.

    Compares the current workspace against what `livery init` would produce
    today, and offers to update the framework-managed parts only:
    - The LIVERY-MANAGED block at the top of CLAUDE.md / AGENTS.md / etc.
    - Skill files for each declared CoS engine.

    Hard guardrails: never touches livery.toml, agents/, tickets/, or any
    user-edited content outside the LIVERY-MANAGED markers.
    """
    root = find_root()
    plan = compute_plan(root)

    typer.echo(f"Livery {_resolve_version()}")
    typer.echo(f"Workspace: {root}")
    typer.echo(f"CoS engines: {', '.join(plan.cos_engines)}")
    typer.echo()

    label = {
        Action.SKIP: "ok    ",
        Action.CREATE: "create",
        Action.REFRESH: "refresh",
        Action.INSERT: "insert",
        Action.MIGRATE: "migrate",
        Action.WARN: "warn  ",
    }
    for item in plan.items:
        rel = item.path.relative_to(root)
        typer.echo(f"  [{label[item.action]}] {rel}")
        if item.action != Action.SKIP:
            typer.echo(f"           — {item.reason}")

    if not plan.has_changes:
        typer.echo("\nNothing to do — workspace scaffolding is current with the running Livery version.")
        typer.echo("(To pick up a newer Livery release: `uv tool upgrade livery`.)")
        return

    if not apply:
        typer.echo("\n(dry-run; pass --apply to make changes)")
        return

    written = apply_plan(plan, force=force)
    typer.echo(f"\nApplied {len(written)} change(s).")
    skipped_warns = [i for i in plan.items if i.action == Action.WARN]
    if skipped_warns and not force:
        typer.echo(
            f"Skipped {len(skipped_warns)} customized file(s) — pass --force to overwrite.",
            err=True,
        )


@app.command("doctor")
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON for scripting"),
) -> None:
    """Check which runtimes are installed and, if inside a workspace, validate hired agents."""
    import json

    try:
        root = find_root()
    except RuntimeError:
        root = None

    report = run_doctor(workspace_root=root)

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2))
        raise typer.Exit(0 if report.ok else 1)

    typer.echo("Runtimes:")
    for r in report.runtimes:
        status = "ok " if r.ok else "FAIL"
        bits: list[str] = []
        if r.binary_path:
            bits.append(f"bin={r.binary_path}")
        elif r.binary:
            bits.append(f"bin={r.binary} (not found)")
        if r.http_endpoint:
            bits.append(f"http={'up' if r.http_reachable else 'down'}")
        detail = ", ".join(bits)
        typer.echo(f"  [{status}] {r.runtime:<12} {detail}")
        for note in r.notes:
            typer.echo(f"           - {note}")

    if root is None:
        typer.echo("\n(not inside a workspace — skipping agent checks)")
    else:
        typer.echo(f"\nWorkspace: {root}")
        if not report.agents:
            typer.echo("  (no agents hired yet — run `livery hire <id>` to add one)")
        for a in report.agents:
            status = "ok " if a.ok else "FAIL"
            typer.echo(f"  [{status}] {a.agent_id:<14} runtime={a.runtime} cwd={a.cwd}")
            for note in a.notes:
                typer.echo(f"           - {note}")

    raise typer.Exit(0 if report.ok else 1)


@telegram_app.command("register-commands")
def telegram_register_commands() -> None:
    """Register Livery slash commands with the Telegram bot (via setMyCommands)."""
    try:
        result = set_my_commands(DEFAULT_LIVERY_BOT_COMMANDS)
    except Exception as e:
        typer.echo(f"Failed: {e}", err=True)
        raise typer.Exit(1)
    for cmd in DEFAULT_LIVERY_BOT_COMMANDS:
        typer.echo(f"  /{cmd['command']:<10} {cmd['description']}")
    typer.echo(f"ok={result.get('ok')}, {len(DEFAULT_LIVERY_BOT_COMMANDS)} commands registered.")


if __name__ == "__main__":
    app()
