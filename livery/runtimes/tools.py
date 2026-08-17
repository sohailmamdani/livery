"""Tools exposed to local-LLM agents. Stdlib only.

Each tool has:
- a name (matches the OpenAI tool-call convention)
- a JSON schema (OpenAI-compatible) used when we tell the model which tools it has
- a Python callable that executes it and returns a string

The agent loop in lm_studio.py consults TOOLS to resolve each tool_call
emitted by the model.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "livery-junior-research/0.1 (+https://github.com/sohailmamdani/livery)"
HTTP_TIMEOUT = 30.0


@dataclass(slots=True)
class Tool:
    name: str
    schema: dict
    run: Callable[..., str]


# ---------------------------------------------------------------------------
# HTML → readable text (lightweight, no beautifulsoup)
# ---------------------------------------------------------------------------

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    text = _SCRIPT_STYLE.sub(" ", html)
    text = _TAG.sub(" ", text)
    # Decode common entities; fallback leaves them untouched.
    for src, dst in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(src, dst)
    return _WHITESPACE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# web_fetch — GET a URL and return readable text
# ---------------------------------------------------------------------------


def web_fetch(url: str, max_chars: int = 20000) -> str:
    """Fetch a URL and return stripped text (up to max_chars)."""
    if not url.startswith(("http://", "https://")):
        return f"error: url must start with http:// or https:// (got: {url})"

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return f"error: HTTP {e.code} fetching {url}"
    except urllib.error.URLError as e:
        return f"error: could not reach {url} ({e.reason})"
    except Exception as e:  # defensive
        return f"error: {type(e).__name__}: {e}"

    try:
        body = raw.decode("utf-8", errors="replace")
    except Exception:
        body = raw.decode("latin-1", errors="replace")

    if "text/html" in content_type.lower() or "<html" in body[:500].lower():
        text = html_to_text(body)
    else:
        text = body

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[truncated at {max_chars} chars of {len(text)}]"
    return text


# ---------------------------------------------------------------------------
# web_search — DuckDuckGo HTML endpoint, no API key needed
# ---------------------------------------------------------------------------


_DDG_RESULT = re.compile(
    r'<a\s+[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.+?)</a>'
    r'.*?<a\s+[^>]*class="result__snippet"[^>]*>(?P<snippet>.+?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _clean_snippet(s: str) -> str:
    return html_to_text(s)


def web_search(query: str, max_results: int = 5) -> str:
    """DuckDuckGo HTML search. Returns a JSON string of [{title, url, snippet}, ...]."""
    q = urllib.parse.urlencode({"q": query})
    url = f"https://duckduckgo.com/html/?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})

    results = []
    for m in _DDG_RESULT.finditer(html):
        raw_href = m.group("href")
        # DDG wraps real URLs in a redirect: /l/?uddg=<encoded>
        parsed = urllib.parse.urlparse(raw_href)
        if parsed.path == "/l/":
            qs = urllib.parse.parse_qs(parsed.query)
            actual = qs.get("uddg", [""])[0]
            href = urllib.parse.unquote(actual) if actual else raw_href
        else:
            href = raw_href
        results.append({
            "title": _clean_snippet(m.group("title")),
            "url": href,
            "snippet": _clean_snippet(m.group("snippet")),
        })
        if len(results) >= max_results:
            break

    if not results:
        return json.dumps({"warning": "no results parsed", "query": query})
    return json.dumps(results)


# ---------------------------------------------------------------------------
# ticket_update — append to the ticket bound to this dispatch
# ---------------------------------------------------------------------------


def ticket_update(message: str, kind: str = "progress") -> str:
    """Append a concise update to the current dispatch ticket.

    The dispatch command binds workspace, ticket, and actor through private
    environment context. Models can choose only the update text and kind, so
    this tool cannot be redirected to an unrelated ticket or workspace.
    """
    workspace_raw = os.environ.get("LIVERY_WORKSPACE_ROOT")
    ticket_id = os.environ.get("LIVERY_TICKET_ID")
    actor = os.environ.get("LIVERY_ASSIGNEE")
    if not workspace_raw or not ticket_id or not actor:
        return "error: ticket_update is only available inside a ticket dispatch"

    from livery.ticket_updates import append_ticket_update, find_ticket_path

    workspace_root = Path(workspace_raw).expanduser().resolve()
    try:
        ticket_path = find_ticket_path(workspace_root, ticket_id)
        update = append_ticket_update(
            workspace_root=workspace_root,
            ticket_path=ticket_path,
            actor=actor,
            kind=kind,
            message=message,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except (OSError, ValueError) as exc:
        return f"error: could not update ticket ({exc})"
    return json.dumps({
        "ticket_id": update.ticket_id,
        "kind": update.kind,
        "actor": update.actor,
        "path": str(update.path),
        "appended": update.appended,
    })


# ---------------------------------------------------------------------------
# Registry + OpenAI-style schemas
# ---------------------------------------------------------------------------


TOOLS: dict[str, Tool] = {
    "ticket_update": Tool(
        name="ticket_update",
        schema={
            "type": "function",
            "function": {
                "name": "ticket_update",
                "description": "Append a concise milestone, decision, blocker, or result to the current Livery ticket. Use at meaningful progress points; never paste raw logs or narrate every tool call.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "A concise, human-readable update focused on outcome, evidence, decision, blocker, or next step.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["progress", "decision", "blocker", "result"],
                            "default": "progress",
                        },
                    },
                    "required": ["message"],
                },
            },
        },
        run=ticket_update,
    ),
    "web_fetch": Tool(
        name="web_fetch",
        schema={
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": "Fetch a web URL and return its stripped text content (up to ~20k chars). Use this to read About pages, press releases, interview transcripts, founder profiles.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full http(s) URL to fetch"},
                        "max_chars": {"type": "integer", "description": "Optional truncation limit", "default": 20000},
                    },
                    "required": ["url"],
                },
            },
        },
        run=web_fetch,
    ),
    "web_search": Tool(
        name="web_search",
        schema={
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web via DuckDuckGo and return a JSON list of up to 5 results (title, url, snippet). Use this to discover URLs you don't already know — press coverage, Reddit threads, interview pages.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query text"},
                        "max_results": {"type": "integer", "description": "Optional cap on results (<=5)", "default": 5},
                    },
                    "required": ["query"],
                },
            },
        },
        run=web_search,
    ),
}


def tool_schemas() -> list[dict]:
    """Return the OpenAI-compatible tool list for the chat-completions `tools` param."""
    return [t.schema for t in TOOLS.values()]


def execute_tool_call(name: str, arguments: dict) -> str:
    """Execute a tool by name with its argument dict. Returns a string result (possibly JSON)."""
    tool = TOOLS.get(name)
    if tool is None:
        return f"error: unknown tool {name!r}. available: {sorted(TOOLS)}"
    try:
        return tool.run(**arguments)
    except TypeError as e:
        return f"error: bad arguments to {name}: {e}"
    except Exception as e:  # defensive
        return f"error: {name} raised {type(e).__name__}: {e}"
