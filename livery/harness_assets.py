"""Shared harness assets for Livery command-facing skills.

The hand-written entrypoints in ``init.py`` are friendly aliases such as
``livery-new-ticket``. This module adds the command-shaped layer: one managed
skill per concrete ``livery`` command so harnesses can discover the CLI surface
directly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandHarnessAsset:
    command: str
    skill_name: str
    description: str
    summary: str
    steps: tuple[str, ...]
    notes: tuple[str, ...] = ()
    slash_name: str | None = None
    argument_hint: str | None = None

    @property
    def slash_file(self) -> str:
        name = self.slash_name or self.skill_name.removeprefix("livery-")
        return f"{name}.md"


def _numbered(lines: tuple[str, ...]) -> str:
    return "\n".join(f"{idx}. {line}" for idx, line in enumerate(lines, start=1))


def _bullets(lines: tuple[str, ...]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def render_command_skill(asset: CommandHarnessAsset, *, linked_repo: bool = False) -> str:
    context = (
        "If this is a linked repo, Livery commands resolve through the parent "
        "workspace. Run `livery where --format json` when the user needs that "
        "location made explicit."
        if linked_repo
        else "Run the command from the active Livery workspace or linked repo."
    )
    notes = asset.notes
    notes_block = f"\n## Notes\n\n{_bullets(notes)}\n" if notes else ""
    return f"""---
name: {asset.skill_name}
description: {asset.description}
livery: managed
---

# {asset.command}

{asset.summary}

{context}

## Steps

{_numbered(asset.steps)}
{notes_block}"""


def render_command_slash(asset: CommandHarnessAsset, *, linked_repo: bool = False) -> str:
    hint = f"argument-hint: {asset.argument_hint}\n" if asset.argument_hint else ""
    context = (
        "If this command is run from a linked repo, mention that Livery will "
        "operate on the parent workspace."
        if linked_repo
        else "Use the active Livery workspace context."
    )
    notes = asset.notes
    notes_block = f"\nNotes:\n{_bullets(notes)}\n" if notes else ""
    return f"""---
description: {asset.summary}
{hint}livery: managed
---

Help the user run `{asset.command}` from the current harness.

{context}

Steps:
{_numbered(asset.steps)}
{notes_block}"""


COMMAND_HARNESS_ASSETS: tuple[CommandHarnessAsset, ...] = (
    CommandHarnessAsset(
        command="livery capabilities",
        skill_name="livery-capabilities",
        description="Show Livery's feature menu. Use when the user asks what Livery can do, asks for available commands, or wants capability discovery.",
        summary="Show the live Livery feature menu and explain the relevant options.",
        steps=(
            "Run `livery capabilities --format json`.",
            "Summarize the capabilities that matter for the user's current goal.",
            "Point to the next concrete command only when it helps the user act.",
        ),
    ),
    CommandHarnessAsset(
        command="livery next",
        skill_name="livery-next",
        description="Show context-aware next steps for the current directory. Use when the user asks what to do next or seems unsure which Livery command applies.",
        summary="Ask Livery for context-aware next steps from the current directory.",
        steps=(
            "Run `livery next --format json`.",
            "Explain whether the directory is a workspace, linked repo, or not Livery-aware.",
            "Recommend the highest-signal next action from the JSON.",
        ),
    ),
    CommandHarnessAsset(
        command="livery session-brief",
        skill_name="livery-session-brief",
        description="Read the concise Livery startup brief. Use when the user wants session orientation or hook-like workspace context.",
        summary="Read the startup brief that Livery injects into CoS sessions.",
        steps=(
            "Run `livery session-brief --format json`.",
            "Acknowledge the workspace or linked-repo context if one is present.",
            "Call out important status or instruction fields without turning it into a full report.",
        ),
    ),
    CommandHarnessAsset(
        command="livery ticket new",
        skill_name="livery-ticket-new",
        description="Create a new Livery ticket. Use when the user asks to create, file, open, or formalize work as a ticket.",
        summary="Create a markdown ticket in the active Livery workspace.",
        argument_hint="[title or brief description]",
        steps=(
            "Gather title, optional assignee, optional repo, description, and context.",
            "Run `livery ticket new --title \"...\" [--assignee <id|cos>] [--repo <repo>] --description \"...\" [--context \"...\"] --format json`.",
            "Show the created ticket id, repo metadata when present, and relative path.",
        ),
        notes=(
            "If assignee is blank, omit `--assignee`.",
            "From a linked repo, Livery can infer repo metadata automatically.",
        ),
    ),
    CommandHarnessAsset(
        command="livery ticket list",
        skill_name="livery-ticket-list",
        description="List Livery tickets, including open and closed tickets. Use when the user asks to list tickets, see open work, review closed work, or filter tickets by status, assignee, or repo.",
        summary="List tickets with optional status, assignee, or repo filters.",
        argument_hint="[--status open|done|closed|cancelled|abandoned|wontfix] [--assignee id] [--repo repo]",
        steps=(
            "Infer any requested filters: status, assignee, or repo.",
            "Run `livery ticket list [--status <status>] [--assignee <id>] [--repo <repo>] --format json`.",
            "Summarize the matching ticket ids, titles, statuses, assignees, and repo metadata.",
        ),
        notes=(
            "Omit `--status` to list open and closed tickets together.",
            "Use `--status open` for open tickets.",
            "Closed-style terminal statuses include `done`, `closed`, `cancelled`, `abandoned`, and `wontfix`.",
        ),
    ),
    CommandHarnessAsset(
        command="livery ticket show",
        skill_name="livery-ticket-show",
        description="Show a Livery ticket's full content. Use when the user asks to inspect, read, or summarize a specific ticket.",
        summary="Load one ticket by id or filename fragment.",
        argument_hint="<ticket-id-or-fragment>",
        steps=(
            "Identify the ticket id or unique filename fragment.",
            "Run `livery ticket show <ticket-id-or-fragment> --format json`.",
            "Summarize the ticket metadata and body, preserving important constraints.",
        ),
    ),
    CommandHarnessAsset(
        command="livery ticket close",
        skill_name="livery-ticket-close",
        description="Close or cancel a Livery ticket. Use only when the user explicitly asks to close, mark done, cancel, abandon, or wontfix a ticket.",
        summary="Set a ticket to a terminal status, append a summary, commit, and optionally push or notify Telegram.",
        argument_hint="<ticket-id> [--status done|closed|cancelled|abandoned|wontfix]",
        steps=(
            "Confirm the target ticket and terminal status if either is ambiguous.",
            "Gather a concise closing summary.",
            "Run `livery ticket close <ticket-id> --summary \"...\" [--status <terminal-status>] --format json`.",
            "Report the status, commit result, push result, and Telegram result from the JSON.",
        ),
        notes=(
            "Default status is `done`.",
            "Use `--no-push` or `--no-telegram` only when the user asks for that behavior.",
        ),
    ),
    CommandHarnessAsset(
        command="livery dispatch prep",
        skill_name="livery-dispatch-prep",
        description="Prepare a ticket dispatch for its assigned agent. Use when the user wants to dispatch a ticket or get the runtime command for an agent.",
        summary="Compose a dispatch prompt and runtime command for one ticket.",
        argument_hint="<ticket-id> [--worktree]",
        steps=(
            "Identify the ticket id or unique fragment.",
            "Decide whether a worktree is needed; prefer `--worktree` for implementation work in a repo.",
            "Run `livery dispatch prep <ticket-id> [--worktree] --format json`.",
            "Show the assignee, cwd, prompt path, output path, and command.",
        ),
    ),
    CommandHarnessAsset(
        command="livery dispatch fan-out",
        skill_name="livery-dispatch-fan-out",
        description="Prepare or launch the same ticket for multiple agents. Use when the user wants several agents to work or compare approaches in parallel.",
        summary="Prepare parallel dispatches for multiple agents.",
        argument_hint="<ticket-id> --to a,b [--run]",
        steps=(
            "Identify the ticket id and comma-separated target agent ids.",
            "Run `livery dispatch fan-out <ticket-id> --to <a,b> --format json` for a safe prep view.",
            "Only use `--run` when the user explicitly wants to launch the dispatches now.",
            "Summarize the prepared commands or launched outcomes.",
        ),
        notes=(
            "`--run` does not support JSON output.",
            "Default fan-out uses worktrees to avoid collisions.",
        ),
    ),
    CommandHarnessAsset(
        command="livery dispatch status",
        skill_name="livery-dispatch-status",
        description="List dispatch attempts and runtime artifacts. Use when the user asks what is running, what finished, what failed, or wants scheduled or prepared agent work status.",
        summary="Show prepared, running, succeeded, failed, blocked, stale, or cancelled dispatch attempts.",
        argument_hint="[--since-minutes N]",
        steps=(
            "Apply a `--since-minutes` filter if the user asks for recent activity.",
            "Run `livery dispatch status [--since-minutes <N>] --format json`.",
            "Summarize attempts by status, assignee, ticket label, and useful failure details.",
        ),
    ),
    CommandHarnessAsset(
        command="livery dispatch tail",
        skill_name="livery-dispatch-tail",
        description="Read the output of a specific dispatch attempt. Use when the user wants logs, latest output, or a dispatch summary for one ticket or agent.",
        summary="Tail one dispatch output file by matching ticket id or assignee.",
        argument_hint="<query> [-n lines]",
        steps=(
            "Choose a query that uniquely matches the dispatch label.",
            "Run `livery dispatch tail <query> --lines <N> --format json`.",
            "Relay the important output lines and dispatch metadata.",
        ),
        notes=(
            "Do not use `--follow` when structured JSON is needed.",
        ),
    ),
    CommandHarnessAsset(
        command="livery memory add",
        skill_name="livery-memory-add",
        description="Add a durable Livery memory entry. Use when the user wants to save a decision, lesson, or preference in workspace memory.",
        summary="Create a git-tracked memory entry under `memory/`.",
        argument_hint="--type decision|lesson|preference --title title",
        steps=(
            "Identify the memory type, short title, body, optional scope, and optional source ticket.",
            "Run `livery memory add --type <type> --title \"...\" --body \"...\" [--scope <scope>] [--source-ticket <id>] --format json`.",
            "Report the memory id and relative path.",
        ),
    ),
    CommandHarnessAsset(
        command="livery memory list",
        skill_name="livery-memory-list",
        description="List durable Livery memory entries. Use when the user asks what decisions, lessons, or preferences are saved.",
        summary="List workspace memory, optionally filtered by type.",
        argument_hint="[--type decision|lesson|preference]",
        steps=(
            "Apply a memory type filter if requested.",
            "Run `livery memory list [--type <type>] --format json`.",
            "Summarize ids, titles, types, scopes, and source tickets.",
        ),
    ),
    CommandHarnessAsset(
        command="livery memory show",
        skill_name="livery-memory-show",
        description="Show one durable Livery memory entry. Use when the user asks to read a saved decision, lesson, or preference.",
        summary="Load one memory entry by id, title, or filename.",
        argument_hint="<query>",
        steps=(
            "Identify the memory id, title, or filename query.",
            "Run `livery memory show <query> --format json`.",
            "Return the relevant content and metadata.",
        ),
    ),
    CommandHarnessAsset(
        command="livery memory search",
        skill_name="livery-memory-search",
        description="Search durable Livery memory entries. Use when the user asks whether something is documented or wants remembered decisions, lessons, or preferences matching a topic.",
        summary="Search memory entries by case-insensitive substring.",
        argument_hint="<query> [--type decision|lesson|preference]",
        steps=(
            "Identify the search query and optional type filter.",
            "Run `livery memory search <query> [--type <type>] --format json`.",
            "Summarize matching entries and include ids for follow-up `memory show` calls.",
        ),
    ),
    CommandHarnessAsset(
        command="livery init",
        skill_name="livery-init",
        description="Scaffold a new Livery workspace. Use when the user asks to initialize Livery in a directory or create a new workspace.",
        summary="Create the workspace marker, convention files, skill directories, agents, tickets, and memory scaffold.",
        argument_hint="[--path path] [--cos-engine engine]",
        steps=(
            "Confirm the target directory and whether it should be a workspace rather than a linked repo.",
            "Gather name, description, optional default runtime, optional Telegram settings, and CoS engines.",
            "Run `livery init --path <path> --name \"...\" --description \"...\" --cos-engine <engine-list>` with other flags as needed.",
            "Summarize created files and next setup steps.",
        ),
        notes=(
            "Do not run this inside the Livery framework repo itself.",
            "Use `livery link` instead when a project repo should point at an existing workspace.",
        ),
    ),
    CommandHarnessAsset(
        command="livery link",
        skill_name="livery-link",
        description="Link a project repo to a parent Livery workspace. Use when the user wants Livery commands in a repo to operate on an existing workspace.",
        summary="Write `.livery-link.toml` and install linked-repo harness entrypoints.",
        argument_hint="<workspace> [--repo path] [--repo-id id]",
        steps=(
            "Confirm the parent workspace path and project repo path.",
            "Choose a short repo id if one is not already known.",
            "Run `livery link <workspace> --repo <repo> [--repo-id <id>]`.",
            "Report the link file, repo id, and installed harness entries.",
        ),
    ),
    CommandHarnessAsset(
        command="livery where",
        skill_name="livery-where",
        description="Show which Livery workspace the current directory resolves to. Use when the user asks where Livery state lives or whether a repo is linked.",
        summary="Resolve the current directory to a workspace or linked repo.",
        steps=(
            "Run `livery where --format json`.",
            "Report the resolution kind, workspace root, marker path, linked repo root, repo id, and workspace id when present.",
        ),
    ),
    CommandHarnessAsset(
        command="livery agents",
        skill_name="livery-agents",
        description="List hired Livery agents. Use when the user asks for agents, assignees, runtimes, models, or working directories.",
        summary="List hired agents in the active workspace.",
        steps=(
            "Run `livery agents --format json`.",
            "Summarize agent ids, names, runtimes, models, and cwd values.",
            "Use returned ids exactly for tickets, dispatch, Talk, and Walkie-Talkie.",
        ),
    ),
    CommandHarnessAsset(
        command="livery talk",
        skill_name="livery-talk",
        description="Talk directly with a hired Livery agent. Use when the user wants advisory input from an agent or wants to inspect Talk transcripts.",
        summary="Send a direct advisory message to an agent or inspect Talk transcripts.",
        argument_hint="<agent-id> message | list | show session",
        steps=(
            "Run `livery agents --format json` if the target agent id is missing or ambiguous.",
            "For a message, run `livery talk <agent-id> \"...\" [--session <id>] --format json`.",
            "For transcript inventory, run `livery talk list --format json`.",
            "For one transcript, run `livery talk show <session> --format json`.",
            "Summarize the reply or transcript path and content.",
        ),
        notes=(
            "Talk is advisory only. Use tickets and dispatch for file-changing work.",
        ),
    ),
    CommandHarnessAsset(
        command="livery talk list",
        skill_name="livery-talk-list",
        description="List Livery Talk transcripts. Use when the user asks for previous direct-agent conversations or Talk sessions.",
        summary="List append-only Talk transcripts in the workspace.",
        steps=(
            "Run `livery talk list --format json`.",
            "Summarize sessions by id, agent, message count, updated timestamp, and path.",
        ),
    ),
    CommandHarnessAsset(
        command="livery talk show",
        skill_name="livery-talk-show",
        description="Show one Livery Talk transcript. Use when the user asks to read or summarize a direct-agent conversation.",
        summary="Load a Talk transcript by session id.",
        argument_hint="<session-id>",
        steps=(
            "Identify the Talk session id.",
            "Run `livery talk show <session-id> --format json`.",
            "Summarize the content and transcript metadata.",
        ),
    ),
    CommandHarnessAsset(
        command="livery hire",
        skill_name="livery-hire",
        description="Hire a new Livery agent. Use when the user asks to add, hire, or scaffold an agent.",
        summary="Create `agents/<id>/agent.md` and an agent prompt scaffold.",
        argument_hint="<agent-id>",
        steps=(
            "Gather agent id, name, role, runtime, model, cwd, and reports-to.",
            "Run `livery hire <agent-id>` with flags for known fields, or let the command prompt interactively.",
            "Report the created agent files and remind the user to flesh out the agent prompt.",
        ),
    ),
    CommandHarnessAsset(
        command="livery onboard",
        skill_name="livery-onboard",
        description="Run guided Livery setup. Use when the user wants a step-by-step setup flow for runtimes, workspace creation, and first agent hiring.",
        summary="Run Livery's guided onboarding flow.",
        steps=(
            "Run `livery onboard`.",
            "Follow the interactive prompts with the user.",
            "Summarize the setup state and any remaining manual steps.",
        ),
    ),
    CommandHarnessAsset(
        command="livery status",
        skill_name="livery-status",
        description="Show the Livery workspace dashboard. Use when the user asks for open work, stale or blocked tickets, recent closes, runtime health, or an overall board check.",
        summary="Show the workspace status dashboard.",
        argument_hint="[--full] [--stale-days N]",
        steps=(
            "Add `--full` if the user wants all closed tickets rather than the recent subset.",
            "Add `--stale-days <N>` if the user gives a stale threshold.",
            "Run `livery status [--full] [--stale-days <N>] --format json`.",
            "Summarize open counts, stale and blocked tickets, recent or all closed tickets, and runtime health.",
        ),
    ),
    CommandHarnessAsset(
        command="livery install-hooks",
        skill_name="livery-install-hooks",
        description="Install or remove Livery git hooks. Use when the user wants convention files kept in sync by a pre-commit hook.",
        summary="Install or uninstall the Livery-managed pre-commit hook.",
        argument_hint="[--uninstall] [--force]",
        steps=(
            "Confirm whether the user wants to install, refresh, force overwrite, or uninstall hooks.",
            "Run `livery install-hooks` with `--force` or `--uninstall` only when requested.",
            "Report installed, updated, skipped, or removed hook files.",
        ),
    ),
    CommandHarnessAsset(
        command="livery install-agent-hooks",
        skill_name="livery-install-agent-hooks",
        description="Install or remove Livery SessionStart hooks and linked-repo harness entries. Use when Codex or Claude Code should start Livery-aware.",
        summary="Install CoS startup hooks and linked-repo entrypoints.",
        argument_hint="[--engine codex,claude_code] [--uninstall] [--force]",
        steps=(
            "Confirm engines if the user wants something other than codex and claude_code.",
            "Run `livery install-agent-hooks [--engine <engines>]` with `--force` or `--uninstall` only when requested.",
            "Report hook and harness-entry statuses.",
        ),
    ),
    CommandHarnessAsset(
        command="livery sync-cos",
        skill_name="livery-sync-cos",
        description="Mirror user content between CoS convention files. Use when CLAUDE.md, AGENTS.md, or other convention files should be synchronized.",
        summary="Sync user-editable convention content between sibling CoS files.",
        argument_hint="[--from CLAUDE.md] [--apply]",
        steps=(
            "Run a dry run first with `livery sync-cos [--from <file>]`.",
            "Explain which files would change.",
            "Run `livery sync-cos [--from <file>] --apply` only when the user wants the changes written.",
        ),
    ),
    CommandHarnessAsset(
        command="livery upgrade-workspace",
        skill_name="livery-upgrade-workspace",
        description="Refresh framework-managed workspace scaffolding. Use after upgrading Livery or when shipped skills, slash commands, or managed blocks need backfilling.",
        summary="Preview or apply framework-managed scaffold updates.",
        argument_hint="[--apply] [--force]",
        steps=(
            "Run `livery upgrade-workspace` first as a dry run.",
            "Explain created, refreshed, warned, and skipped items.",
            "Run `livery upgrade-workspace --apply` when the user wants safe updates written.",
            "Use `--force` only when the user explicitly wants customized shipped skill or command files overwritten.",
        ),
    ),
    CommandHarnessAsset(
        command="livery doctor",
        skill_name="livery-doctor",
        description="Check Livery runtime and agent health. Use when the user asks whether runtimes are installed, why dispatch may fail, or for an environment check.",
        summary="Validate runtime binaries, HTTP endpoints, and hired-agent configuration.",
        steps=(
            "Run `livery doctor --json`.",
            "Summarize failing runtimes or agents first, then healthy ones briefly.",
            "Suggest the smallest next fix for each failure.",
        ),
    ),
    CommandHarnessAsset(
        command="livery telegram register-commands",
        skill_name="livery-telegram-register-commands",
        description="Register Livery commands with the configured Telegram bot. Use when the user asks to set up or refresh Telegram bot slash commands.",
        summary="Register the default Livery Telegram slash commands via Bot API.",
        steps=(
            "Confirm Telegram configuration exists in the workspace or environment.",
            "Run `livery telegram register-commands`.",
            "Report which commands were registered or the error returned.",
        ),
    ),
    CommandHarnessAsset(
        command="livery walkie new",
        skill_name="livery-walkie-new",
        description="Create a manual Livery Walkie-Talkie file. Use when the user wants an append-only AI-to-AI debate with another harness.",
        summary="Create a new manual Walkie-Talkie transcript.",
        argument_hint="<topic> --with <peer>",
        steps=(
            "Gather topic, peer identity, your own engine identity, and optional opener.",
            "Run `livery walkie new <topic> --with <peer> --as <self> [--opener \"...\"]`.",
            "Tell the user the file path and how to give it to the peer harness.",
        ),
    ),
    CommandHarnessAsset(
        command="livery walkie auto",
        skill_name="livery-walkie-auto",
        description="Run or resume an automated Livery Walkie-Talkie debate. Use when two hired agents should debate a topic until convergence.",
        summary="Run the Walkie-Talkie controller between two hired agents.",
        argument_hint="<topic> --peer-a id --peer-b id",
        steps=(
            "Gather topic, peer-a, peer-b, optional briefing, optional ticket, max turns, and timeout.",
            "Run `livery walkie auto <topic> --peer-a <id> --peer-b <id> [--briefing ...] [--ticket <id>]`.",
            "Use `--resume` only when continuing an existing walkie.",
            "Summarize whether it locked, where the file is, and attempt ids.",
        ),
    ),
    CommandHarnessAsset(
        command="livery walkie list",
        skill_name="livery-walkie-list",
        description="List Livery Walkie-Talkie files. Use when the user asks what debates exist or which are locked.",
        summary="List Walkie-Talkie transcripts with turn counts and lock status.",
        steps=(
            "Run `livery walkie list`.",
            "Summarize filenames, topics, turn counts, peers, signed peers, and lock status.",
        ),
    ),
    CommandHarnessAsset(
        command="livery walkie show",
        skill_name="livery-walkie-show",
        description="Show one Livery Walkie-Talkie transcript. Use when the user asks to inspect or summarize a debate.",
        summary="Print one Walkie-Talkie file by topic substring.",
        argument_hint="<topic-fragment>",
        steps=(
            "Identify the topic substring that uniquely matches the walkie.",
            "Run `livery walkie show <topic-fragment>`.",
            "Summarize the turn count, peers, signatures, locked state, and latest substantive points.",
        ),
    ),
)
