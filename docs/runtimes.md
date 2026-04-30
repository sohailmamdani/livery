# Runtimes

A **runtime** is the thing that actually executes an agent's work. Livery doesn't ship a model or a tool sandbox — it composes a prompt (the agent's `AGENTS.md` plus the ticket) and hands it to a runtime to run.

## Supported runtimes

| Id | What it is | Tool use | Status |
|---|---|---|---|
| `codex` | OpenAI Codex CLI (`codex exec`) | Delegated to Codex | Battle-tested |
| `claude_code` (`claude`) | Anthropic Claude Code CLI (`claude -p`) | Delegated to Claude Code | Verified shape |
| `cursor` (`cursor_agent`) | Cursor Agent CLI (`cursor-agent --print`) | Delegated to Cursor | Verified shape |
| `lm_studio` (`mlx`) | LM Studio's local HTTP server at `:1234/v1` | Livery-hosted | Battle-tested |
| `ollama` | Ollama's local HTTP server at `:11434/v1` | Livery-hosted | Battle-tested |

"Battle-tested" means real dispatches have completed work end-to-end. "Verified shape" means the command is correct in form but hasn't been exercised under real tickets yet; first real dispatch may surface flag adjustments.

## How Livery talks to each runtime

### CLI-harness runtimes: `codex`, `claude_code`, `cursor`

Livery writes the composed prompt to a temp file, then builds a shell command of the shape:

```sh
<binary> [flags] < /tmp/livery-dispatch-<ticket>.txt > /tmp/livery-dispatch-<ticket>.out 2>&1
```

The harness reads the prompt on stdin, runs autonomously with its own tool surface, and writes its output (including the required `=== DISPATCH_SUMMARY ===` block) to the output file. When it finishes, you close the ticket with `livery ticket close` and the loop moves on.

Livery passes flags that disable interactive approval gates (`--dangerously-bypass-approvals-and-sandbox` for Codex, `--dangerously-skip-permissions` for Claude Code, `--force` for Cursor). This is intentional: dispatched agents run unattended.

### Raw-LLM runtimes: `lm_studio`, `ollama`

These are OpenAI-compatible HTTP endpoints, not harnesses. There's no binary that knows how to read files or run commands — the model returns text, and someone has to interpret it. For these runtimes, Livery itself runs the agent loop (`livery/runtimes/lm_studio.py`): it calls `/v1/chat/completions`, parses any tool calls the model emits, executes them, feeds results back, and repeats (capped at 20 iterations by default).

Ollama uses the same loop as LM Studio, just with a different base URL.

## Tool use

This is the question most new users ask, so it gets its own section.

### If your agent runs on `codex` / `claude_code` / `cursor`

Livery contributes nothing to the tool layer. Your agent gets exactly the tools its harness ships with — file read/write, bash, web fetch, MCP servers, whatever the vendor has wired up. That's deliberate: the teams at Anthropic and OpenAI have put enormous work into their tool surfaces, and Livery has no business reinventing it.

If you want an agent to have access to, say, a specific MCP server, configure that in the harness's own config (e.g. `~/.claude/mcp.json` for Claude Code) — not in Livery.

### If your agent runs on `lm_studio` / `ollama`

There's no harness, so Livery provides a minimal built-in tool set from `livery/runtimes/tools.py`:

- **`web_fetch(url, max_chars=20000)`** — GET a URL, strip HTML, return text.
- **`web_search(query, max_results=5)`** — DuckDuckGo HTML endpoint, returns a JSON list of results.

Both use stdlib `urllib` with a 30-second timeout. No filesystem tools, no shell, no database access. This is intentional scope: raw-LLM agents in Livery today are for research-style work (read the web, write text back into the ticket thread), not engineering.

Tool calls use a custom wire format (`<tool_call>{"name": "...", "arguments": {...}}</tool_call>`) parsed by Livery directly. See the docstring at the top of `livery/runtimes/lm_studio.py` for why we sidestep LM Studio's built-in tool-call path — in short, it fails silently under extended sessions.

If you want more tools for raw-LLM agents, add them to `livery/runtimes/tools.py` following the existing pattern (a `Tool` dataclass with a name, JSON schema, and Python callable).

## Picking a runtime for an agent

Rules of thumb:

- **Engineering work** (writing code, running tests, touching many files): use `codex` or `claude_code`. The harness tool surface is what makes this work.
- **Research / drafting** on a cost-sensitive model: `claude_code` with `claude-sonnet-4-6` is the default; upgrade to Opus when quality matters.
- **Fully local / offline / zero-API-cost**: `lm_studio` or `ollama`. Pick a model with solid instruction-following (Gemma 3+ and Qwen 2.5+ are the usual suspects). Expect limited tool surface.
- **Cursor users who want the Cursor agent**: `cursor`. Same shape as Claude Code, different vendor.

## Adding a new runtime

Runtime support lives in `livery/dispatch.py::build_runtime_command`. Adding a new runtime is:

1. Pick an id (used as the `runtime:` field in `agent.md`).
2. Add a branch in `build_runtime_command` that returns the shell command to run the runtime, reading the prompt from `prompt_path` and writing output to `output_path`.
3. For raw-LLM runtimes that expose an OpenAI-compatible endpoint, you may be able to reuse `livery.runtimes.lm_studio` with a different base URL (that's how `ollama` works today).
4. Add tests in `tests/test_dispatch.py` covering `build_runtime_command(runtime="yours", ...)`.
5. Update this page and the `livery doctor` checker in `livery/doctor.py`.

Keep adapters **self-contained**: no shared helpers reaching outside the runtimes package, and no new runtime dependencies in the core (`requests`, `httpx`, etc.). Stdlib `urllib` is the bar.
