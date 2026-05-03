# Changelog

All notable changes to Livery. Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semver-ish (breaking changes to CLI surface or config shape bump the minor version during 0.x).

## Unreleased

## 0.8.4 — 2026-05-02

### Added
- `livery init` and `livery onboard` now proactively offer to install the pre-commit sync-cos hook when (a) multiple CoS convention files were just scaffolded, (b) the workspace is a git repo, and (c) the session is interactive. Default answer is "yes" — saying yes installs the hook in one go; saying no leaves the existing "Tip: `livery install-hooks`" line as the fallback. Non-interactive runs (scripts, CI) skip the prompt silently.

## 0.8.3 — 2026-05-02

### Added
- `livery install-hooks` — installs Livery's pre-commit hook into `.git/hooks/`. The hook runs `livery sync-cos --apply` before each commit and re-stages any convention files the sync touched, so CLAUDE.md / AGENTS.md don't drift between commits. Idempotent: re-running refreshes the hook if it's drifted from the shipped content. Refuses to overwrite user-written hooks without `--force`. Pass `--uninstall` to remove. Hooks are NOT auto-installed by `init` or `upgrade-workspace` — `.git/hooks/` is your territory; this is opt-in.
- `livery init`'s "Next steps" output now mentions `install-hooks` when more than one CoS convention file is scaffolded.

### Other
- Tests: 159 (was 148). The 11 new tests cover install/refresh/uninstall paths, the user-written-hook detection, and `--force` overwrite semantics.

## 0.8.2 — 2026-05-02

### Added
- `livery sync-cos` — mirror user content from one convention file (`CLAUDE.md`, `AGENTS.md`, …) to all its siblings. Source defaults to whichever sibling was modified most recently; override with `--from FILENAME`. Each target's `LIVERY-MANAGED` block is refreshed to current as part of the sync. Dry-run by default; `--apply` writes. Closes the drift gap surfaced by real usage — v0.8.1's mirror-on-create only helped when files didn't exist; this handles ongoing drift between existing siblings.

## 0.8.1 — 2026-05-02

### Fixed
- `livery upgrade-workspace` now **mirrors user content from a sibling convention file** when creating a new one. Previously, opting into Codex on a workspace that already had `CLAUDE.md` produced a bare-template `AGENTS.md` with the framework block but none of the user's customizations (workspace conventions, hired-agent notes, project-specific rules). Now it copies the sibling's user-editable section into the new file with the framework's managed block refreshed to current. Falls back to the bare template only when no sibling exists (fresh init case). Handles legacy sibling files with no `LIVERY-MANAGED` markers — prepends a fresh block to their content.

### Migration note for existing users
- If you already opted into a new engine on a v0.8.0 install (or earlier) and ended up with a bare-template AGENTS.md (or CLAUDE.md), regenerate it via the new mirror behavior:

  ```sh
  rm AGENTS.md  # or CLAUDE.md
  livery upgrade-workspace --apply
  ```

  The framework will recreate it from the sibling's content, with your customizations carried over.

## 0.8.0 — 2026-05-02

### Added
- `livery upgrade-workspace` now performs a one-time migration on legacy workspaces (created before `cos_engines` existed in `livery.toml`). The migration appends `cos_engines = ["claude_code", "codex", "pi", "opencode"]` to `livery.toml` (preserving all existing content + comments) and then scaffolds files for every currently-supported engine. After migration, the workspace is on the modern config and subsequent upgrades respect whatever the user leaves in the `cos_engines` list. New `Action.MIGRATE` plan item type renders as `[migrate]` in dry-run output. (Closes the gap where existing workspaces couldn't get AGENTS.md and other Codex/Pi/OpenCode files added retroactively.)

### Changed
- `livery upgrade-workspace`'s "never touches livery.toml" rule now has one documented exception: the legacy migration above. The migration is additive only (writes a missing field), idempotent (no-op on second run), and TOML-aware (insertion happens before any `[section]` headers so the file stays parseable). Workspaces with `cos_engines` already set are still untouched.

## 0.7.1 — 2026-05-02

### Changed
- README's install section reorganized: floating-with-`main` is now the headline command, and pinning is documented as an "advanced" variant with an explicit caveat that pinning disables `uv tool upgrade livery` (uv re-resolves the same git ref each time and concludes nothing changed). Adds the recovery dance: reinstall with the new tag (or drop the pin entirely) to move the install forward. `docs/first-setup.md` cross-references the same caveat. Documentation-only release prompted by a real bite from the maintainer's own usage.

## 0.7.0 — 2026-05-01

### Added
- `livery dispatch status` — rollup of every dispatch artifact in `--output-dir` (default `/tmp`). Classifies each dispatch as **done** (output contains a `=== DISPATCH_SUMMARY ===` block), **active** (recent file activity, no summary yet), or **stale** (file hasn't moved in 5+ minutes and never produced a summary — likely crashed or stuck). Shows summary excerpt for done dispatches, last line for in-flight ones. ANSI-colored on TTY.
- `livery dispatch tail <query>` — one-shot or follow (`-f`) tail of a specific dispatch's output file. Resolves the dispatch via substring match against the `<ticket-id>-<assignee>` filename label; errors cleanly on no match or ambiguous match.
- `livery/dispatch_view.py` — pure-data scanner for dispatch artifacts, exposes `list_dispatches()` and `find_dispatch()` for downstream code.

## 0.6.4 — 2026-05-01

### Added
- `livery ticket close --status <terminal-status>` — pick a non-default terminal status when closing a ticket. Accepts `done` (default), `closed`, `cancelled`, `abandoned`, `wontfix`. Reuses the regular close pipeline (writes the file, git commits, pushes, pings Telegram), but the commit subject and Telegram ping use a verb that matches the chosen status (`Cancel ticket X` rather than `Close ticket X`). Closes the gap from v0.6.3, where `cancelled` was a recognized terminal status with no CLI to set it.

### Changed
- "Already closed" check now rejects re-closing on *any* terminal status, not just `done`. Trying to close a ticket that's already `cancelled` errors out cleanly instead of double-flipping.

## 0.6.3 — 2026-05-01

### Fixed
- `livery status` no longer treats `cancelled` (or `abandoned`, or `wontfix`) tickets as open. Previously the open/closed split was an exclusion of just `{done, closed}`, so any other terminal status leaked into the active queue and inflated stale counts. Terminal statuses are now an explicit set documented in `docs/config.md`.

### Changed
- `livery/status.py` exposes a public `TERMINAL_STATUSES` constant. New custom terminal statuses are an opt-in addition there (and a doc note in `docs/config.md`) — silent fall-through to "open" was the wrong default.
- `docs/config.md` now documents the staleness convention explicitly: age is computed from `created`, not `updated`, so spec rewrites and thread comments don't reset the staleness clock.

## 0.6.2 — 2026-04-30

### Added
- `livery --version` (alias `-v`) — prints the installed package version. Resolves from `importlib.metadata` for installed builds, falling back to `pyproject.toml` when running from the dev tree.

### Changed
- `livery upgrade-workspace` now prints the running Livery version at the top of its output and clarifies the "nothing to do" case: it spells out that the workspace scaffolding is current with the running Livery version, and points users at `uv tool upgrade livery` for catching up the binary itself. The two commands have always done different things; the names made that ambiguous, so the wording now says it explicitly.

## 0.6.1 — 2026-04-30

### Changed
- README, `docs/first-setup.md`, `CONTRIBUTING.md`, and `bin/livery` install instructions updated to reflect the public repo. Install URL switched from `git+ssh://` to `git+https://`; the SSH-access prerequisite and "pre-1.0 private repo" framing are gone. Pinned-version install variant added. Documentation-only release.

## 0.6.0 — 2026-04-29

### Added
- `livery status` — at-a-glance dashboard for the workspace. Groups open tickets by assignee with oldest-age signal, surfaces stale tickets (open ≥ 7 days, configurable via `--stale-days`), surfaces blocked tickets (either `status: blocked` or `blocked_on: <reason>` in frontmatter), shows the most recent ticket closes, and reports runtime health. Companion to `livery ticket list` — that's the raw scriptable cut, this is the human rollup. ANSI-colored on TTY, plain when piped.
- `blocked_on` frontmatter field convention. Optional; tickets with `blocked_on: "<reason>"` are surfaced separately from stale tickets in `livery status`. Either `status: blocked` or `blocked_on: ...` works — both routes are equivalent.

## 0.5.0 — 2026-04-25

### Added
- **CoS engine registry** at `livery/cos_engines.py` — adding a new engine is now a ~10-line entry. Ships with `claude_code`, `codex`, `pi` ([pi-mono](https://github.com/badlogic/pi-mono)), and `opencode` ([opencode.ai](https://opencode.ai/)) supported out of the box.
- `livery upgrade-workspace` — refresh framework-managed scaffolding without touching user content. Dry-run by default; `--apply` writes; `--force` overrides customization warnings. Compares the workspace against what `livery init` would produce today and creates missing files / refreshes stale framework blocks. Hard guardrail: never touches `livery.toml`, `agents/`, `tickets/`, or anything outside the LIVERY-MANAGED markers in convention files.
- `--cos-engine` now accepts a comma-separated list of engines (`pi,opencode`) in addition to the historical single value or `both` alias.
- `cos_engines = [...]` field in `livery.toml`, written at init time, used by `upgrade-workspace` to know which engines to manage. Legacy workspaces without this field fall back to detection from existing files.

### Changed
- CoS convention files (`CLAUDE.md`, `AGENTS.md`, ...) now have a framework-managed block fenced by `<!-- LIVERY-MANAGED:BEGIN -->` ... `<!-- LIVERY-MANAGED:END -->` markers at the top, with user-editable content below. This is what makes `livery upgrade-workspace` safe — the framework owns the marked block, the user owns everything outside it. Existing workspaces (no markers) get a managed block prepended on first `upgrade-workspace` run; user content is preserved verbatim below.
- Onboarding's CoS-engine prompt now lists every registered engine.

## 0.4.1 — 2026-04-25

### Fixed
- `livery init --cos-engine codex` no longer scaffolds the Claude-Code-specific `.claude/` directory — Codex users now get a clean workspace without dead Claude-only files. Codex's own skill format is supported instead: `livery init` writes `.agents/skills/new-ticket/SKILL.md` (Codex's convention) when the engine choice includes Codex. `--cos-engine both` produces both `.claude/skills/` and `.agents/skills/` with identical SKILL.md content.

## 0.4.0 — 2026-04-24

### Added
- `livery init --cos-engine <claude_code|codex|both>` — pick which CoS convention file(s) to scaffold. `claude_code` writes only `CLAUDE.md`, `codex` writes only `AGENTS.md`, `both` (default) writes both with identical content. Codex users can now use Livery as their orchestration layer without a stray `CLAUDE.md` in their workspace.
- `livery onboard` prompts for the CoS engine when creating a new workspace.

### Changed
- Scaffolded CoS templates are now engine-neutral — wording refers to "your Claude Code or Codex session" instead of assuming Claude Code. Existing workspaces are unaffected; this applies only to freshly-scaffolded files.
- Post-init "Next:" messaging and `livery onboard`'s "Next steps" output adapt to which CoS file(s) got scaffolded.

## 0.3.0 — 2026-04-23

### Added
- `livery onboard` — stateful guided setup. Checks your runtimes, offers to create a workspace if you're not in one, offers to hire a first agent if none exist, then points at next steps (open Claude Code, flesh out `AGENTS.md`, file your first ticket). Idempotent — safe to re-run at any point; skips steps you've already completed.
- `livery dispatch fan-out <ticket> --to a,b,c` — dispatch one ticket to multiple agents in parallel. Each gets its own worktree, prompt file, and output file. Prints N shell commands by default; `--run` launches them in parallel and waits for completion.

### Changed
- Dispatch prompt and output filenames now include the assignee (`livery-dispatch-<ticket>-<agent>.{txt,out}`). This makes fan-out-safe by default and makes it unambiguous which file belongs to which agent; single-agent dispatches get a slightly longer filename but nothing breaks.
- Worktree names now include the assignee when one is set (`<repo>-<agent>-t<suffix>`), so fan-out into a shared `cwd:` doesn't collide.

## 0.2.0 — 2026-04-21

### Added
- `livery hire <id>` — interactive wizard (or flag-driven) to scaffold a new agent. Writes `agents/<id>/agent.md` with structured frontmatter plus an `AGENTS.md` stub with section headers the user fleshes out with their CoS.
- `livery doctor` — reports which runtimes are reachable (codex / claude / cursor-agent / ollama on PATH; LM Studio at :1234; Ollama at :11434). Inside a workspace, also validates each hired agent's `cwd` and runtime. Supports `--json`.
- Interactive `livery init` — prompts for workspace name, description, default runtime, and Telegram config when stdin is a TTY. `--no-interactive` preserves the old flag-driven behavior for scripting.
- `LICENSE` (MIT).
- `docs/runtimes.md`, `docs/config.md`, `docs/patterns.md`, `docs/first-setup.md` — reference documentation and a step-by-step first-time setup walkthrough. `patterns.md` now opens with the foundational "one workspace per company, not per project" pattern.
- `CONTRIBUTING.md`.

### Changed
- README audited for public-repo release. Install section now clearly documents the pre-1.0 private-repo gate.

## 0.1.0 — 2026-04-20

First distributable release.

### Added
- `livery.toml` as the workspace marker.
- `livery init` to scaffold a fresh workspace (`livery.toml`, `CLAUDE.md`, `agents/`, `tickets/`, `.claude/commands/ticket.md`, `.claude/skills/new-ticket/SKILL.md`).
- `bin/livery` dev wrapper that preserves the user's cwd when invoked through the in-repo `.venv`.
- Install flow via `uv tool install --from git+ssh://...` verified end-to-end.

### Changed
- Extracted the framework from the self-hosted workspace layout. The maintainer's personal workspace content moved to a separate directory; this repo now contains only Python code, tests, and documentation.
