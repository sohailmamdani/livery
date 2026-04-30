# Contributing to Livery

Livery is pre-1.0 and still maintained as a single-author project, but PRs are welcome. This page is what you need to know before sending one.

## Scope

Livery's job is to be a **thin, stdlib-heavy orchestration layer** for running a small team of AI agents on one person's machine. Features that pull it away from that shape will be declined, even if they're individually nice. In particular:

- **No web UI, no server.** Livery is a CLI plus a Telegram bot. If you want visualization, shell out to a tool that does it well.
- **No multi-user / auth.** One operator per workspace. If you need multi-user orchestration, you're past Livery's scope.
- **No cloud anything.** State is in git. Coordination happens through your existing chat tool (Telegram today; others plausible later).
- **Stdlib-only for core network calls.** `urllib` is the bar; `requests` / `httpx` are not. User workspaces can depend on anything, but the framework itself stays lean.

If you're not sure whether something fits, open an issue first.

## Development setup

```sh
git clone https://github.com/sohailmamdani/livery.git
cd livery
uv sync
uv run pytest
```

The full test suite should pass before any commit. Tests are hermetic — `tests/conftest.py` blocks real network calls, so any new feature that makes HTTP requests needs tests that mock `urllib.request.urlopen`.

## Commit style

- **Small, logical commits.** One feature per commit. Don't batch unrelated changes.
- **Imperative subject line**, <70 characters. "Add X" / "Fix Y" / "Refactor Z", not "added X".
- **Body explains the why**, not the what. The diff shows what.
- **Tests pass before commit.** `uv run pytest` green, always. No `--no-verify`.

## Runtime adapters

Adding a new runtime is the most common kind of PR. Shape:

1. Add a branch in `livery/dispatch.py::build_runtime_command` that returns the shell command.
2. Add the runtime to `livery/doctor.py`'s `RUNTIME_BINARIES` (and `RUNTIME_HTTP_ENDPOINTS` if applicable).
3. Add it to `livery/hire.py::SUPPORTED_RUNTIMES` with a suggested default model in `SUGGESTED_MODELS`.
4. Add tests in `tests/test_dispatch.py` and `tests/test_doctor.py`.
5. Update `docs/runtimes.md`.

Adapters must be **self-contained**: no shared helpers reaching outside the runtimes package, no runtime dependencies beyond stdlib.

## Tools for raw-LLM runtimes

If you're adding a tool to `livery/runtimes/tools.py` (only used by `lm_studio` / `ollama` agents):

- Follow the `Tool` dataclass pattern — name, OpenAI-compatible JSON schema, Python callable.
- No new dependencies. `urllib` is the bar.
- Add tests in `tests/test_tools.py` that mock any HTTP.
- Keep the tool set small and purposeful. Livery is not a general-purpose agent framework.

## Backwards compatibility

The framework must not break existing workspaces. In particular:

- `livery.paths.find_root` still accepts the legacy `pyproject.toml + livery/` marker for workspaces predating `livery.toml`. Don't remove this without a real migration plan.
- Changes to `livery.toml` schema should be additive, not breaking, at least through v1.0.
- Agent `agent.md` frontmatter is part of the public API. Adding optional fields is fine; renaming or requiring new ones is a breaking change.

## Filing issues

A good issue for Livery:

- Names one specific thing that's broken or missing.
- Includes the exact command run and the output.
- For UX suggestions, names the user scenario — not just "it would be nice if…".

Not a good issue: "Livery should have a web UI." (It shouldn't. See **Scope** above.)
