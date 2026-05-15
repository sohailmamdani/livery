# Configuration

Livery has two kinds of config:

1. **Workspace config** — `livery.toml` at the workspace root. Marks the directory as a Livery workspace and captures user-specific settings.
2. **Agent config** — `agents/<id>/agent.md` frontmatter. Declares what an agent is, what runtime it runs on, and where it works.
3. **Linked-repo config** — `.livery-link.toml` at a project repo root. Points project-level `livery` commands back at a workspace.

## `livery.toml`

`livery.toml` is the workspace marker. The CLI walks up from your cwd looking for this file — so `livery ticket list` works anywhere inside the workspace tree. If it finds `.livery-link.toml` first, it follows that link and operates on the linked workspace instead.

All fields are optional. A minimal file is just:

```toml
name = "my-workspace"
description = "What this workspace is for"
```

### Full schema

```toml
# Top-level
name = "my-workspace"                      # string (default: "unnamed-workspace")
description = "One-line description"       # string (default: "")
default_runtime = "claude_code"            # string | unset — one of codex, claude_code, cursor, lm_studio, ollama
cos_engines = ["claude_code", "codex"]     # list of CoS engines this workspace targets

[telegram]
chat_id = "-1001234567890"                 # string — group or DM id for ticket-close pings
token_file = "~/.claude/channels/telegram/.env"  # string — path to .env containing TELEGRAM_BOT_TOKEN
```

### Fields

#### `name` (string)

Human-readable workspace name. Used in CLI output (`livery init`, `livery doctor`) and in the generated `CLAUDE.md`. Defaults to the directory name if unset.

#### `description` (string)

One-liner shown in `CLAUDE.md`. Purely informational.

#### `default_runtime` (string, optional)

If set, subcommands that would otherwise require a runtime choice can fall back to this. Useful when your workspace is dominated by one stack. Leave unset to force explicit choices.

Valid values: `codex`, `claude_code`, `cursor`, `lm_studio`, `ollama`.

#### `cos_engines` (list of strings, optional)

Which CoS engines this workspace targets. Used by `livery upgrade-workspace` to decide which convention files (`CLAUDE.md`, `AGENTS.md`, ...) and skill directories (`.claude/skills/`, `.agents/skills/`) to manage. Written automatically by `livery init` based on `--cos-engine`.

Valid entries: `claude_code`, `codex`, `pi`, `opencode`. Pass any combination as a comma-separated value to `livery init --cos-engine`, e.g. `--cos-engine claude_code,pi`.

**Legacy workspaces without this field** (created before v0.5.0) get a one-time migration the first time `livery upgrade-workspace --apply` is run on them. The migration appends `cos_engines = ["claude_code", "codex", "pi", "opencode"]` to `livery.toml` (preserving all existing content + comments), then scaffolds files for every supported engine — the "full benefits" reading. To opt out of an engine afterward, edit the list. Subsequent upgrades respect whatever you leave there.

To **add a new CoS engine to an existing modern workspace**, edit the `cos_engines` list in `livery.toml` and re-run `livery upgrade-workspace --apply`. The framework will scaffold the new engine's convention file and skill directory. To **remove** an engine, just delete it from the list — `upgrade-workspace` won't actively delete the existing files, but it'll stop managing them.

#### `[telegram]` table

Optional. If present, Livery pings your configured chat when tickets close.

- **`chat_id`** — Telegram chat or group id. For a group, prefix with `-100`. For a topic inside a group, the format is `-100<group_id>/<topic_id>` (Livery handles topic parsing).
- **`token_file`** — Path to a `.env` file containing `TELEGRAM_BOT_TOKEN=...`. Defaults to `~/.claude/channels/telegram/.env` if unset. `~` and `$HOME` are expanded.

## `agents/<id>/agent.md`

Each hired agent lives at `agents/<id>/agent.md`. The frontmatter is the structured config; the body is a one-line role description (the long-form system prompt goes in `agents/<id>/AGENTS.md`).

### Schema

```yaml
---
id: writer                  # required — must match the directory name
name: Senior Writer         # required — human-friendly name
runtime: claude_code        # required — one of the supported runtimes
model: claude-sonnet-4-6    # optional for harness runtimes; required for lm_studio / ollama
cwd: /Users/me/code/repo    # required — where the agent works
reports_to: cos             # optional — default "cos"
hired: 2026-04-20           # optional — ISO date
---

One-line role: what this agent does, for whom.
```

### Fields

- **`id`** — short identifier. Used in ticket `assignee:` fields, in dispatch logs, and as the directory name. Lowercase, hyphenated, stable.
- **`name`** — human-readable name. Used in CLI output.
- **`runtime`** — the runtime module that handles dispatch. See `docs/runtimes.md`.
- **`model`** — the specific model id the runtime should use. For `lm_studio` / `ollama` this is required because Livery passes it on the HTTP call. For harness runtimes it's passed via `--model` when present, otherwise the harness's own default applies.
- **`cwd`** — the directory the agent operates in. For engineering agents, typically a git repo (so worktree-based dispatch works). `livery doctor` warns if the path doesn't exist or isn't a git repo.
- **`reports_to`** — informational; used by the CoS to understand team structure. Default: `cos`.
- **`hired`** — ISO date the agent was created. Informational.

Scaffold new agents with `livery hire <id>` rather than hand-writing these files — the wizard captures the structured fields, and leaves the AGENTS.md system prompt for you to flesh out with your CoS.

## `.livery-link.toml`

Project repos can be linked to a Livery workspace. This is for the common case where the workspace is the coordination HQ, while the actual source lives in one or more separate repos.

Create the link from the project repo:

```sh
cd ~/code/acme-api
livery link ~/work/acme-livery --repo-id api
```

The link file is intentionally small:

```toml
workspace = "/Users/me/work/acme-livery"
repo_id = "api"
workspace_id = "acme"
```

### Fields

- **`workspace`** — required path to a directory containing `livery.toml`. Absolute paths are simplest. Relative paths are resolved from the directory containing `.livery-link.toml`.
- **`repo_id`** — optional short name for the project repo, useful in CLI output and future ticket metadata.
- **`workspace_id`** — optional stable name for the workspace, useful when absolute paths differ across machines.

After linking, `livery where` shows how the current directory resolves:

```sh
Workspace: /Users/me/work/acme-livery
Source:    linked-repo
Marker:    /Users/me/code/acme-api/.livery-link.toml
Repo:      /Users/me/code/acme-api
Repo id:   api
```

By default, `livery link` adds `.livery-link.toml` to `.git/info/exclude` when the project has a normal `.git/` directory. That keeps machine-local absolute paths out of commits. Use `--no-exclude` if you intentionally want to commit the link file.

## `tickets/<id>.md`

Each ticket is a markdown file with frontmatter and three body sections (`## Description`, optional `## Context`, `## Thread`). `livery ticket new` scaffolds them; `livery ticket close` flips status and commits.

### Frontmatter schema

```yaml
---
id: 2026-04-29-001-livery-dispatch-prep-worktree-should-symlink-env  # required, matches filename stem
title: "livery dispatch prep --worktree should symlink .env into the worktree"  # required
assignee: cos                                # agent id, "cos", or null
status: open                                 # open | done | blocked
created: 2026-04-29T10:30:00Z                # ISO timestamp
updated: 2026-04-29T10:30:00Z                # ISO timestamp
blocked_on: "waiting on Airtable schema PR"  # optional — see below
---
```

### Fields

- **`status`** — one of:
  - **`open`** (default) — in the active queue, not yet resolved.
  - **`blocked`** — open but waiting on something. Surfaced in its own section by `livery status`.
  - **`done`** — completed. Set automatically by `livery ticket close` (default).
  - **`closed`** — synonym for `done` if you prefer that word. `livery ticket close --status closed`.
  - **`cancelled`** / **`abandoned`** / **`wontfix`** — terminal. Decided not to do. Set via `livery ticket close --status <one of these>`. Same pipeline as a regular close (writes the file, commits, pushes, pings Telegram), with a verb in the commit message and Telegram ping that matches the actual terminal status (`Cancel ticket X` rather than `Close ticket X`).

  The terminal set (everything that takes a ticket out of the open queue) is `done`, `closed`, `cancelled`, `abandoned`, `wontfix`. `livery status` excludes all of these from the open queue and includes them in the "recently closed" roll-up. Anything outside this set is treated as still-open — including `blocked` (which is open-but-paused, not terminal).

  If you need a custom terminal status, add it to `TERMINAL_STATUSES` in `livery/status.py` and document the addition here. Silent fall-through to "open" was the prior behavior and turned out to mask cancelled tickets, so we made the set explicit.

- **`blocked_on`** (optional) — a free-form string describing what the ticket is waiting on. An alternative to `status: blocked` for cases where the ticket is technically still open but parked. `livery status` treats either signal as "blocked" and renders accordingly. Use whichever fits your style.

### Staleness

`livery status` flags any open ticket that's been around for ≥ `--stale-days` (default 7) as stale. **Age is computed from `created`, not `updated`** — so a spec rewrite or a thread comment doesn't reset the staleness clock. The reasoning: "this work has been outstanding for N days" is a more useful signal than "the file changed N days ago," especially for tickets you re-scope mid-flight. If you want a different convention, override the threshold per-run with `--stale-days N` or change the `stale_days` parameter in `compute_status()`.

## `agents/<id>/AGENTS.md`

The long-form system prompt for the agent. Livery passes its contents as-is when dispatching a ticket. The `livery hire` wizard scaffolds it with section headers (`## Role`, `## Scope`, `## Out of scope`, `## Process`, `## Quality bar`, `## Output format`) that you fill in during a Claude Code session.

No schema is enforced — whatever you write is what the model sees. Write like you're onboarding an employee: specific, opinionated, non-negotiable where it matters.

## Workspace-level CoS files: `CLAUDE.md` and `AGENTS.md`

Not strictly config, but load-bearing. The workspace's top-level CoS convention file(s) auto-load in every CoS session that runs inside it. That's where your **CoS conventions** live — things like "push back at ≥70% confidence," "ping Telegram on ticket close," and any workspace-specific context future sessions will need.

Livery supports two CoS engines with different filename conventions:

- **`CLAUDE.md`** — read by Claude Code when you run `claude` in the workspace.
- **`AGENTS.md`** — read by Codex (and the growing de-facto `AGENTS.md` standard) when you run `codex` in the workspace.

`livery init` scaffolds both by default, so your workspace works with either engine out of the box. Use `--cos-engine claude_code` or `--cos-engine codex` to scaffold only one. Content of both files is identical; if you keep both, either sync them by hand or delete the one you don't use.

### The two files named `AGENTS.md` (and the two `agents`-ish directories)

There are two roles for files named `AGENTS.md` in a Livery workspace — don't confuse them:

- **`AGENTS.md` at the workspace root** — the Codex-convention CoS file. Read by Codex (or any AGENTS.md-aware tool) when the user opens their CoS session.
- **`agents/<id>/AGENTS.md`** — the system prompt for a hired agent. Read only by Livery's dispatch code, which includes its content in the prompt when dispatching a ticket to that agent.

They share a filename because both conventions landed on `AGENTS.md` independently. The paths disambiguate them (`./AGENTS.md` vs `./agents/<id>/AGENTS.md`), but a reader unfamiliar with both conventions may trip on it.

Similarly, there are two `agents`-ish directories at the workspace root:

- **`agents/`** (no leading dot) — Livery's hired-agents directory. Each subdir is one agent (`agents/<id>/`) containing `agent.md` (frontmatter config) and `AGENTS.md` (system prompt).
- **`.agents/`** (leading dot, only when `cos_engine` includes `codex`) — Codex's skill discovery directory. Codex scans `.agents/skills/<skill>/SKILL.md` from the cwd up to the repository root.

The leading dot disambiguates them on disk and in `ls -la`; otherwise no overlap.

## CoS skill files

When `cos_engine` includes Codex, Livery scaffolds skills at `.agents/skills/<name>/SKILL.md` (Codex's convention). When `cos_engine` includes Claude Code, the same skills go to `.claude/skills/<name>/SKILL.md` (plus a slash-command entry at `.claude/commands/<name>.md`).

Both `SKILL.md` formats use the same frontmatter (`name`, `description`) and prose body, so the file content is identical between engines — only the discovery path differs.
