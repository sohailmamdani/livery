"""Workspace config — read from `livery.toml` at the workspace root.

`livery.toml` is the workspace marker. It captures user-specific settings
(Telegram chat id, bot token path, default runtime hints) so they don't have
to live in code. All fields are optional with sensible defaults.

Stdlib only (tomllib ships with Python 3.11+).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .paths import find_root


@dataclass(slots=True)
class TelegramConfig:
    chat_id: str | None = None
    token_file: str | None = None  # defaults to ~/.claude/channels/telegram/.env


@dataclass(slots=True)
class WorkspaceConfig:
    name: str = "unnamed-workspace"
    description: str = ""
    default_runtime: str | None = None
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    raw: dict = field(default_factory=dict)


def load(root: Path | None = None) -> WorkspaceConfig:
    """Load the workspace config. Returns defaults if `livery.toml` is absent."""
    if root is None:
        root = find_root()
    marker = root / "livery.toml"
    if not marker.is_file():
        return WorkspaceConfig()
    raw = tomllib.loads(marker.read_text())

    tg_raw = raw.get("telegram", {}) or {}
    telegram = TelegramConfig(
        chat_id=tg_raw.get("chat_id"),
        token_file=tg_raw.get("token_file"),
    )

    return WorkspaceConfig(
        name=raw.get("name", "unnamed-workspace"),
        description=raw.get("description", ""),
        default_runtime=raw.get("default_runtime"),
        telegram=telegram,
        raw=raw,
    )
