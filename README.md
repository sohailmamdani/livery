# Livery

Local, single-user harness for running a small AI company. Markdown + git, no database, no server.

Livery is a tool for people who want to **run a team of AI agents** (Claude Code, Codex, Cursor, Ollama-hosted local models, etc.) on their own machine to do real work — research, engineering, editorial. It's deliberately minimal: agents are files, tickets are files, state is in git. No web UI, no multi-user, no cloud anything.

If OpenClaw is an employee, Livery is the company.

## Who this is for

Tech-savvy operators (not necessarily programmers) who want an AI workforce on their laptop. Comfortable with the terminal, comfortable with git, comfortable reading markdown.

## What Livery gives you

- A **workspace** (a directory with `agents/`, `tickets/`, config) that becomes your CoS's operating context.
- **Linked project repos** so you can run `livery` commands from source repos while keeping one shared workspace/backlog.
- A **CLI** for hiring agents, filing tickets, dispatching work to agents, closing the loop.
- **Living ticket threads** that the CoS and hired agents update with distilled milestones, decisions, blockers, and results while Livery mirrors dispatch lifecycle events automatically.
- **Workspace memory** for durable decisions, lessons, and preferences as git-tracked markdown.
- **Runtime adapters** so agents can live on different stacks: Claude Code CLI, Codex CLI, Cursor, LM Studio, Ollama. Adding a new adapter is ~30 lines of Python.
- **Durable dispatch attempts** under `.livery/dispatch/attempts/`, with status, PID, failures, hook outcomes, prompt path, and output path recorded per run.
- **Portable schedules** tracked as markdown, installed explicitly as user-level `launchd` jobs on macOS or `systemd` timers on Linux.
- **Talk** for direct advisory conversations with a hired agent from your current harness, backed by append-only transcripts.
- **Walkie-Talkie** for structured AI-to-AI debate, either manual append-only transcripts or automated alternating dispatches between two hired agents.
- **Discoverability commands, startup hooks, and a Livery hello skill** so CoS sessions can ask Livery what applies from the current directory instead of guessing from stale docs.
- **Telegram integration** — close a ticket, get a ping.
- **CoS convention files** for Claude Code, Codex, Pi, and OpenCode, plus slash commands and skills where those engines support them.

## Harness-first API

Livery's primary UI is the agentic harness. The Python CLI is the local kernel:
it owns workspace discovery, file mutations, git/worktree operations, dispatch
attempt records, and upgrade compatibility. Harness skills and slash commands
call that kernel.

Most human commands keep readable text as the default, and the primary resource
commands also expose `--format json` for harnesses and scripts. Use JSON when a
tool needs to create tickets, inspect status, search memory, prepare dispatches,
or read dispatch output without scraping prose. See
[`docs/harness-api.md`](docs/harness-api.md).

## Status

Livery is **pre-1.0**. The CLI surface, `livery.toml` schema, and `agent.md` frontmatter shape are all stable enough that existing workspaces won't break across patch releases — but until 1.0 we reserve the right to make breaking changes between minor versions. Each one is called out in [`CHANGELOG.md`](CHANGELOG.md) with a migration note. MIT-licensed; bug reports and PRs welcome (see [`CONTRIBUTING.md`](CONTRIBUTING.md)).

## Install

Prerequisites:

1. **`uv`** — Astral's Python tool manager. Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
2. **At least one runtime** — Claude Code CLI, Codex CLI, Cursor Agent, Ollama, or LM Studio. You don't need all of them. Run `livery doctor` after install to see what's reachable.

Install the `livery` command globally — **floating with `main`** (this is what most people want):

```sh
uv tool install --from git+https://github.com/sohailmamdani/livery.git livery
```

Update later:

```sh
uv tool upgrade livery
```

After upgrading, preview and apply the framework-managed workspace refresh in
each existing workspace. This backfills new scaffolding and managed harness
entrypoints without touching your custom content:

```sh
livery upgrade-workspace          # preview
livery upgrade-workspace --apply  # write the proposed updates
```

### Pinning to a specific version (advanced)

```sh
uv tool install --force --from 'git+https://github.com/sohailmamdani/livery.git@v0.20.0' livery
```

**Important caveat:** pinning with `@v0.20.0` (or any git ref) makes `uv tool upgrade livery` a no-op forever — uv re-resolves the same pinned ref each time and concludes nothing has changed. To move a pinned install to a newer version, you have to reinstall with the new tag (or drop the pin entirely):

```sh
# move the pin forward when a newer tag exists
uv tool install --force --from 'git+https://github.com/sohailmamdani/livery.git@vX.Y.Z' livery

# or unpin and float with main
uv tool install --force --from 'git+https://github.com/sohailmamdani/livery.git' livery
```

Check what your install is tracking with `cat ~/.local/share/uv/tools/livery/uv-receipt.toml` — if you see `?rev=...` in the git URL, you're pinned.

## Quickstart

For a full step-by-step walkthrough, see [`docs/first-setup.md`](docs/first-setup.md). **The fastest path:** make a dedicated workspace directory and run `livery onboard`, which chains the runtime check, workspace init, and first-agent hire into one guided flow.

```sh
# Create a directory of its own for shared operations. The workspace is
# coordination, not source code; project repos can be linked back to it.
mkdir ~/companies/my-first-company && cd ~/companies/my-first-company
git init
livery onboard                # guided setup — safe to re-run at any point
```

Or use the commands directly:

```sh
# Ask Livery what applies from wherever you are
livery next
livery capabilities
livery session-brief                    # concise CoS startup context

livery init                             # scaffolds CLAUDE.md + AGENTS.md by default
# livery init --cos-engine codex        # if you'll use Codex (AGENTS.md only)
# livery init --cos-engine pi           # if you'll use Pi (AGENTS.md, no skill dirs)
# livery init --cos-engine opencode     # if you'll use OpenCode (AGENTS.md)
# livery init --cos-engine claude_code,codex,pi   # multiple engines, comma-separated
livery doctor                           # see which runtimes are reachable
livery hire writer                      # hire your first agent (interactive wizard)
livery agents                           # list hired agents and their runtimes/cwds
livery install-agent-hooks              # make Codex / Claude Code start Livery-aware
# Manual session entry: the Livery hello command/skill in your harness.

# From a project repo, point local livery commands back at this workspace
cd ~/code/my-project
livery link ~/companies/my-first-company --repo-id my-project
livery install-agent-hooks              # install linked-repo startup awareness here too
# Also installs linked-repo Livery entrypoints:
# Claude Code: /livery-hello, /livery-list-agents, /livery-new-ticket, /livery-talk-agent,
#              /livery-walkie-talkie, plus command-shaped entries like /livery-ticket-list
#              and /livery-schedule-install
# Codex: matching livery-* skills, including livery-schedule-* for every schedule command

# If this repo was already initialized as a standalone workspace, migrate it
# into the shared workspace while linking it.
livery link ~/companies/my-first-company --repo-id my-project --move-existing-workspace

# File a ticket, either for your CoS session ("cos") or a hired agent.
# From a linked repo, Livery records repo metadata automatically.
livery ticket new --title "Draft the homepage copy" --assignee cos

# See what's on the board
livery ticket list
livery ticket list --repo my-project

# Record durable workspace knowledge
livery memory add --type lesson --title "Review dispatch output before closing" --body "..."
livery memory search dispatch

# Ask a hired agent an advisory question without leaving this harness
livery talk web-swe "What do you think about this API boundary?"
livery talk web-swe "Follow-up question" --session api-boundary
livery talk list
livery talk show api-boundary

# Dispatch a ticket to its assigned agent (composes prompt + prints command)
livery dispatch prep <ticket-id>

# Close a ticket (commits, pushes, pings Telegram if configured)
livery ticket close <ticket-id> --summary "Shipped v1 copy."

# Cancel a ticket you decided not to do (same pipeline as close)
livery ticket close <ticket-id> --status cancelled --summary "Folded into the new schema."
```

`livery next`, `livery capabilities`, and `livery session-brief` are intentionally useful to both humans and CoS agents. Add `--format json` when Codex, Claude Code, or another tool needs structured output instead of prose. `livery install-agent-hooks` wires `session-brief` into Codex / Claude Code `SessionStart` hooks for the current workspace or linked repo. In a linked repo, `livery link` and `livery install-agent-hooks` also install linked-repo-specific Livery entrypoints so the harness knows tickets, Talk transcripts, and Walkie-Talkies belong in the parent workspace.

If hooks are not installed or you want an explicit session handshake, use the shipped Livery hello entry point. Claude Code gets a grouped Livery slash command in workspaces and `/livery-hello` in linked repos; Codex gets the `livery-hello` skill. It runs `livery session-brief`, acknowledges the active workspace or linked repo, then runs `livery status` for a quick board check.

For a repo that was linked before Livery shipped linked-repo entrypoints, update Livery, `cd` into that project repo, and run `livery install-agent-hooks` again. Livery refreshes files it owns and skips user-written command/skill files unless you pass `--force`.

## Workspace layout

A typical workspace looks like this. `livery init` creates the core scaffold; runtime state directories such as `.livery/`, `talk/`, and `walkie-talkie/` appear on first use.

```
my-workspace/
├── livery.toml                                # workspace marker + config
├── CLAUDE.md                                  # CoS conventions (Claude Code reads this)
├── AGENTS.md                                  # CoS conventions (Codex reads this) — same content as CLAUDE.md
├── agents/                                    # one dir per hired agent (Livery)
├── tickets/                                   # one markdown per ticket
├── memory/                                    # git-tracked decisions, lessons, preferences
│   ├── decisions/
│   ├── lessons/
│   └── preferences/
├── schedules/                                 # git-tracked recurring and one-shot agent tasks
├── talk/                                      # append-only operator-to-agent transcripts, created on first use
├── walkie-talkie/                             # append-only AI-to-AI debate transcripts, created on first use
├── .livery/                                   # ignored runtime state: attempts, hooks, ticket locks, schedule runs/install receipts
├── .claude/                                   # Claude Code's skill discovery dir
│   ├── commands/livery/                       # grouped Livery slash commands
│   │   ├── hello.md                           # Livery orientation command
│   │   ├── ticket-list.md                     # command-shaped wrappers
│   │   ├── ticket-update.md                   # concise progress/decision/blocker/result updates
│   │   ├── dispatch-status.md
│   │   ├── schedule-install.md                # schedule new/list/install/run/status/...
│   │   └── ...
│   └── skills/                                # livery-* skills, including one per CLI command
│       ├── livery-hello/SKILL.md
│       ├── livery-ticket-list/SKILL.md
│       ├── livery-ticket-update/SKILL.md
│       ├── livery-dispatch-status/SKILL.md
│       ├── livery-schedule-install/SKILL.md
│       └── ...
└── .agents/                                   # Codex's skill discovery dir (.agents/skills)
    └── skills/
        ├── livery-hello/SKILL.md
        ├── livery-ticket-list/SKILL.md
        ├── livery-dispatch-status/SKILL.md
        ├── livery-schedule-install/SKILL.md
        └── ...
```

`CLAUDE.md` and `AGENTS.md` are the same content with different names — one for each engine's convention. Same with shipped skills: they live in `.claude/skills/` for Claude Code and `.agents/skills/` for Codex. Livery ships a few friendly aliases (`livery-hello`, `livery-new-ticket`, `livery-talk-agent`, etc.) plus command-shaped skills such as `livery-ticket-list`, `livery-dispatch-status`, `livery-memory-search`, and all ten `livery-schedule-*` entries so the harness can discover the CLI surface directly. Claude Code slash commands live under `.claude/commands/livery/` so they stay grouped as Livery commands. `--cos-engine claude_code` skips the `.agents/` directory; `--cos-engine codex` skips `.claude/`. `--cos-engine pi` and `--cos-engine opencode` scaffold their `AGENTS.md`-style convention files without Claude/Codex-specific skill directories.

## Configuration (`livery.toml`)

```toml
name = "my-workspace"
description = "What this workspace is for"

default_runtime = "claude_code"   # optional; used for some subcommands
cos_engines = ["claude_code", "codex"]  # optional; which CoS scaffolding Livery manages

[telegram]
chat_id = "-1001234567890"                           # group or DM id
token_file = "~/.claude/channels/telegram/.env"       # optional; defaults here

[dispatch_hooks]
after_worktree_create = "..."  # optional; blocking hook after a dispatch worktree is made
before_run = "..."             # optional; blocking hook before a --run dispatch launches
after_run = "..."              # optional; advisory hook after a --run dispatch exits
```

## Runtimes

Livery dispatches work to external runtimes. Supported:

- **codex** — OpenAI Codex CLI
- **claude_code** (or `claude`) — Anthropic Claude Code CLI
- **cursor** (or `cursor_agent`) — Cursor Agent CLI
- **lm_studio** (or `mlx`) — LM Studio local server (HTTP)
- **ollama** — Ollama local server (HTTP, OpenAI-compatible)

**Tool use.** For the CLI-harness runtimes (codex, claude_code, cursor), Livery delegates tool use entirely to the harness — your agent gets whatever file, bash, web, and MCP tools the harness ships with. For the raw-LLM runtimes (lm_studio, ollama) there's no harness, so Livery runs its own agent loop with a minimal built-in tool set (`web_fetch`, `web_search`). See `docs/runtimes.md` for details.

Declare an agent's runtime in `agents/<id>/agent.md`:

```yaml
---
id: writer
name: Senior Writer
runtime: claude_code
model: claude-sonnet-4-6
effort: high
cwd: /Users/me/code/my-content-repo
reports_to: cos
hired: 2026-04-20
---
```

`model` is passed through to the runtime when present. `effort` is optional; today Livery passes it to Codex as `model_reasoning_effort` and to Claude Code as `--effort`.

List hired agents and their configured runtimes/cwds:

```sh
livery agents
livery agents --format json
```

## Talk

Talk is for direct advisory conversation with a hired agent while staying in
your current harness. It spawns the agent runtime in print mode, includes the
agent's `AGENTS.md`, appends the exchange to `talk/<session>.md`, and returns
the reply.

```sh
livery talk web-swe "Should this be a server-side export?"
livery talk web-swe "What would you check next?" --session pdf-export
livery talk list
livery talk show pdf-export
```

By default, Talk is not an implementation path. The prompt tells the agent not
to modify files, commit, install dependencies, or launch long-running work. If
the conversation turns into a task, file a ticket and dispatch it explicitly.

## Dispatch

Prepare a dispatch (composes the prompt, prints the shell command to run):

```sh
livery dispatch prep <ticket-id> --worktree
```

Run the printed command (usually as a background task so you can keep working). Every prepared dispatch writes a durable attempt record to `.livery/dispatch/attempts/<attempt-id>.json`, pointing at the prompt file, output file, runtime, model, PID/status when known, and any hook outcomes.

The attempt JSON is the detailed machine record; the ticket Thread is the
human record. Livery automatically appends distilled prepared, running,
completed, blocked, failed, and cancelled events. The dispatch prompt also
requires the operating agent to record meaningful milestones, decisions, and
blockers while it works:

```sh
livery ticket update <ticket-id> \
  --kind decision \
  --message "Use the existing parser; the replacement would break old workspaces." \
  --format json
```

Updates are locked so fan-out agents cannot overwrite one another. They should
capture outcomes, evidence, decisions, and next steps—not raw logs or every
tool call. When the agent finishes, review the ticket and close it with
`livery ticket close`.

To run **the same ticket against multiple agents in parallel** — e.g. to triangulate a research output across two different models — use fan-out:

```sh
livery dispatch fan-out <ticket-id> --to research,research-codex --run
```

Each agent gets its own git worktree, prompt file, and output file. Drop `--run` to print the N shell commands for you to run yourself.

To check on dispatches you've launched:

```sh
livery dispatch status                    # attempt records first, /tmp fallback for legacy/manual runs
livery dispatch tail <query>              # one-shot: print last 20 lines
livery dispatch tail <query> -f           # follow (tail -f)
```

`status` reads workspace attempt JSON first, then falls back to scanning `/tmp/livery-dispatch-*.out` for old or manually launched commands. Attempt-backed dispatches show richer lifecycle states: **prepared**, **running**, **succeeded**, **failed**, **blocked**, **stale**, **cancelled**, or **unknown**. For old/manual outputs with no attempt record, Livery still uses the legacy **done**, **active**, and **stale** classification.

## Scheduling

Schedule definitions are portable, git-tracked markdown under `schedules/`.
Activation is deliberately separate and host-local: macOS uses a per-user
LaunchAgent; Linux uses a `systemd --user` service and timer. Livery does not
run a resident scheduler daemon and does not edit crontab.

```sh
livery schedule new evening-brief \
  --agent evening-brief \
  --weekdays 18:00 \
  --task "Produce and verify the Evening Brief."

livery schedule install evening-brief --dry-run  # inspect exact host files + command
livery schedule install evening-brief            # install and enable
livery schedule list
livery schedule status evening-brief
livery schedule run evening-brief --now           # explicit manual run
livery schedule logs evening-brief
livery schedule disable evening-brief
livery schedule enable evening-brief
livery schedule uninstall evening-brief           # keeps schedules/evening-brief.md
```

Fresh, upgraded, and linked workspaces receive managed Claude slash commands
and matching Claude/Codex skills for every schedule subcommand: `new`, `list`,
`install`, `run`, `status`, `enable`, `disable`, `uninstall`, `sync`, and
`logs`. The install and sync entrypoints preview native scheduler changes
before applying them. Run, enable, disable, and uninstall remain explicit
actions because they can launch paid runtime work or mutate host scheduler
state.

Every fire is a managed dispatch with a unique prompt/output directory and a
normal durable attempt record. Scheduled attempts add `schedule_id`,
`scheduled_for`, and `trigger` fields, so `livery dispatch status` sees them
alongside ordinary work. The default policies are `overlap: skip` and
`missed_run: once`; only one missed calendar occurrence is caught up.

Supported portable triggers are one-shot RFC 3339 timestamps, daily/weekday/
weekly local calendar times, and fixed intervals. Interval timers measure time
while the native user scheduler is active and do not replay missed intervals.
Calendar definitions use the machine's local timezone. See
[`docs/scheduling.md`](docs/scheduling.md) for the file format, sleep/offline
semantics, Linux linger notes, and native file locations.

## Memory

Memory is git-tracked workspace knowledge, not hidden model memory. Use it for durable decisions, lessons, and preferences that future CoS sessions or agents should be able to rediscover:

```sh
livery memory add --type decision --title "Use worktrees for agent edits" --body "Engineering agents should dispatch with --worktree unless the ticket says otherwise."
livery memory list
livery memory search worktree
livery memory show <id>
```

Entries live under `memory/decisions/`, `memory/lessons/`, and `memory/preferences/`. `livery init` creates those directories for new workspaces; `livery upgrade-workspace --apply` backfills the scaffold for existing workspaces without touching existing memory entries.

### Dispatch hooks

Optional `[dispatch_hooks]` commands in `livery.toml` let you wire local automation into the dispatch lifecycle:

- `after_worktree_create` runs only when `--worktree` creates a worktree. Failure blocks the dispatch prep.
- `before_run` runs before `dispatch fan-out --run` or `walkie auto` launches a runtime. Failure blocks that run.
- `after_run` runs after a launched runtime exits. Failure is recorded as a warning, not a replacement for the runtime status.

Hook logs land under `.livery/dispatch/hooks/`, and each outcome is recorded on the attempt JSON.

## Walkie-Talkie

Walkie-Talkie is Livery's append-only debate protocol for two AI peers. Use it when a decision benefits from two agents pushing on each other's reasoning before implementation.

Manual mode creates the shared transcript and leaves turn-taking to the participants:

```sh
livery walkie new "rate limiter design" --with codex --as claude-code
livery walkie list
livery walkie show rate-limiter-design
```

Auto mode creates or resumes a transcript and alternates two hired agents until both sign, a turn stalls, or `--max-turns` is reached:

```sh
livery walkie auto "rate limiter design" --peer-a proposer --peer-b critic --ticket <ticket-id>
livery walkie auto "rate limiter design" --resume
```

Each auto turn is a normal dispatch attempt with its own prompt, output, hook outcomes, and status record.

## Status

Get an at-a-glance dashboard of the workspace — open tickets grouped by assignee, stale ones flagged, blocked ones highlighted, recent closes, runtime health:

```sh
livery status
```

`livery status` is the human-readable rollup; `livery ticket list` is the raw scriptable cut. In multi-repo workspaces, `livery ticket list --repo <repo-id>` filters to tickets tagged with a linked repo.

A ticket counts as **blocked** if its frontmatter has either `status: blocked` or `blocked_on: "<reason>"`. **Stale** is open ≥ 7 days by default (configurable with `--stale-days`).

## Upgrade an existing workspace

When Livery itself ships new framework defaults (refined CoS conventions, new shipped skills, etc.), you can refresh the framework-managed parts of an existing workspace without touching anything you've customized:

```sh
livery upgrade-workspace          # dry run — shows what would change
livery upgrade-workspace --apply  # actually write changes
```

Hard guardrails: it leaves agents, tickets, existing memory entries, custom
harness files, and text outside the `LIVERY-MANAGED` convention blocks alone.
It can add missing scaffolding (including `schedules/`) and create or refresh
Livery-owned slash commands and skills. The one `livery.toml` exception is an
additive, one-time `cos_engines` migration for legacy workspaces. Safe to run
after every `uv tool upgrade livery`; use `--force` only when you explicitly
want customized shipped command or skill files overwritten.

## Sync convention files

If you've edited `CLAUDE.md` and want `AGENTS.md` (or vice versa) to reflect the same content, run:

```sh
livery sync-cos                        # dry-run preview
livery sync-cos --apply                # actually write
livery sync-cos --from AGENTS.md       # explicit source (default: richest user content)
```

This mirrors user content from one sibling convention file to all the others, with each target's framework block refreshed to current. Useful when you maintain CLAUDE.md as the canonical file but also want Codex/Pi/OpenCode users to get the same workspace context.

## Telegram

Register the bot's slash commands with Telegram (one-time):

```sh
livery telegram register-commands
```

## Development

For working on Livery itself, see the dev CLAUDE.md in this repo.

```sh
git clone https://github.com/sohailmamdani/livery.git
cd livery
uv sync
uv run pytest
```

## License

MIT. See LICENSE.

## Roadmap (informal)

- More runtime adapters (bash, arbitrary HTTP, more model providers)
- More CoS engine adapters (currently: Claude Code, Codex, Pi, OpenCode)
- Stable v1.0

See `CHANGELOG.md` for what's already shipped.
