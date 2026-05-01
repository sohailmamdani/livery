# Livery

Local, single-user harness for running a small AI company. Markdown + git, no database, no server.

Livery is a tool for people who want to **run a team of AI agents** (Claude Code, Codex, Cursor, Ollama-hosted local models, etc.) on their own machine to do real work — research, engineering, editorial. It's deliberately minimal: agents are files, tickets are files, state is in git. No web UI, no multi-user, no cloud anything.

If OpenClaw is an employee, Livery is the company.

## Who this is for

Tech-savvy operators (not necessarily programmers) who want an AI workforce on their laptop. Comfortable with the terminal, comfortable with git, comfortable reading markdown.

## What Livery gives you

- A **workspace** (a directory with `agents/`, `tickets/`, config) that becomes your company HQ.
- A **CLI** for hiring agents, filing tickets, dispatching work to agents, closing the loop.
- **Runtime adapters** so agents can live on different stacks: Claude Code CLI, Codex CLI, Cursor, LM Studio, Ollama. Adding a new adapter is ~30 lines of Python.
- **Telegram integration** — close a ticket, get a ping.
- **Slash commands and skills** for the Claude Code session that runs your workspace (the "Chief of Staff").

## Status

Livery is **pre-1.0**. The CLI surface, `livery.toml` schema, and `agent.md` frontmatter shape are all stable enough that existing workspaces won't break across patch releases — but until 1.0 we reserve the right to make breaking changes between minor versions. Each one is called out in [`CHANGELOG.md`](CHANGELOG.md) with a migration note. MIT-licensed; bug reports and PRs welcome (see [`CONTRIBUTING.md`](CONTRIBUTING.md)).

## Install

Prerequisites:

1. **`uv`** — Astral's Python tool manager. Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
2. **At least one runtime** — Claude Code CLI, Codex CLI, Cursor Agent, Ollama, or LM Studio. You don't need all of them. Run `livery doctor` after install to see what's reachable.

Install the `livery` command globally:

```sh
uv tool install --from git+https://github.com/sohailmamdani/livery.git livery
```

Pin to a specific version (recommended for stability):

```sh
uv tool install --force --from 'git+https://github.com/sohailmamdani/livery.git@v0.6.2' livery
```

Update later:

```sh
uv tool upgrade livery
```

After upgrading, run `livery upgrade-workspace` in any existing workspace to refresh framework-managed scaffolding without touching your custom content.

## Quickstart

For a full step-by-step walkthrough, see [`docs/first-setup.md`](docs/first-setup.md). **The fastest path:** make a dedicated workspace directory and run `livery onboard`, which chains the runtime check, workspace init, and first-agent hire into one guided flow.

```sh
# Create a directory of its own (NOT inside a code repo — the workspace is
# coordination, not source code). One workspace per company, not per project.
mkdir ~/companies/my-first-company && cd ~/companies/my-first-company
git init
livery onboard                # guided setup — safe to re-run at any point
```

Or use the commands directly:

```sh
livery init                             # scaffolds CLAUDE.md + AGENTS.md by default
# livery init --cos-engine codex        # if you'll use Codex (AGENTS.md only)
# livery init --cos-engine pi           # if you'll use Pi (AGENTS.md, no skill dirs)
# livery init --cos-engine opencode     # if you'll use OpenCode (AGENTS.md)
# livery init --cos-engine claude_code,codex,pi   # multiple engines, comma-separated
livery doctor                           # see which runtimes are reachable
livery hire writer                      # hire your first agent (interactive wizard)

# File a ticket, either for your CoS session ("cos") or a hired agent.
livery ticket new --title "Draft the homepage copy" --assignee cos

# See what's on the board
livery ticket list

# Dispatch a ticket to its assigned agent (composes prompt + prints command)
livery dispatch prep <ticket-id>

# Close a ticket (commits, pushes, pings Telegram if configured)
livery ticket close <ticket-id> --summary "Shipped v1 copy."

# Cancel a ticket you decided not to do (same pipeline as close)
livery ticket close <ticket-id> --status cancelled --summary "Folded into the new schema."
```

## Workspace layout

After `livery init` (default `--cos-engine both`):

```
my-workspace/
├── livery.toml                                # workspace marker + config
├── CLAUDE.md                                  # CoS conventions (Claude Code reads this)
├── AGENTS.md                                  # CoS conventions (Codex reads this) — same content as CLAUDE.md
├── agents/                                    # one dir per hired agent (Livery)
├── tickets/                                   # one markdown per ticket
├── .claude/                                   # Claude Code's skill discovery dir
│   ├── commands/ticket.md                     # /ticket slash command
│   └── skills/new-ticket/SKILL.md
└── .agents/                                   # Codex's skill discovery dir (.agents/skills)
    └── skills/new-ticket/SKILL.md
```

`CLAUDE.md` and `AGENTS.md` are the same file with different names — one for each engine's convention. Same with the `new-ticket` skill: it lives in `.claude/skills/` for Claude Code and `.agents/skills/` for Codex (Codex's convention path). `--cos-engine claude_code` skips the `.agents/` directory; `--cos-engine codex` skips `.claude/`.

## Configuration (`livery.toml`)

```toml
name = "my-workspace"
description = "What this workspace is for"

default_runtime = "claude_code"   # optional; used for some subcommands

[telegram]
chat_id = "-1001234567890"                           # group or DM id
token_file = "~/.claude/channels/telegram/.env"       # optional; defaults here
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
cwd: /Users/me/code/my-content-repo
title: Senior Writer
reports_to: cos
hired: 2026-04-20
---
```

## Dispatch

Prepare a dispatch (composes the prompt, prints the shell command to run):

```sh
livery dispatch prep <ticket-id> --worktree
```

Run the printed command (usually as a background task so you can keep working). When it finishes, close the ticket with `livery ticket close` and the loop continues.

To run **the same ticket against multiple agents in parallel** — e.g. to triangulate a research output across two different models — use fan-out:

```sh
livery dispatch fan-out <ticket-id> --to research,research-codex --run
```

Each agent gets its own git worktree, prompt file, and output file. Drop `--run` to print the N shell commands for you to run yourself.

To check on dispatches you've launched:

```sh
livery dispatch status                    # rollup of every dispatch artifact in /tmp
livery dispatch tail <query>              # one-shot: print last 20 lines
livery dispatch tail <query> -f           # follow (tail -f)
```

`status` flags each dispatch as **done** (its output contains a `=== DISPATCH_SUMMARY ===` block), **active** (recent file activity, no summary yet), or **stale** (file hasn't moved in 5+ minutes and never produced a summary — usually means the agent crashed or stuck).

## Status

Get an at-a-glance dashboard of the workspace — open tickets grouped by assignee, stale ones flagged, blocked ones highlighted, recent closes, runtime health:

```sh
livery status
```

`livery status` is the human-readable rollup; `livery ticket list` is the raw scriptable cut.

A ticket counts as **blocked** if its frontmatter has either `status: blocked` or `blocked_on: "<reason>"`. **Stale** is open ≥ 7 days by default (configurable with `--stale-days`).

## Upgrade an existing workspace

When Livery itself ships new framework defaults (refined CoS conventions, new shipped skills, etc.), you can refresh the framework-managed parts of an existing workspace without touching anything you've customized:

```sh
livery upgrade-workspace          # dry run — shows what would change
livery upgrade-workspace --apply  # actually write changes
```

Hard guardrails: it never touches `livery.toml`, `agents/`, `tickets/`, or anything outside the `LIVERY-MANAGED` markers in your CoS convention files. Safe to run after every `uv tool upgrade livery`.

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
