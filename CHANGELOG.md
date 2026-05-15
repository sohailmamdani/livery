# Changelog

All notable changes to Livery. Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semver-ish (breaking changes to CLI surface or config shape bump the minor version during 0.x).

## Unreleased

### Added
- Linked project repos. `livery link <workspace>` writes `.livery-link.toml` in a project repo so commands run from that repo operate on the linked workspace. `livery where` shows whether the current directory resolved through a workspace marker, linked repo marker, or legacy marker.
- Linked-repo cleanup migration. `livery link <workspace> --move-existing-workspace` converts a repo that was accidentally initialized as its own Livery workspace into a linked repo by moving its workspace scaffolding into the parent workspace, preserving the old `livery.toml` under `.livery/linked-repos/<repo-id>/`, then writing `.livery-link.toml`.
- Discoverability commands. `livery capabilities` prints the feature menu, and `livery next` inspects the current directory and suggests the most relevant next actions. Both support `--format json` so CoS agents can consume the same live framework truth as humans.

### Changed
- Workspace guidance now frames the boundary as an operational context rather than strictly "one company, not one project." Multi-repo operations should share a workspace; isolated one-off projects may have their own.
- The managed CoS convention block now tells Claude Code, Codex, and other CoS agents to run `livery next` / `livery capabilities` instead of guessing which Livery features exist.

## 0.10.1 — 2026-05-15

### Fixed
- `livery walkie auto <topic> --resume` no longer requires `--peer-a` / `--peer-b`; resumed walkies read their declared peers from the walkie file frontmatter.
- `livery walkie auto --ticket <query>` now resolves the ticket before creating the walkie and fails clearly if it cannot find one.
- Walkie auto turns now count as successful advancement only when the dispatched peer appends exactly the expected turn number. Wrong-peer, wrong-number, and multi-turn appends are treated as stalls.
- Walkie auto timeouts now terminate the launched runtime process group, and Ctrl+C waits for the in-flight turn to finish before surfacing the abort.

## 0.10.0 — 2026-05-13

The **Walkie-Talkie auto-mode** release. v0.8.x's manual walkie-talkie protocol (markdown file + append-only turns + sign-to-converge) now has a controller that drives the loop automatically: each turn is a Livery dispatch, peers alternate by themselves, and the operator only writes the briefing once.

### Added
- **`livery walkie auto <topic>`** — automated walkie-talkie controller. Two declared peers (hired Livery agents) alternate appending turns until both sign or `--max-turns` is reached. Flags: `--peer-a`, `--peer-b`, `--briefing` (inline or `@file`), `--ticket` (canonical question lives in a ticket; its markdown is embedded in every turn), `--max-turns` (default 20), `--turn-timeout` (default 600s), `--resume` (continue an existing walkie).
- **`prepare_walkie_turn`** (in `livery/dispatch.py`) — sibling to `prepare_dispatch`. Builds a three-layer prompt for one turn: peer's `AGENTS.md` (identity) + optional briefing / ticket markdown (constant debate context) + walkie task template (read the file, take Turn N, follow protocol, exit). Prompt lands under `.livery/walkie-talkie/prompts/<attempt-id>.txt`; output to `/tmp` so existing `dispatch status` / `dispatch tail` machinery still works.
- **`livery/walkie_controller.py`** — the loop. `run_controller` parses the walkie file, picks the next peer via `decide_next_peer` (alternates by last turn header), calls `prepare_walkie_turn`, spawns the runtime as a subprocess, waits, marks the attempt finished, and re-parses to confirm a turn was appended. Stops on lock, stall, runtime failure, timeout, or max-turns — each with an explicit reason.
- **Per-turn dispatch attempts.** Each walkie turn is a full `DispatchAttempt` with its own JSON record under `.livery/dispatch/attempts/`. PID, exit code, lifecycle timestamps, hook outcomes — same audit trail as any other dispatch. Attempt ids carry the walkie label (`walkie-<topic-slug>-tNNN-<peer>-<ts>-<hex>`).
- **Briefing + peers + ticket in walkie frontmatter.** `new_walkie` accepts `briefing`, `peers`, and `ticket_id` arguments. Briefing lands in a `## Briefing` section above the protocol; peers and ticket go in frontmatter so the controller can resume statelessly. `parse_walkie` reads them back as `WalkieFile.declared_peers` and `WalkieFile.ticket_id`.
- **Hook integration per turn.** `before_run` and `after_run` dispatch hooks (from v0.9.0) fire on every walkie turn. Configure `[dispatch_hooks].after_run = "..."` in `livery.toml` to get a Telegram ping on each turn for free.

### Failure modes (explicit, no silent retries)
- **Peer ran but didn't append a turn** → `ControllerStep.advanced=False`; loop stops with `stalled` reason. Operator can investigate the attempt's output file and resume.
- **Peer's runtime exited non-zero** → loop stops; attempt JSON has `failure_class=runtime_error` and the exit code.
- **Per-turn timeout** → attempt marked `stale` with `failure_class=runtime_error` and "timed out after Ns" detail; loop stops.
- **Ctrl+C** → in-flight attempt completes, then loop bails. Walkie file is preserved; `--resume` picks up at the next turn.

### Mental model
- Manual walkie (`livery walkie new`) is still there — useful when you want full control over each turn or are pairing with a human.
- Auto walkie (`livery walkie auto`) is the standard mode for AI-to-AI debate. The briefing is the constant frame; the walkie file is the evolving transcript; each turn is a dispatch attempt.

## 0.9.0 — 2026-05-07

The **attempt-backed dispatch lifecycle** release. Implementation plan was hashed out and signed by Claude + Codex via the walkie-talkie protocol (see `walkie-talkie.md` and the `CLAUDE-SPEC-INTEGRATION-PLAN.md` doc on the `symphony-spec-analysis` branch). 0.8.6 already shipped the path-safety prep work; this release adds durable attempt records, attempt-aware status reporting, and user-configurable lifecycle hooks.

### Added
- **Durable dispatch attempt records.** Every `prepare_dispatch` now writes a JSON record to `<workspace>/.livery/dispatch/attempts/<attempt-id>.json` capturing the full dispatch context: ticket id, assignee, runtime, model, workspace + agent cwd, worktree path, prompt path, output path, generated command, status, timestamps, hooks, hook warnings. Schema is versioned (`schema_version=1`); future Livery versions can add fields additively. Atomic writes via `<file>.json.tmp` + `os.replace`. Attempt id format: `<ticket-id>-<assignee>-<YYYYMMDDTHHMMSSZ>-<4hex>` (sortable + collision-resistant + ticket-prefix-globbable).
- **Attempt status lifecycle.** New `AttemptStatus` enum: `prepared` → `running` → (`succeeded` | `failed` | `blocked` | `stale` | `cancelled` | `unknown`). `livery dispatch ... --run` updates the attempt JSON as the subprocess transitions states (running with PID on launch, finished with exit code on completion, cancelled on Ctrl+C). New `FailureClass` enum (`ticket_error`, `agent_config_error`, `workspace_error`, `runtime_error`, `hook_error`, `notification_error`) for structured failure reporting.
- **`livery dispatch status` is now attempt-aware.** When run inside a workspace, it reads `.livery/dispatch/attempts/*.json` first and falls back to `/tmp/livery-dispatch-*.out` scanning for any legacy or manually-launched dispatches without an attempt record. Compatibility contract: new dispatches → attempt JSON canonical; old / manual → output scanning; both → JSON wins, output tail fills missing summary/last-line. Output now shows the richer `AttemptStatus` (including `failed / hook_error`, `running`, `cancelled`, etc.) instead of the coarse done/active/stale classification, plus the workspace path the dispatches belong to. `livery dispatch tail` likewise resolves attempts when available.
- **Read-time status inference for prepared attempts.** A `prepared` attempt with a `=== DISPATCH_SUMMARY ===` block in its output displays as `succeeded`; one whose output file has gone stale (no summary, mtime > 5 min) displays as `stale`. Inference is display-only — never written back to the attempt JSON.
- **Dispatch lifecycle hooks.** New `[dispatch_hooks]` table in `livery.toml` configures shell commands to run at well-known points in the attempt lifecycle: `after_worktree_create` (fires only when a worktree is made), `before_run` (immediately before the runtime launches), and `after_run` (after the runtime exits). Pre-run hook failures are blocking — the attempt is marked failed with `failure_class=hook_error` and the runtime does not launch. Post-run hook failures are advisory — recorded in `attempt.hook_warnings` without changing the runtime-determined status. Each hook captures stdout+stderr to `<workspace>/.livery/dispatch/hooks/<attempt-id>-<hook-name>.log` and records a `HookOutcome` (exit code, duration, log path, started_at) in the attempt JSON. Hooks see dispatch context via env vars: `LIVERY_TICKET_ID`, `LIVERY_ASSIGNEE`, `LIVERY_RUNTIME`, `LIVERY_MODEL`, `LIVERY_CWD`, `LIVERY_ATTEMPT_ID`, `LIVERY_ATTEMPT_PATH`, `LIVERY_PROMPT_PATH`, `LIVERY_OUTPUT_PATH` (always); `LIVERY_EXIT_CODE` on `after_run` only. Default per-hook timeout 60 s (treated as exit 124).
- **`livery/attempts.py`** — full module: `DispatchAttempt` and `HookOutcome` dataclasses, atomic `write_attempt`/`load_attempt`, ticket-scoped `find_attempts_for_ticket` and chronological `list_attempts`, lifecycle helpers `mark_running` / `mark_finished`. `ensure_attempts_dir` also drops a `.livery/.gitignore` so the framework's bookkeeping never lands in user commits.
- **`livery/dispatch_hooks.py`** — hook executor with blocking (`run_pre_run_hook`) and advisory (`run_post_run_hook`) wrappers.

### Changed
- `DispatchPrep` gained `attempt_id` and `attempt_path` fields. They are populated whenever `prepare_dispatch` succeeds (every dispatch now has a durable record).
- `dispatch_view.list_dispatches` and `find_dispatch` now accept an optional `workspace_root` argument. When provided, attempt-backed views are surfaced first; without it the old /tmp-only behavior still works (CLI still functions outside a workspace).
- `DispatchView` gained two display-only fields: `attempt: DispatchAttempt | None` (the underlying record, when there is one) and `inferred_status: AttemptStatus | None` (read-time inference for prepared attempts).

### Compatibility
- Forward-compat: a status string in attempt JSON that this Livery doesn't recognize is loaded as `AttemptStatus.UNKNOWN` rather than failing.
- Backward-incompat: a `schema_version` higher than this Livery's refuses to load (`ValueError`) — better than silently dropping unknown fields.
- Existing workspaces work unchanged; the `.livery/` directory is created on the first dispatch in this version.

## 0.8.6 — 2026-05-07

### Added
- `livery/paths_safety.py` — `sanitize_path_component` and `assert_path_contained` helpers for generated worktree paths. Defends against ticket-id and agent-id values containing path-traversal characters (`..`, `/`, control chars, etc.). `ensure_worktree` now sanitizes its inputs and verifies the resulting path lives strictly under `repo.parent` before invoking `git worktree add`. No behavior change for normal inputs; adversarial inputs are normalized or rejected.

### Fixed
- `livery init` is now safe to run in a populated directory. Previously it silently overwrote `CLAUDE.md`, `AGENTS.md`, and any user-written skill/command files at Livery target paths. Now:
  - **`CLAUDE.md` / `AGENTS.md`**: if the file exists, Livery writes the fresh template at the top and appends the user's previous content below (under a "Carried over from previous CLAUDE.md" heading). Any pre-existing `LIVERY-MANAGED` block in the old content is stripped to avoid duplication.
  - **Skill / command files** (`.claude/skills/<name>/SKILL.md`, `.claude/commands/<name>.md`, `.agents/skills/<name>/SKILL.md`): Livery distinguishes its own files (frontmatter has `livery: managed`) from user-written ones. User-written files trigger an interactive prompt: rename to a new functional name (default), skip without installing Livery's, or overwrite. Renames are real — a skill's parent directory is renamed AND its frontmatter `name` field is updated, so the user's skill stays usable as the new name. Commands rename the file directly.
  - In non-interactive mode, the default action for collisions is "skip" with a stderr warning.
- `init_workspace` now returns an `InitResult` dataclass with `created` / `appended` / `skipped` / `backed_up` lists rather than a flat list of paths. `livery init` CLI output reflects all four categories so the user sees exactly what happened to each file.

### Marker convention
- Livery-shipped skill and slash-command files now include `livery: managed` in their frontmatter. This is the marker `init` (and future `upgrade-workspace` extensions) use to distinguish framework-managed files from user-written ones — same pattern as the `LIVERY-MANAGED:BEGIN`/`END` markers in convention files. Existing scaffolded files in older workspaces don't have this marker; on the next `livery init` they'd look user-written. `upgrade-workspace` retroactively refreshes them via the existing `--force` path.

## 0.8.5 — 2026-05-02

### Fixed
- **Critical: `livery sync-cos` no longer clobbers a long-edited convention file with a freshly-scaffolded template one.** v0.8.2's "most recently modified" source picker was wrong — a bare-template `AGENTS.md` created by `upgrade-workspace` (on v0.8.0, before the v0.8.1 mirror fix) had a newer mtime than a long-edited `CLAUDE.md`, and `sync-cos` would treat the template file as canonical and overwrite the rich one. **The fix:** source picker now ranks files by user content size (everything outside the LIVERY-MANAGED block, ignoring the bare template scaffold). Bare-template files score zero and can never win against a file with real edits. mtime is only the tiebreaker when content size is equal. `--from FILENAME` still works as an explicit override but is no longer needed for the common case.

### Recovery
- If you ran `sync-cos` on v0.8.2–v0.8.4 and got a template-overwritten convention file, recover from git: `git checkout HEAD -- CLAUDE.md` (uncommitted) or `git checkout <sha> -- CLAUDE.md` (committed). Then re-run `sync-cos --apply` on this version to propagate the rich content correctly.

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
