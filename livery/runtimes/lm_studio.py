"""LM Studio / MLX runtime adapter — local-LLM agent loop with native-format tool calls.

We sidestep LM Studio's built-in tool-call parser entirely. Reasons (verified via
`lms log stream` capture against gemma-4-26b-a4b-it-mlx on 2026-04-20):

- LM Studio's "Default" tool-use path injects a pseudo-syntax system prompt with
  `<|"|>` delimiters and `<|tool>` / `<tool|>` markers. Models comply for a few
  rounds, then emit slight format variations LM Studio can't parse back.
- This fails silently: unparseable tool calls fall through as `content`, and our
  agent loop thinks the model produced a final answer when it was actually
  trying to call another tool.
- The "Tool Use" badge in LM Studio marks models it tries to support, not models
  it reliably supports.

Fix: don't pass `tools=[...]` to LM Studio at all. Inject our own instructions
into a system message, telling the model to emit tool calls in a simple format
we parse ourselves.

Convention:
- Tools described in a system message we prepend to the user prompt.
- Tool calls: `<tool_call>{"name": "...", "arguments": {...}}</tool_call>`
  (Gemma and most instruction-tuned models take to this naturally.)
- Tool results: we feed back as plain user messages containing
  `<tool_response name="...">...</tool_response>` blocks.
- Final answer: plain text with no `<tool_call>` blocks.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

from livery.runtimes.tools import execute_tool_call, tool_schemas

DEFAULT_URL = "http://localhost:1234/v1"
DEFAULT_MAX_ITERATIONS = 20

# Lenient: matches <tool_call>{...}</tool_call> with any inner whitespace/newlines.
_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(?P<body>\{.*?\})\s*</tool_call>",
    re.IGNORECASE | re.DOTALL,
)


def build_tool_system_prompt(schemas: list[dict]) -> str:
    """Compact system message that tells the model how to call tools."""
    lines = [
        "You have access to the tools below. Use them to gather information before answering.",
        "",
        "AVAILABLE TOOLS:",
    ]
    for schema in schemas:
        fn = schema.get("function", schema)
        name = fn.get("name", "?")
        desc = (fn.get("description") or "").strip()
        params = fn.get("parameters", {}) or {}
        properties = params.get("properties", {}) or {}
        required = set(params.get("required", []) or [])
        lines.append(f"  • {name} — {desc}")
        for pname, pspec in properties.items():
            ptype = pspec.get("type", "any")
            pdesc = (pspec.get("description") or "").strip()
            marker = " (required)" if pname in required else ""
            lines.append(f"      - {pname} ({ptype}){marker}: {pdesc}")

    lines.extend([
        "",
        "HOW TO CALL A TOOL:",
        "  Emit one or more <tool_call> blocks. Each block contains exactly one JSON object",
        '  with `name` (the tool name) and `arguments` (an object of parameter values).',
        "",
        "  Example:",
        "  <tool_call>",
        '  {"name": "web_fetch", "arguments": {"url": "https://example.com"}}',
        "  </tool_call>",
        "",
        "AFTER A TOOL CALL:",
        "  You'll receive <tool_response name=\"...\"> blocks with the tool's output.",
        "  Read them, decide whether more tool calls are needed, and continue.",
        "",
        "WHEN TO STOP:",
        "  Once you have enough information, reply with your FINAL answer as plain text",
        "  with NO <tool_call> blocks. Do NOT keep calling tools once you have enough.",
    ])
    return "\n".join(lines)


def extract_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse <tool_call> blocks out of model text. Returns list of {name, arguments}.

    Silently drops blocks whose JSON fails to parse (they bubble up as "no tool
    call happened" rather than crashing the loop).
    """
    calls: list[dict[str, Any]] = []
    for match in _TOOL_CALL_RE.finditer(text):
        try:
            parsed = json.loads(match.group("body"))
        except json.JSONDecodeError:
            continue
        name = parsed.get("name")
        args = parsed.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            continue
        calls.append({"name": name, "arguments": args})
    return calls


def _post(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_chat_completions(
    *,
    model: str,
    prompt: str,
    url: str = DEFAULT_URL,
    timeout_seconds: float = 1800.0,
    max_tokens: int | None = None,
) -> str:
    """Single-shot completion. No tools, no loop."""
    endpoint = f"{url.rstrip('/')}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    response = _post(endpoint, payload, timeout_seconds)
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(
            f"Unexpected LM Studio response shape: {json.dumps(response)[:500]}"
        ) from e


def _extract_native_tool_calls(message: dict) -> list[dict]:
    """Parse OpenAI-style structured tool_calls from a response message.

    Returns a list of {"id", "name", "arguments"} dicts. Silently skips any
    entry with malformed JSON in its arguments field.
    """
    out: list[dict] = []
    for call in (message.get("tool_calls") or []):
        fn = call.get("function") or {}
        name = fn.get("name")
        raw_args = fn.get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                continue
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            continue
        if not isinstance(name, str):
            continue
        out.append({"id": call.get("id", ""), "name": name, "arguments": args})
    return out


def run_agent_loop(
    *,
    model: str,
    prompt: str,
    url: str = DEFAULT_URL,
    timeout_seconds: float = 1800.0,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tokens: int | None = None,
    trace: list[dict] | None = None,
    verbose: bool = False,
) -> str:
    """Agent loop that supports BOTH OpenAI-native tool calls AND our custom
    `<tool_call>`-in-content format.

    Order of preference per response:
      1. If `message.tool_calls` is populated (well-trained models like
         gpt-oss, Qwen), use the structured native path — roundtrip via
         role=tool messages with tool_call_id.
      2. Else, parse `<tool_call>{...}</tool_call>` blocks from content (our
         custom format for models like Gemma that don't structure tool calls
         via LM Studio's Default parser).
      3. Else, it's a final answer — return content.

    Also passes the `tools` parameter to the endpoint (OpenAI standard). Plus
    injects our custom-format instructions into a system message as a belt-
    and-suspenders fallback for models that ignore the `tools` param.
    """
    endpoint = f"{url.rstrip('/')}/chat/completions"
    tools = tool_schemas()
    system_prompt = build_tool_system_prompt(tools)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    for iteration in range(max_iterations):
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        response = _post(endpoint, payload, timeout_seconds)
        if trace is not None:
            trace.append({"iteration": iteration, "response": response})

        try:
            message = response["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(
                f"Unexpected response shape (iteration {iteration}): "
                f"{json.dumps(response)[:500]}"
            ) from e

        content = message.get("content") or ""
        native_calls = _extract_native_tool_calls(message)

        if native_calls:
            # OpenAI-standard path (gpt-oss, Qwen, Llama-tool-tuned, etc.).
            if verbose:
                summary = ", ".join(
                    f"{c['name']}({next(iter(c['arguments'].values()), '...')})"
                    for c in native_calls
                )
                print(f"[iter {iteration}] native tool_calls: {summary}", file=sys.stderr)

            # Echo the assistant message verbatim so follow-up context includes it.
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": message.get("tool_calls"),
            })

            for call in native_calls:
                result = execute_tool_call(call["name"], call["arguments"])
                if verbose:
                    preview = (result or "")[:120].replace("\n", " ")
                    print(
                        f"         → {call['name']} returned {len(result)} chars: {preview}",
                        file=sys.stderr,
                    )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": result,
                })
            continue

        # Fallback: custom <tool_call> blocks in content (Gemma-style).
        text_calls = extract_tool_calls(content)
        if text_calls:
            if verbose:
                summary = ", ".join(
                    f"{c['name']}({next(iter(c['arguments'].values()), '...')})"
                    for c in text_calls
                )
                print(f"[iter {iteration}] text tool_calls: {summary}", file=sys.stderr)

            messages.append({"role": "assistant", "content": content})
            response_blocks = []
            for call in text_calls:
                result = execute_tool_call(call["name"], call["arguments"])
                if verbose:
                    preview = (result or "")[:120].replace("\n", " ")
                    print(
                        f"         → {call['name']} returned {len(result)} chars: {preview}",
                        file=sys.stderr,
                    )
                response_blocks.append(
                    f"<tool_response name={json.dumps(call['name'])}>\n{result}\n</tool_response>"
                )
            messages.append({"role": "user", "content": "\n\n".join(response_blocks)})
            continue

        # No tool calls of either flavor → this is the final answer.
        if verbose:
            print(
                f"[iter {iteration}] DONE — final answer ({len(content)} chars)",
                file=sys.stderr,
            )
        return content

    last = (messages[-1].get("content") or "") if messages else ""
    return (
        "[agent-loop] reached max_iterations without a final answer. "
        "Last message content:\n" + (last or "(empty)")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="livery-lm-studio")
    parser.add_argument("--model", required=True, help="Model id as loaded in LM Studio")
    parser.add_argument(
        "--url",
        default=os.environ.get("LIVERY_LM_STUDIO_URL", DEFAULT_URL),
        help=f"Base URL (default: {DEFAULT_URL} or LIVERY_LM_STUDIO_URL env var)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=1800.0,
        help="HTTP timeout per round (default 30 min)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=f"Agent-loop iteration budget (default {DEFAULT_MAX_ITERATIONS})",
    )
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help="Disable agent loop; single prompt → single reply",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-iteration tool-call summary to stderr",
    )
    args = parser.parse_args(argv)

    prompt = sys.stdin.read()
    if not prompt.strip():
        print("livery-lm-studio: no prompt on stdin", file=sys.stderr)
        return 2

    try:
        if args.no_tools:
            reply = call_chat_completions(
                model=args.model,
                prompt=prompt,
                url=args.url,
                timeout_seconds=args.timeout_seconds,
                max_tokens=args.max_tokens,
            )
        else:
            reply = run_agent_loop(
                model=args.model,
                prompt=prompt,
                url=args.url,
                timeout_seconds=args.timeout_seconds,
                max_iterations=args.max_iterations,
                max_tokens=args.max_tokens,
                verbose=args.verbose,
            )
    except urllib.error.URLError as e:
        print(f"livery-lm-studio: HTTP error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"livery-lm-studio: {e}", file=sys.stderr)
        return 1

    sys.stdout.write(reply)
    if not reply.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
