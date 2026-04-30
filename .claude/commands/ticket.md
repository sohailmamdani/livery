---
description: Create a new Livery ticket
argument-hint: [title or brief description]
---

Help the user create a new Livery ticket.

If `$ARGUMENTS` is present, treat it as the starting title or description.

Steps:
1. Gather any missing details conversationally:
   - **title** — one-line, imperative ("Add login timeout", not "Login bug")
   - **assignee** — agent id from `agents/<id>/`, `cos` for Claude Code itself, or leave blank
   - **description** — one paragraph stating the goal
   - **context** (optional) — links, constraints, prior decisions
2. Run `bin/livery ticket new --title "..." --assignee <id|cos> --description "..." [--context "..."]`. Pass an empty description with `-d ""` only if the user wants to fill it in later.
3. Show the user the created file path and ticket id.
