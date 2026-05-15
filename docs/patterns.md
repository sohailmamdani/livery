# Patterns

Worked examples of how Livery gets used in practice. These are intentionally hands-on — the shape of a real workspace, not abstract theory.

## One workspace per operational context

The foundational pattern. A **workspace** is the operational context your CoS is responsible for understanding: one directory, one `livery.toml`, one team of agents, one ticket backlog, one `CLAUDE.md` / `AGENTS.md` with that operation's conventions.

For a real company or client, that often means one workspace coordinating several repos. For an isolated one-off project, the project can have its own workspace. The boundary is not "company" or "repo" by itself. The boundary is: **should the same CoS share context, priorities, agents, and ticket history for this work?**

Agents work in *other* directories. Each agent's `agent.md` has a `cwd:` field that can point anywhere on your machine — a code repo, a writing project, a content folder. So a multi-repo operation typically looks like:

```
~/companies/indies-and-micros/    ← workspace (CoS HQ)
  livery.toml
  CLAUDE.md
  agents/
    research/agent.md             ← cwd: ~/code/branddb
    lead-dev/agent.md             ← cwd: ~/code/branddb
    writer/agent.md               ← cwd: ~/writing/i-and-m
  tickets/

~/code/branddb/                   ← agent cwd (code repo)
~/writing/i-and-m/                ← agent cwd (writing project)
```

You file tickets in the workspace. The CoS dispatches them to agents, who go do work in their own `cwd`. The workspace itself rarely contains the work product — it contains the **coordination**.

### Linked project repos

For convenience, a project repo can point back to its coordinating workspace with `.livery-link.toml`:

```toml
workspace = "/Users/me/companies/indies-and-micros"
repo_id = "branddb"
```

Create it from the project repo:

```sh
cd ~/code/branddb
livery link ~/companies/indies-and-micros --repo-id branddb
livery where
```

After that, running `livery status`, `livery ticket new`, or other workspace commands from inside `~/code/branddb` uses the linked workspace. The repo does not become a workspace; it just knows where its CoS HQ lives.

By default `livery link` adds `.livery-link.toml` to `.git/info/exclude` when the repo has a normal `.git/` directory. That keeps the absolute local path out of commits. If your team wants a committed link file, use `--no-exclude` and agree on paths that make sense for every machine.

### When to create a second workspace

Create a second workspace for genuinely separate operational contexts:
- Personal vs. client work
- Two unrelated domains (a research operation and a content operation)
- Different teams you don't want bleeding into each other's ticket queues
- A one-off project whose tickets, agents, and CoS context should stay self-contained

If you just have a new repo inside the same operation, link it to the existing workspace or point an agent's `cwd:` at it. Don't `livery init` in every related repo — you'll fragment your ticket queue and split one CoS brain into several smaller, less useful ones.

### Why one shared workspace can beat one-per-repo

1. **Ticket queue coherence.** One list of what's going on, not twelve. `livery ticket list` shows the state of the whole business.
2. **Shared CoS context.** The CoS knows about all your agents and all your active work. It can push a ticket between agents, chain tickets, notice patterns across projects.
3. **One Telegram channel.** One workspace = one mission control. N workspaces = N Telegram groups.
4. **Agents are reusable.** One `researcher` agent can pull tickets about five different projects, because projects are encoded in the ticket content, not the workspace boundary.

The one-workspace-per-repo anti-pattern is tempting because it matches how git works. It is wrong when the repos are part of the same operation. Livery is coordination, not source control. Your git repos are your project boundaries; your workspace is the CoS context that runs work across the repos that belong together.

## A research-and-editorial workspace

**Scenario.** You're building a small content operation about a niche topic (say, independent watch brands). You want:

- Deep research drafts on each brand, cited to primary sources.
- Data cleaning on a structured database of those brands.
- Editorial review before anything is published.
- Yourself in the loop on every draft, but not pushing every keystroke.

### Team shape

Four agents, each hired for a specific job:

| Id | Runtime | Model | Role |
|---|---|---|---|
| `research` | `claude_code` | `claude-sonnet-4-6` | Drafts brand profiles from primary + secondary sources. |
| `research-gpt` | `ollama` | local `gpt-oss` variant | Same role as `research`, for comparison. Zero API cost. |
| `lead-dev` | `codex` | `gpt-5-codex` | Data work: scrapes, imports, schema changes to the brand database. |
| `qa` | `claude_code` | `claude-sonnet-4-6` | Reviews crawl output and research drafts for schema compliance and source quality. |

Plus the **CoS** — your Claude Code session running in the workspace directory itself. The CoS is the thing you talk to all day; the hired agents are dispatched work.

### Workflow

1. **You file a ticket**, either through conversation with the CoS (`/ticket` slash command) or directly (`livery ticket new --title "..." --assignee research"`).
2. **CoS discusses scope** with you, pushes back on anything unclear, then either handles it directly (for `assignee: cos` tickets) or prepares a dispatch for the named agent.
3. **Dispatch runs in the background** with `livery dispatch prep <ticket> --worktree`. Output streams to `/tmp/livery-dispatch-<ticket>.out`. For engineering work you use `--worktree` so the agent can't step on your in-progress changes.
4. **The agent finishes** with a `=== DISPATCH_SUMMARY ===` block stating what it did, what it touched, and any pushback it wants you to see.
5. **CoS reads the summary**, reports to you, and — on your go-ahead — runs `livery ticket close <ticket> --summary "..."` which commits, pushes, and pings Telegram.

### Why two research agents?

Running the same ticket through `research` (Claude Sonnet 4.6) and `research-gpt` (local gpt-oss on Ollama) in parallel produces two drafts you can triangulate. The local one is free; the hosted one is sharper. On contentious brands, having both is useful — and on cheap brands, you can skip the hosted run entirely. This is only possible because Livery treats runtime as a per-agent attribute, not a global setting.

The CLI ergonomics are a single command:

```sh
livery dispatch fan-out <ticket-id> --to research,research-gpt --run
```

Each agent gets its own git worktree and its own output file, so they never step on each other. `--run` launches both subprocesses in parallel and waits; drop the flag to print the shell commands instead and run them yourself.

### Telegram in the loop

`livery.toml` points `[telegram]` at a group chat. Every `livery ticket close` pings the group. When you're away from the desk, you can see which agents finished what. Inbound slash commands (`/tickets`, `/close <id>`, `/dispatch <id>`) route back to `livery` and give you a mobile interface.

## An engineering-first workspace

**Scenario.** You're the only engineer on a small app. You want an agent that can pick up the kind of small, well-scoped refactors and bug fixes that pile up.

### Team shape

One agent.

| Id | Runtime | Model | Role |
|---|---|---|---|
| `refactor` | `codex` | `gpt-5-codex` | Picks up small, well-scoped refactors and bug fixes from the ticket queue. Works in a worktree; never touches main directly. |

### Workflow

1. You keep a backlog of well-scoped tickets (`livery ticket new --assignee refactor`).
2. When you have a free hour, you dispatch the top-of-queue ticket with `--worktree`.
3. You review the worktree's diff before merging. The agent never merges its own work.

Key constraint: **the agent's `AGENTS.md` must spell out the scope bar.** Codex will happily over-engineer if you let it. The quality bar for this agent is "minimum-diff fix, don't refactor surrounding code, report any drift from scope in the summary."

## A solo writing workspace

**Scenario.** You're writing a book. You want an agent that can do research passes on chapter topics, but writing itself stays in your hands.

### Team shape

```yaml
# agents/researcher/agent.md
id: researcher
runtime: claude_code
model: claude-sonnet-4-6
cwd: /Users/me/book-project
reports_to: cos
```

That's it. No engineering agents, no data agents. Most of your "tickets" are just CoS-handled notes: you chat with your CoS about the current chapter, it keeps track of what you've decided, and when you need a research pass it dispatches `researcher` with a clear brief.

The point: **Livery scales down as well as up.** A workspace with one agent and a good `CLAUDE.md` is a perfectly valid use.

## Anti-patterns

- **Hiring agents you don't need.** An unused agent is just noise in `livery doctor`. Hire when you have repeat work for a clear role, not speculatively.
- **Overloading one agent with multiple roles.** If `AGENTS.md` starts saying "and also…", it's probably two agents.
- **Skipping worktree dispatch for engineering agents.** Mainline-touching agents will race with you. Always `--worktree` for anything that edits code.
- **Putting secrets in `agent.md` or `AGENTS.md`.** Agent files get committed. Use env vars, token files referenced by path, or the harness's own secret store.
- **Treating the CoS as a generic assistant.** The CoS exists to push back on you, track workspace state, and delegate work. If you're using it as a chat buddy, you're undershooting.
