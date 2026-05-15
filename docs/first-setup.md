# First setup

A step-by-step walkthrough for getting Livery running end-to-end. By the end of this, you'll have a workspace, one hired agent, and a ticket dispatched and closed. Expect ~20 minutes.

If you've already skimmed the README and just want the conceptual model (one workspace per operational context, with linked project repos when useful), read [`docs/patterns.md`](patterns.md) first and come back.

**TL;DR:** after installing, create a dedicated workspace directory and run `livery onboard` — it chains the runtime check, workspace init, and first-agent hire into a single guided flow and tells you what to do next. The walkthrough below is the same path, broken out so you understand each step.

At any point, ask Livery what applies from your current directory:

```sh
livery next
livery capabilities
```

Both commands also support `--format json`, which is meant for Codex, Claude Code, and other CoS agents. The managed `CLAUDE.md` / `AGENTS.md` block tells agents to consult these commands instead of guessing from stale memory.

## Prerequisites

### 1. `uv`

Astral's Python tool manager. Livery is a Python package distributed as a `uv tool`.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify:

```sh
uv --version
```

### 2. At least one runtime

A runtime is what actually executes an agent's work. You don't need all of these — pick one or two for now.

- **Claude Code CLI** — `claude`. Install via Anthropic's instructions.
- **OpenAI Codex CLI** — `codex`. Install via OpenAI's instructions.
- **Cursor Agent CLI** — `cursor-agent`. Ships with Cursor.
- **Ollama** — local models. https://ollama.com.
- **LM Studio** — local models with a GUI. https://lmstudio.ai.

You'll verify what's reachable after install with `livery doctor`.

## Step 1: Install Livery

```sh
uv tool install --from git+https://github.com/sohailmamdani/livery.git livery
```

This puts a `livery` binary on your PATH. Verify:

```sh
livery --help
```

You should see the top-level commands: `init`, `hire`, `doctor`, `ticket`, `dispatch`, `telegram`.

### Updating later

```sh
uv tool upgrade livery
```

The install above floats with `main` (no `@v0.x.y` pin), so `uv tool upgrade` picks up new releases automatically. If you ever pinned the install with `@v...`, `uv tool upgrade` becomes a no-op — see the README's "Pinning to a specific version (advanced)" section for the recovery dance.

## Step 2: Check your runtimes

Run `livery doctor` from anywhere — it works without a workspace:

```sh
livery doctor
```

You'll see a block like:

```
Runtimes:
  [ok ] codex        bin=/opt/homebrew/bin/codex
  [ok ] claude_code  bin=/Users/you/.local/bin/claude
  [FAIL] cursor      bin=cursor-agent (not found)
  [FAIL] ollama      bin=ollama (not found)
           - `ollama` not on PATH
           - endpoint http://localhost:11434/api/tags unreachable
  [FAIL] lm_studio   http=down
```

Don't panic about `FAIL` rows for runtimes you don't use. You only need one runtime to do real work.

## Step 3: Create your workspace

A workspace is a directory for coordination: tickets, agents, CoS conventions, and runtime state. For a multi-repo operation, keep it somewhere you won't confuse with your actual projects. A common convention:

```sh
mkdir -p ~/companies/my-first-company
cd ~/companies/my-first-company
git init
```

The workspace being a git repo is strongly recommended — Livery commits ticket closes, and you'll want that history.

Now scaffold Livery:

```sh
livery init
```

Interactive mode asks for:

- **Workspace name** — human-readable. Defaults to the directory name.
- **One-line description** — optional.
- **Default runtime** — optional, leave blank if unsure.
- **Telegram chat id** — optional, skip for now; we'll come back to it.

(If you're running `livery onboard` instead, it also asks which CoS engine you want — Claude Code, Codex, or both. If you're calling `livery init` directly, pass `--cos-engine claude_code|codex|both`; the default is `both`.)

After `init`, your workspace looks like this (with `--cos-engine both` — the default):

```
my-first-company/
├── livery.toml                      # workspace marker + config
├── CLAUDE.md                        # CoS conventions, read by Claude Code
├── AGENTS.md                        # CoS conventions, read by Codex (same content)
├── agents/                          # where hired agents will live
├── tickets/                         # where tickets will live
├── .claude/                         # Claude Code skill discovery
│   ├── commands/ticket.md           # /ticket slash command
│   └── skills/new-ticket/SKILL.md
└── .agents/                         # Codex skill discovery (.agents/skills)
    └── skills/new-ticket/SKILL.md
```

`CLAUDE.md` and `AGENTS.md` have identical content — they're named for the two engines that auto-load them. Same with the `new-ticket` skill: it's scaffolded at `.claude/skills/new-ticket/SKILL.md` for Claude Code and `.agents/skills/new-ticket/SKILL.md` for Codex. Delete whichever pair you don't use, or keep both if you move between engines.

If you used `--cos-engine claude_code`, only `CLAUDE.md` and `.claude/` get scaffolded. If you used `--cos-engine codex`, only `AGENTS.md` and `.agents/` get scaffolded — no stray Claude-specific files in your workspace.

(Heads up on the naming: there's also `agents/<id>/AGENTS.md` inside each hired agent's folder, which is a different file with a different job — it's the agent's system prompt for dispatch. And `.agents/skills/` (with a leading dot) is the Codex skill dir — different from `agents/` (without a dot), which is Livery's hired-agents directory. See `docs/config.md` for the full disambiguation.)

Commit the scaffolding:

```sh
git add -A
git commit -m "Initial Livery scaffold"
```

### Optional: link a project repo to this workspace

If you want `livery` commands to work from inside a project repo while still using this workspace's ticket queue, link the repo:

```sh
cd ~/code/my-project
livery link ~/companies/my-first-company --repo-id my-project
livery where
```

This writes `.livery-link.toml` in the project repo. The file points to the workspace, but the repo does not become a workspace. By default, Livery adds the link file to `.git/info/exclude` because it contains a local absolute path.

If you accidentally created a full Livery workspace inside the project repo first, migrate that scaffolding into the shared workspace while linking:

```sh
cd ~/code/my-project
livery link ~/companies/my-first-company --repo-id my-project --move-existing-workspace
```

The migration moves the repo's tickets, agents, runtime metadata, and CoS scaffolding into the parent workspace. The repo is left with `.livery-link.toml`, and the old repo `livery.toml` is archived in the parent workspace under `.livery/linked-repos/<repo-id>/`.

For an isolated one-off project, it is also acceptable to create a dedicated Livery workspace for that project. The decision rule is whether the same CoS should share context, tickets, and agents across the work.

## Step 4: Edit `CLAUDE.md`

This is the single most important file. Claude Code auto-loads it every time you start a session in this directory — so it's how your **Chief of Staff** (CoS) knows what company it's running, what conventions to follow, and how to talk to you.

The scaffolded version gives you a skeleton. Open it and extend it with:

- **What this company does.** A paragraph, not a slogan.
- **Who the agents are** (you'll fill this in as you hire them).
- **Conventions the CoS should follow.** Things like:
  - "Push back at ≥70% confidence when you think I'm wrong."
  - "On ticket close, ping Telegram."
  - "Commit after every ticket mutation."
  - Any domain-specific rules ("always cite sources," "don't touch production data directly," etc.)

Write like you're onboarding an employee, not writing marketing copy.

## Step 5: Hire your first agent

Agents are where the real work happens. Each agent has:

- A **short id** (like `writer`, `research`, `lead-dev`). Becomes the directory name under `agents/`.
- A **runtime** (one of the runtimes `doctor` said was `ok`).
- A **model** (runtime-specific — e.g. `claude-sonnet-4-6` for `claude_code`, `gpt-5-codex` for `codex`).
- A **`cwd:`** — the directory the agent operates in. This is **not the workspace directory** — it's wherever the agent's actual work lives. A code repo, a writing folder, a data project.
- A **role** description.

Run:

```sh
livery hire writer
```

The wizard walks you through every field. For the first time, pick:

- **Runtime**: whatever's `ok` in `doctor` output.
- **Model**: the suggested default is fine.
- **`cwd:`**: pick (or create) a real directory. For example, `~/writing/my-blog`. If it doesn't exist yet, the wizard warns you; if it's not a git repo, it warns you too. Both are recoverable.
- **Role**: one line. What does this agent do, for whom?

When the wizard finishes, it prints:

```
Hired 'writer' (Writer) on claude_code
  + agents/writer/agent.md
  + agents/writer/AGENTS.md

Next: open agents/writer/AGENTS.md with your CoS and flesh out the system prompt.
```

## Step 6: Flesh out the agent's `AGENTS.md`

The `AGENTS.md` Livery scaffolded for you is a stub with section headers — not a finished system prompt. That's deliberate. A CLI wizard can capture structured config (runtime, model, cwd) but it can't extract *taste* from you through a sequence of terminal prompts.

The right way to write `AGENTS.md`: open your CoS session (Claude Code or Codex) in the workspace and write it **together**. Say something like:

> *"I just hired a writer agent. Help me write its AGENTS.md. The role is [role]. The main thing that should be non-negotiable is [X]."*

The CoS will ask clarifying questions, push back on scope, and produce a proper system prompt. The stub's section headers (`## Role`, `## Scope`, `## Out of scope`, `## Process`, `## Quality bar`, `## Output format`) give you the shape to fill.

If you'd rather just write it yourself, the minimum viable version is two paragraphs: what the agent does, and what "good work" looks like. You can expand over time.

## Step 7: Meet your CoS

```sh
cd ~/companies/my-first-company   # back to the workspace

# Whichever engine you've chosen:
claude                             # Claude Code — auto-loads CLAUDE.md
codex                              # Codex — auto-loads AGENTS.md
```

Whichever you opened, the engine reads its convention file (CLAUDE.md or AGENTS.md) and takes the role of **your CoS** for this company. Both engines work the same way for Livery — it's all shell commands, ticket markdown, and conversation. Talk to the CoS like you would a chief of staff:

- "What do we have going on today?" → it runs `livery ticket list`.
- "I want to write a blog post about X. File a ticket for the writer agent." → it files a ticket.
- "Dispatch that ticket." → it composes the prompt and kicks off the dispatch.

You don't need to memorize `livery` commands. You can, and sometimes running them directly is faster — but the daily interface is conversational.

## Step 8: File and run your first ticket

### The CoS-assigned ticket (for work you want the CoS itself to do)

The CoS can take on tickets directly. Try:

```sh
livery ticket new --title "Write out my company's goals for this quarter" --assignee cos
```

Or just tell the CoS: *"File a ticket for yourself to write out my company's goals this quarter."*

CoS-assigned tickets aren't dispatched — the CoS handles them conversationally, in the same Claude Code session. When done, close the ticket:

```sh
livery ticket close <ticket-id> --summary "Drafted in CLAUDE.md under ## Goals."
```

That commits, pushes, and (if configured) pings Telegram.

### The agent-assigned ticket (dispatched work)

For work your hired agent should do:

```sh
livery ticket new --title "Draft a 300-word post about Y" --assignee writer
```

Prep the dispatch:

```sh
livery dispatch prep 2026-XX-XX-001-draft-a-300-word-post --worktree
```

The `--worktree` flag creates a git worktree at the agent's `cwd:` on a ticket-specific branch. Use it whenever the agent touches code or files you care about — it prevents the agent from stepping on your in-progress work.

Livery prints a shell command at the end. Run it (usually in the background, so you can keep working):

```sh
# bash example — runs in background, writes output to /tmp/livery-dispatch-<ticket>.out
<paste the printed command> &
```

Wait for it to finish. When it does, look at the output file for the `=== DISPATCH_SUMMARY ===` block. That's the agent's report: what it did, what it touched, any pushback it wants you to see.

Review the work. If it's good:

```sh
livery ticket close <ticket-id> --summary "Merged from worktree. Shipped."
```

If it needs revision, append notes to the ticket's `## Thread` and dispatch again.

## Step 9 (optional): Telegram

Telegram makes Livery feel alive. Every ticket close pings a chat. Inbound slash commands (`/tickets`, `/close <id>`, `/dispatch <id>`) give you a mobile interface.

Setup:

1. Make a Telegram bot via `@BotFather`. Save the token.
2. Put the token in a `.env`:
   ```sh
   mkdir -p ~/.claude/channels/telegram
   echo 'TELEGRAM_BOT_TOKEN=<your-token>' > ~/.claude/channels/telegram/.env
   ```
3. Get the chat id you want to ping. For a group, add the bot, send a message, and query Telegram's API for `getUpdates`.
4. Edit `livery.toml`:
   ```toml
   [telegram]
   chat_id = "-1001234567890"
   # token_file defaults to ~/.claude/channels/telegram/.env
   ```
5. Register the bot's slash commands:
   ```sh
   livery telegram register-commands
   ```

Now `livery ticket close` pings the group, and you can type `/tickets` in Telegram to see what's open.

## Day-to-day commands you'll come back to

Three commands that aren't part of first-time setup but become routine:

- **`livery status`** — at-a-glance dashboard. Open tickets by assignee, stale ones (open ≥ 7 days), blocked ones, recent closes, runtime health. The companion to `livery ticket list` (which is the flat scriptable cut). Run it whenever you sit down to work and want to know where the team stands.
- **`livery dispatch fan-out <ticket> --to a,b --run`** — dispatch the same ticket to multiple agents in parallel. Each agent gets its own git worktree and output file. Useful for triangulating research outputs across different models, or running the same engineering ticket through Codex and Claude Code for comparison.
- **`livery upgrade-workspace`** — after a `uv tool upgrade livery` brings in a new framework version, this refreshes the framework-managed parts of your workspace (the `LIVERY-MANAGED` block in `CLAUDE.md`/`AGENTS.md`, shipped skill files) without touching your custom content. Dry-run by default; pass `--apply` to actually write.

## What to do next

- **Extend `CLAUDE.md`** as you learn what your CoS needs to know. Treat it as a living document.
- **Hire more agents** as distinct roles emerge. "If `AGENTS.md` starts saying 'and also…', it's probably two agents."
- **Don't create a second workspace** just because you started a new project. A new project is a new agent `cwd:` or a new agent, not a new company.
- **Read [`docs/patterns.md`](patterns.md)** for worked examples and anti-patterns.
- **Read [`docs/runtimes.md`](runtimes.md)** when you want to understand why tool use works differently for different agents.
- **Read [`docs/config.md`](config.md)** when you want the full `livery.toml` and `agent.md` schema.

## Troubleshooting

### `livery: command not found` after install

`uv tool install` puts binaries in `~/.local/bin`. Make sure that's on your PATH. On bash/zsh:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Add that to your shell rc file.

### `livery doctor` shows my runtime as FAIL

Either:
- The binary isn't on PATH (for CLI runtimes). Install it or adjust PATH.
- The local HTTP server isn't running (for LM Studio / Ollama). Start it.

You don't need *all* runtimes — one is enough.

### `livery ticket close` fails on `git push`

Check that the workspace has a git remote set. If you haven't pushed yet:

```sh
git remote add origin <url>
git push -u origin main
```

Or pass `--no-push` to `livery ticket close` if you don't want to push.

### The dispatched agent's output looks empty / stuck

Check the dispatch output file:

```sh
tail -f /tmp/livery-dispatch-<ticket-id>.out
```

For raw-LLM runtimes (`lm_studio`, `ollama`), make sure the model is actually loaded in your local server. `livery doctor` confirms the server is reachable, not that a model is loaded.
