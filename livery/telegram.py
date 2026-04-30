"""Telegram notifications via Bot API. Uses stdlib urllib; no dep."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_TOKEN_PATH = Path.home() / ".claude/channels/telegram/.env"


def _resolve_chat_id(chat_id: str | None) -> str:
    """Pick the chat id: explicit arg > workspace config > env var."""
    if chat_id:
        return chat_id
    try:
        from .config import load as _load_config

        cfg = _load_config()
        if cfg.telegram.chat_id:
            return cfg.telegram.chat_id
    except Exception:
        pass
    env_chat = os.environ.get("LIVERY_TELEGRAM_CHAT_ID")
    if env_chat:
        return env_chat
    raise RuntimeError(
        "No Telegram chat_id: set it in livery.toml under [telegram] chat_id=..., "
        "or export LIVERY_TELEGRAM_CHAT_ID."
    )


def _load_token() -> str:
    """Read the Telegram bot token. Precedence: $TELEGRAM_BOT_TOKEN > workspace config > default .env."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token

    path = DEFAULT_TOKEN_PATH
    try:
        from .config import load as _load_config

        cfg = _load_config()
        if cfg.telegram.token_file:
            path = Path(cfg.telegram.token_file).expanduser()
    except Exception:
        pass

    if not path.exists():
        raise RuntimeError(
            f"No Telegram token: set $TELEGRAM_BOT_TOKEN or create {path}"
        )
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"TELEGRAM_BOT_TOKEN not found in {path}")


def send_message(text: str, chat_id: str | None = None) -> dict:
    """POST a message to the workspace's Telegram chat via sendMessage.

    `chat_id` is resolved from (1) explicit arg, (2) livery.toml, (3) env var.
    Returns API response.
    """
    resolved = _resolve_chat_id(chat_id)
    token = _load_token()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": resolved, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def set_my_commands(commands: list[dict[str, str]]) -> dict:
    """Register the bot's slash-command menu via setMyCommands.

    `commands` is a list of {"command": "...", "description": "..."} dicts.
    Telegram shows these in the "/" autocomplete menu for users of the bot.
    This only registers UI hints; actual routing to Livery logic is handled
    by CoS when it receives a matching inbound message.
    """
    token = _load_token()
    url = f"https://api.telegram.org/bot{token}/setMyCommands"
    body = json.dumps({"commands": commands}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


DEFAULT_LIVERY_BOT_COMMANDS: list[dict[str, str]] = [
    {"command": "tickets", "description": "List tickets (optional filter: open, review, done, cancelled)"},
    {"command": "ticket", "description": "Show a ticket by id or slug fragment"},
    {"command": "new", "description": "Create a new ticket (CoS will ask for details)"},
    {"command": "close", "description": "Close a ticket: /close <id> [summary]"},
    {"command": "dispatch", "description": "Dispatch a ticket to its assigned agent: /dispatch <id>"},
    {"command": "status", "description": "Brief status: open tickets by assignee"},
]
