# Livery — framework repo

This is the Livery framework source. End users install it with `uv tool install --from git+https://github.com/sohailmamdani/livery.git livery` and operate on their own *workspaces* (separate directories with a `livery.toml` marker). This repo is **not** a workspace — there are no agents or tickets here.

For user-facing documentation, see `README.md` and `docs/`. For framework-internal conventions — what to keep in mind when contributing — keep reading. This file (and its mirror `AGENTS.md`) auto-loads when you open Claude Code or Codex in this repo to work on Livery itself.

## What lives where

- `livery/` — Python package. Entry point `livery.cli:app` (Typer).
- `livery/cos_engines.py` — registry of supported CoS engines (Claude Code, Codex, Pi, OpenCode). Adding a new engine = appending an entry here.
- `livery/runtimes/` — runtime adapters. One module per runtime (codex, claude_code, cursor, lm_studio + ollama). Shared tool definitions in `runtimes/tools.py`.
- `livery/config.py` — reads `livery.toml` from the user's workspace.
- `livery/paths.py` — `find_root()` walks up from cwd to find the workspace marker.
- `livery/init.py` / `livery/upgrade.py` — workspace scaffolding and the markered-block upgrade flow.
- `livery/dispatch.py` — composes prompts, builds runtime shell commands, manages worktrees.
- `livery/status.py` / `livery/doctor.py` / `livery/onboard.py` / `livery/hire.py` — supporting CLI features.
- `livery/telegram.py` — Bot API helper.
- `bin/livery` — dev wrapper; invokes `.venv/bin/python -m livery` preserving cwd. Not distributed; users get a proper `livery` binary on PATH from `uv tool install`.
- `tests/` — pytest suite. Full suite must pass before any commit that touches code.

## Framework-wide conventions

- **Commit each logical change.** Small commits with clear messages. Imperative subject lines under 70 characters.
- **Tests pass before commit.** `uv run pytest` must be green. Tests block real network calls (see `tests/conftest.py`); any new HTTP feature needs a test that mocks `urllib.request.urlopen`.
- **No workspace files in this repo.** No `agents/`, no `tickets/`, no `livery.toml` at the repo root. Those live in user workspaces.
- **Runtime adapters are self-contained.** A new runtime = one branch in `dispatch.build_runtime_command` + a doctor entry + tests. Don't reach outside the runtime package for shared helpers.
- **CoS engine adapters are self-contained.** A new engine = one entry in `cos_engines.COS_ENGINES` + tests. Same shape as runtime adapters.
- **Stdlib-only for network calls in core code.** `urllib` where possible. Avoid `requests` / `httpx` as runtime deps. (User code in their workspace can depend on anything.)
- **Backwards compat for existing workspaces.** `livery.toml` schema and `agent.md` frontmatter changes are additive only during 0.x. `paths.find_root()` still accepts the legacy `pyproject.toml + livery/` marker; don't remove without a migration plan. See `CONTRIBUTING.md` for details.

## Release discipline

- Tag releases with semver-ish tags (`v0.6.0`, `v0.6.1`, ...).
- Patch bump (`0.6.x → 0.6.x+1`): bug fixes, doc-only changes, no behavior change.
- Minor bump (`0.6.x → 0.7.0`): new commands, new flags, additive schema. Anything that changes the user-facing surface.
- Major bump (post-1.0): breaking changes only.
- Every release gets an entry in `CHANGELOG.md` under a dated heading.

## Conventions for the AI session that's working on Livery

(Applies whether you're Claude Code reading this file or Codex reading `AGENTS.md`. Same content, two filenames — see `cos_engines.py` for why.)

- **Push back hard when the user may be wrong.** ≥70% confidence to disagree → say so plainly. Show your reasoning. If they still want it after hearing the pushback, do it.
- **Plain language.** Many users are tech-savvy but not programmers. Skip jargon; explain code changes in terms of behavior, not internals.
- **Don't leak workspace state into the framework.** If you find yourself wanting to hardcode a chat id, an absolute path, a username, or a project-specific convention, stop and move it to config or a workspace-level concept.
- **Match the existing testing discipline.** New CLI behavior gets a test in `tests/test_<module>.py`. Don't ship features without coverage of the happy path and at least one edge case.
- **Read `CONTRIBUTING.md` for what's in scope vs out of scope** before proposing additions. Livery is intentionally minimal — features that pull it toward "web UI / multi-user / cloud" are non-starters.
