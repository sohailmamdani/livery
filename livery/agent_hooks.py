"""Install Livery-aware startup hooks for supported CoS engines."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .paths import WorkspaceResolution, resolve_workspace

MANAGED_BEGIN = "# LIVERY-MANAGED:BEGIN agent-hooks"
MANAGED_END = "# LIVERY-MANAGED:END agent-hooks"
SESSION_BRIEF_COMMAND = "livery session-brief --format text"
SESSION_MATCHER = "startup|resume|clear"
SUPPORTED_AGENT_HOOK_ENGINES = ("codex", "claude_code")


@dataclass(slots=True)
class AgentHookResult:
    engine: str
    path: Path
    status: str
    detail: str = ""


def _hook_target_dir(resolution: WorkspaceResolution) -> Path:
    if resolution.kind == "workspace":
        return resolution.workspace_root
    if resolution.kind == "linked-repo" and resolution.linked_repo_root is not None:
        return resolution.linked_repo_root
    raise RuntimeError(
        "Agent startup hooks can only be installed in a Livery workspace or linked repo."
    )


def hook_target_dir(start: Path | None = None) -> Path:
    return _hook_target_dir(resolve_workspace(start))


def _parse_engines(engines: str) -> list[str]:
    raw = [part.strip() for part in engines.split(",") if part.strip()]
    selected = raw or list(SUPPORTED_AGENT_HOOK_ENGINES)
    unknown = [engine for engine in selected if engine not in SUPPORTED_AGENT_HOOK_ENGINES]
    if unknown:
        expected = ", ".join(SUPPORTED_AGENT_HOOK_ENGINES)
        raise RuntimeError(
            f"Unknown agent hook engine(s): {', '.join(unknown)}. "
            f"Expected: {expected}."
        )
    return selected


def _load_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON at {path}: {e}") from e
    if not isinstance(raw, dict):
        raise RuntimeError(f"Expected JSON object at {path}.")
    return raw


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _is_livery_session_group(group: object) -> bool:
    if not isinstance(group, dict):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if isinstance(hook, dict) and hook.get("command") == SESSION_BRIEF_COMMAND:
            return True
    return False


def _session_start_group(*, include_status_message: bool) -> dict:
    hook = {
        "type": "command",
        "command": SESSION_BRIEF_COMMAND,
        "timeout": 10,
    }
    if include_status_message:
        hook["statusMessage"] = "Loading Livery session brief"
    return {
        "matcher": SESSION_MATCHER,
        "hooks": [hook],
    }


def _install_hook_json(
    path: Path,
    *,
    include_status_message: bool,
    force: bool,
) -> AgentHookResult:
    data = _load_json_object(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        if not force:
            return AgentHookResult(
                "unknown",
                path,
                "skipped",
                "`hooks` is not an object; pass --force to replace.",
            )
        hooks = {}
        data["hooks"] = hooks

    existing = hooks.get("SessionStart", [])
    if not isinstance(existing, list):
        if not force:
            return AgentHookResult(
                "unknown",
                path,
                "skipped",
                "`SessionStart` is not a list; pass --force to replace.",
            )
        existing = []

    cleaned = [group for group in existing if not _is_livery_session_group(group)]
    cleaned.append(_session_start_group(include_status_message=include_status_message))
    hooks["SessionStart"] = cleaned
    _write_json(path, data)
    return AgentHookResult("unknown", path, "installed", "SessionStart runs `livery session-brief`.")


def _uninstall_hook_json(path: Path) -> AgentHookResult:
    if not path.exists():
        return AgentHookResult("unknown", path, "skipped", "File does not exist.")
    data = _load_json_object(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return AgentHookResult("unknown", path, "skipped", "`hooks` is not an object.")
    existing = hooks.get("SessionStart")
    if not isinstance(existing, list):
        return AgentHookResult("unknown", path, "skipped", "No SessionStart hook list found.")
    cleaned = [group for group in existing if not _is_livery_session_group(group)]
    if len(cleaned) == len(existing):
        return AgentHookResult(
            "unknown",
            path,
            "skipped",
            "No Livery-managed SessionStart hook found.",
        )
    if cleaned:
        hooks["SessionStart"] = cleaned
    else:
        hooks.pop("SessionStart", None)
    if not hooks:
        data.pop("hooks", None)
    _write_json(path, data)
    return AgentHookResult("unknown", path, "removed", "Removed Livery-managed SessionStart hook.")


def _has_codex_hooks_setting(text: str) -> bool:
    return bool(re.search(r"(?m)^\s*codex_hooks\s*=", text))


def _codex_hooks_enabled(text: str) -> bool:
    return bool(re.search(r"(?m)^\s*codex_hooks\s*=\s*true\s*(?:#.*)?$", text))


def _ensure_codex_hooks_feature(path: Path, *, force: bool) -> AgentHookResult:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[features]\n"
            f"{MANAGED_BEGIN}\n"
            "codex_hooks = true\n"
            f"{MANAGED_END}\n"
        )
        return AgentHookResult("codex", path, "installed", "Enabled Codex hooks.")

    text = path.read_text()
    if _codex_hooks_enabled(text):
        return AgentHookResult("codex", path, "unchanged", "Codex hooks already enabled.")
    if _has_codex_hooks_setting(text) and not force:
        return AgentHookResult(
            "codex",
            path,
            "skipped",
            "`codex_hooks` is already set; pass --force to replace it.",
        )
    if _has_codex_hooks_setting(text) and force:
        new_text = re.sub(
            r"(?m)^(\s*)codex_hooks\s*=\s*(?:true|false)\s*(?:#.*)?$",
            r"\1codex_hooks = true",
            text,
            count=1,
        )
        path.write_text(new_text)
        return AgentHookResult("codex", path, "updated", "Set `codex_hooks = true`.")

    block = f"{MANAGED_BEGIN}\ncodex_hooks = true\n{MANAGED_END}\n"
    features_match = re.search(r"(?m)^\[features\]\s*$", text)
    if features_match:
        line_end = text.find("\n", features_match.end())
        insert_at = len(text) if line_end == -1 else line_end + 1
        prefix = "\n" if insert_at == len(text) and not text.endswith("\n") else ""
        path.write_text(text[:insert_at] + prefix + block + text[insert_at:])
    else:
        suffix = "" if text.endswith("\n") else "\n"
        path.write_text(text + suffix + "\n[features]\n" + block)
    return AgentHookResult("codex", path, "updated", "Enabled Codex hooks.")


def _remove_managed_codex_feature(path: Path) -> AgentHookResult:
    if not path.exists():
        return AgentHookResult("codex", path, "skipped", "File does not exist.")
    text = path.read_text()
    pattern = re.compile(
        rf"(?ms)^{re.escape(MANAGED_BEGIN)}\ncodex_hooks = true\n{re.escape(MANAGED_END)}\n?"
    )
    new_text, count = pattern.subn("", text)
    if count == 0:
        return AgentHookResult(
            "codex",
            path,
            "skipped",
            "No Livery-managed codex_hooks block found.",
        )
    path.write_text(new_text)
    return AgentHookResult("codex", path, "removed", "Removed Livery-managed codex_hooks block.")


def _set_engine(result: AgentHookResult, engine: str) -> AgentHookResult:
    result.engine = engine
    return result


def install_agent_hooks(
    *,
    start: Path | None = None,
    engines: str = "codex,claude_code",
    force: bool = False,
) -> list[AgentHookResult]:
    target = hook_target_dir(start)
    selected = _parse_engines(engines)
    results: list[AgentHookResult] = []
    if "codex" in selected:
        config_result = _ensure_codex_hooks_feature(
            target / ".codex" / "config.toml",
            force=force,
        )
        results.append(config_result)
        hooks_result = _install_hook_json(
            target / ".codex" / "hooks.json",
            include_status_message=True,
            force=force,
        )
        results.append(_set_engine(hooks_result, "codex"))
    if "claude_code" in selected:
        result = _install_hook_json(
            target / ".claude" / "settings.local.json",
            include_status_message=False,
            force=force,
        )
        results.append(_set_engine(result, "claude_code"))
    return results


def uninstall_agent_hooks(
    *,
    start: Path | None = None,
    engines: str = "codex,claude_code",
) -> list[AgentHookResult]:
    target = hook_target_dir(start)
    selected = _parse_engines(engines)
    results: list[AgentHookResult] = []
    if "codex" in selected:
        results.append(_remove_managed_codex_feature(target / ".codex" / "config.toml"))
        result = _uninstall_hook_json(target / ".codex" / "hooks.json")
        results.append(_set_engine(result, "codex"))
    if "claude_code" in selected:
        result = _uninstall_hook_json(target / ".claude" / "settings.local.json")
        results.append(_set_engine(result, "claude_code"))
    return results
