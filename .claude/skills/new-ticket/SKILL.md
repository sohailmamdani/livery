---
name: new-ticket
description: Create a new Livery ticket to track work or delegate to an agent. Use when the user says "create a ticket", "new ticket", "file a ticket", invokes `/ticket`, or describes a unit of work that should be formalized for tracking or delegation.
---

# Create a Livery ticket

Livery tracks work as markdown tickets in `tickets/<YYYY-MM-DD>-<NNN>-<slug>.md`. Each ticket has frontmatter (id, title, assignee, status, timestamps) and body sections (`## Description`, optional `## Context`, `## Thread`).

## When to invoke

- User types `/ticket` or asks to "create/file/open a ticket"
- User describes a unit of work that belongs in the backlog or should be delegated to another agent

## Fields

- **title** — one-line, imperative ("Add login timeout", not "Login bug")
- **assignee** — agent id from `agents/<id>/`, `cos` for Claude Code itself, or blank for unassigned
- **description** — one paragraph stating the goal (not the implementation)
- **context** (optional) — links, constraints, prior decisions

## Steps

1. Gather any missing fields conversationally. Don't over-ask — title + assignee + a one-paragraph description is enough to create.
2. Run:
   ```
   bin/livery ticket new --title "..." --assignee <id|cos|null> --description "..." [--context "..."]
   ```
3. Show the created path and id to the user.

## Conventions

- Titles imperative, not gerund or noun-phrase.
- Descriptions state the goal, not the steps.
- Context is skipped if empty — don't invent it.
- Leave assignee blank if routing isn't decided yet; fill it when dispatching.
