# Harness API

Livery is meant to feel native inside coding harnesses such as Codex and
Claude Code. The CLI remains the installable local kernel, but harness skills
and slash commands should treat it as an API rather than as prose to copy.

## Layers

```text
Harness skills / slash commands = UI
CLI JSON commands = API surface
Python package = kernel
Markdown/TOML/JSON files = durable state
Git = audit log / sync layer
```

Harness-facing assets should stay visibly Livery-owned. Shipped Codex and
Claude skills use `livery-*` names, and Claude slash commands live under a
`livery/` command group, so Livery does not occupy generic harness names such
as `hello`, `ticket`, or `walkie`.

The markdown files are records, not the API. Harnesses can read them when that
is useful, but normal mutations should go through Livery commands so IDs,
frontmatter, paths, linked workspaces, dispatch attempts, and compatibility
rules stay consistent.

## JSON mode

Human-readable text remains the default. Harnesses and scripts should prefer
`--format json` on commands that support it:

```sh
livery where --format json
livery status --format json
livery agents --format json
livery ticket new --title "..." --assignee cos --description "..." --format json
livery ticket new --title "..." --assignee cos --repo api --description "..." --format json
livery ticket list --format json
livery ticket list --repo api --format json
livery ticket show <ticket-id> --format json
livery ticket close <ticket-id> --summary "..." --no-push --format json
livery memory add --type lesson --title "..." --body "..." --format json
livery memory search <query> --format json
livery dispatch prep <ticket-id> --worktree --format json
livery dispatch fan-out <ticket-id> --to a,b --format json
livery dispatch status --format json
livery dispatch tail <query> --format json
```

The JSON shape is the contract harness integrations should parse. Text output
is for people and may change to improve readability. New JSON responses include
a top-level `schema_version` so future incompatible changes have an explicit
migration point.

## MCP

An MCP interface can be added later as a thin adapter over the same Python
kernel. It should expose the same primitives as the JSON CLI, for example
`list_tickets`, `create_ticket`, `prepare_dispatch`, `get_dispatch_status`,
and `add_memory`.

MCP should not own Livery behavior. The source of truth remains the Python
core plus the file-backed records in the workspace.
