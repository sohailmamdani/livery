"""Native user-level scheduler adapters for macOS and Linux."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from .attempts import now_iso
from .schedule import (
    DAY_NAMES,
    DEFAULT_TASK_PLACEHOLDER,
    ScheduleDefinition,
    ensure_schedule_runtime_dir,
    initialize_installation_state,
    mark_schedule_uninstalled,
    parse_time,
    schedule_runtime_dir,
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class NativeInstallation:
    schedule_id: str
    backend: str
    identity: str
    files: tuple[Path, ...]
    command: tuple[str, ...]
    installed_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schedule_id": self.schedule_id,
            "backend": self.backend,
            "identity": self.identity,
            "files": [str(path) for path in self.files],
            "command": list(self.command),
            "installed_at": self.installed_at,
        }


def installation_record_path(workspace_root: Path, schedule_id: str) -> Path:
    return (
        schedule_runtime_dir(workspace_root) / "installations" / f"{schedule_id}.json"
    )


def workspace_key(workspace_root: Path) -> str:
    return hashlib.sha256(str(workspace_root.resolve()).encode()).hexdigest()[:10]


def scheduler_command(workspace_root: Path, schedule_id: str) -> tuple[str, ...]:
    binary = shutil.which("livery")
    prefix = (binary,) if binary else (sys.executable, "-m", "livery")
    return (
        *prefix,
        "schedule",
        "run",
        "--workspace",
        str(workspace_root.resolve()),
        schedule_id,
    )


def backend_name(platform_name: str | None = None) -> str:
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        return "launchd"
    if platform_name.startswith("linux"):
        return "systemd-user"
    raise RuntimeError(
        f"scheduling is supported on macOS and systemd-based Linux, not {platform_name}"
    )


def _launchd_calendar(
    definition: ScheduleDefinition,
) -> dict[str, int] | list[dict[str, int]]:
    if definition.kind == "once":
        when = datetime.fromisoformat(
            (definition.at or "").replace("Z", "+00:00")
        ).astimezone()
        # launchd has no Year field. The runner's occurrence ledger makes
        # this logically one-shot even though the native trigger is annual.
        return {
            "Month": when.month,
            "Day": when.day,
            "Hour": when.hour,
            "Minute": when.minute,
        }

    hour, minute = parse_time(definition.time or "")
    if set(definition.days) == set(DAY_NAMES):
        return {"Hour": hour, "Minute": minute}
    launchd_weekday = {
        "sun": 0,
        "mon": 1,
        "tue": 2,
        "wed": 3,
        "thu": 4,
        "fri": 5,
        "sat": 6,
    }
    return [
        {"Weekday": launchd_weekday[day], "Hour": hour, "Minute": minute}
        for day in definition.days
    ]


def render_launchd_plist(
    definition: ScheduleDefinition,
    *,
    workspace_root: Path,
    home: Path,
    command: Sequence[str] | None = None,
) -> tuple[str, bytes, Path]:
    identity = f"dev.livery.schedule.{workspace_key(workspace_root)}.{definition.id}"
    arguments = list(command or scheduler_command(workspace_root, definition.id))
    native_log = (
        schedule_runtime_dir(workspace_root) / "native" / f"{definition.id}.log"
    )
    payload: dict[str, object] = {
        "Label": identity,
        "ProgramArguments": arguments,
        "WorkingDirectory": str(workspace_root.resolve()),
        "ProcessType": "Background",
        "StandardOutPath": str(native_log),
        "StandardErrorPath": str(native_log),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }
    if definition.kind == "interval":
        payload["StartInterval"] = int(definition.interval_seconds or 0)
    else:
        payload["StartCalendarInterval"] = _launchd_calendar(definition)
        if definition.missed_run == "once":
            # CalendarInterval catches sleep. RunAtLoad lets Livery's own
            # occurrence ledger catch one event missed while powered off.
            payload["RunAtLoad"] = True
    path = home / "Library" / "LaunchAgents" / f"{identity}.plist"
    return identity, plistlib.dumps(payload, sort_keys=True), path


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _systemd_calendar(definition: ScheduleDefinition) -> str:
    if definition.kind == "once":
        return definition.at or ""
    hour, minute = parse_time(definition.time or "")
    clock = f"{hour:02d}:{minute:02d}:00"
    if set(definition.days) == set(DAY_NAMES):
        return f"*-*-* {clock}"
    weekdays = ",".join(day.title() for day in definition.days)
    return f"{weekdays} *-*-* {clock}"


def render_systemd_units(
    definition: ScheduleDefinition,
    *,
    workspace_root: Path,
    home: Path,
    command: Sequence[str] | None = None,
) -> tuple[str, str, str, Path, Path]:
    identity = f"livery-{workspace_key(workspace_root)}-{definition.id}"
    arguments = tuple(command or scheduler_command(workspace_root, definition.id))
    exec_start = " ".join(_systemd_quote(arg) for arg in arguments)
    service = "\n".join(
        [
            "[Unit]",
            f"Description=Livery schedule {definition.id}",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={_systemd_quote(str(workspace_root.resolve()))}",
            f"ExecStart={exec_start}",
            f"Environment={_systemd_quote('PATH=' + os.environ.get('PATH', '/usr/bin:/bin'))}",
            "TimeoutStartSec=infinity",
            "",
        ]
    )
    timer_lines = [
        "[Unit]",
        f"Description=Livery timer {definition.id}",
        "",
        "[Timer]",
    ]
    if definition.kind == "interval":
        timer_lines.append(f"OnActiveSec={int(definition.interval_seconds or 0)}s")
        timer_lines.append(f"OnUnitActiveSec={int(definition.interval_seconds or 0)}s")
    else:
        timer_lines.append(f"OnCalendar={_systemd_calendar(definition)}")
        timer_lines.append(
            f"Persistent={'true' if definition.missed_run == 'once' else 'false'}"
        )
    timer_lines.extend(
        [
            "AccuracySec=1min",
            f"Unit={identity}.service",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )
    timer = "\n".join(timer_lines)
    unit_dir = home / ".config" / "systemd" / "user"
    return (
        identity,
        service,
        timer,
        unit_dir / f"{identity}.service",
        unit_dir / f"{identity}.timer",
    )


def preview_installation(
    definition: ScheduleDefinition,
    *,
    workspace_root: Path,
    platform_name: str | None = None,
    home: Path | None = None,
    command: Sequence[str] | None = None,
) -> NativeInstallation:
    _validate_installable(definition)
    backend = backend_name(platform_name)
    home = home or Path.home()
    actual_command = tuple(command or scheduler_command(workspace_root, definition.id))
    if backend == "launchd":
        identity, _, path = render_launchd_plist(
            definition, workspace_root=workspace_root, home=home, command=actual_command
        )
        files = (path,)
    else:
        identity, _, _, service_path, timer_path = render_systemd_units(
            definition, workspace_root=workspace_root, home=home, command=actual_command
        )
        files = (service_path, timer_path)
    return NativeInstallation(definition.id, backend, identity, files, actual_command)


def _write_record(workspace_root: Path, installation: NativeInstallation) -> None:
    ensure_schedule_runtime_dir(workspace_root)
    path = installation_record_path(workspace_root, installation.schedule_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(installation.to_dict(), indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def load_installation(
    workspace_root: Path, schedule_id: str
) -> NativeInstallation | None:
    path = installation_record_path(workspace_root, schedule_id)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text())
    return NativeInstallation(
        schedule_id=str(raw["schedule_id"]),
        backend=str(raw["backend"]),
        identity=str(raw["identity"]),
        files=tuple(Path(item) for item in raw.get("files", [])),
        command=tuple(str(item) for item in raw.get("command", [])),
        installed_at=raw.get("installed_at"),
    )


def install_schedule(
    definition: ScheduleDefinition,
    *,
    workspace_root: Path,
    platform_name: str | None = None,
    home: Path | None = None,
    command: Sequence[str] | None = None,
    runner: CommandRunner = subprocess.run,
) -> NativeInstallation:
    _validate_installable(definition)
    backend = backend_name(platform_name)
    home = home or Path.home()
    actual_command = tuple(command or scheduler_command(workspace_root, definition.id))
    if backend == "systemd-user" and shutil.which("systemctl") is None:
        raise RuntimeError("systemctl is required for scheduling on Linux")
    initialize_installation_state(workspace_root, definition)
    if backend == "launchd":
        identity, body, path = render_launchd_plist(
            definition, workspace_root=workspace_root, home=home, command=actual_command
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        (ensure_schedule_runtime_dir(workspace_root) / "native").mkdir(
            parents=True, exist_ok=True
        )
        path.write_bytes(body)
        domain = f"gui/{os.getuid()}"
        target = f"{domain}/{identity}"
        runner(["launchctl", "bootout", target], capture_output=True, text=True)
        runner(
            ["launchctl", "enable", target], check=True, capture_output=True, text=True
        )
        runner(
            ["launchctl", "bootstrap", domain, str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        files = (path,)
    else:
        identity, service, timer, service_path, timer_path = render_systemd_units(
            definition, workspace_root=workspace_root, home=home, command=actual_command
        )
        service_path.parent.mkdir(parents=True, exist_ok=True)
        service_path.write_text(service)
        timer_path.write_text(timer)
        runner(
            ["systemctl", "--user", "daemon-reload"],
            check=True,
            capture_output=True,
            text=True,
        )
        runner(
            ["systemctl", "--user", "enable", "--now", f"{identity}.timer"],
            check=True,
            capture_output=True,
            text=True,
        )
        files = (service_path, timer_path)
    installation = NativeInstallation(
        definition.id,
        backend,
        identity,
        files,
        actual_command,
        now_iso(),
    )
    _write_record(workspace_root, installation)
    return installation


def set_schedule_enabled(
    installation: NativeInstallation,
    *,
    enabled: bool,
    runner: CommandRunner = subprocess.run,
) -> None:
    if installation.backend == "launchd":
        domain = f"gui/{os.getuid()}"
        target = f"{domain}/{installation.identity}"
        if enabled:
            runner(["launchctl", "bootout", target], capture_output=True, text=True)
            runner(
                ["launchctl", "enable", target],
                check=True,
                capture_output=True,
                text=True,
            )
            runner(
                ["launchctl", "bootstrap", domain, str(installation.files[0])],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            runner(
                ["launchctl", "disable", target],
                check=True,
                capture_output=True,
                text=True,
            )
            runner(["launchctl", "bootout", target], capture_output=True, text=True)
    else:
        action = "enable" if enabled else "disable"
        runner(
            ["systemctl", "--user", action, "--now", f"{installation.identity}.timer"],
            check=True,
            capture_output=True,
            text=True,
        )


def native_status(
    installation: NativeInstallation,
    *,
    runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    if installation.backend == "launchd":
        target = f"gui/{os.getuid()}/{installation.identity}"
        result = runner(["launchctl", "print", target], capture_output=True, text=True)
        return {
            "loaded": result.returncode == 0,
            "detail": (result.stdout or result.stderr).strip()[:500],
        }
    result = runner(
        [
            "systemctl",
            "--user",
            "show",
            f"{installation.identity}.timer",
            "--property=LoadState,ActiveState,UnitFileState,NextElapseUSecRealtime",
            "--no-pager",
        ],
        capture_output=True,
        text=True,
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {
        "loaded": result.returncode == 0 and values.get("LoadState") == "loaded",
        **values,
    }


def uninstall_schedule(
    installation: NativeInstallation,
    *,
    workspace_root: Path,
    runner: CommandRunner = subprocess.run,
) -> None:
    if installation.backend == "launchd":
        target = f"gui/{os.getuid()}/{installation.identity}"
        runner(["launchctl", "bootout", target], capture_output=True, text=True)
        runner(["launchctl", "enable", target], capture_output=True, text=True)
    else:
        runner(
            [
                "systemctl",
                "--user",
                "disable",
                "--now",
                f"{installation.identity}.timer",
            ],
            capture_output=True,
            text=True,
        )
    for path in installation.files:
        path.unlink(missing_ok=True)
    if installation.backend == "systemd-user":
        runner(
            ["systemctl", "--user", "daemon-reload"],
            check=True,
            capture_output=True,
            text=True,
        )
    installation_record_path(workspace_root, installation.schedule_id).unlink(
        missing_ok=True
    )
    mark_schedule_uninstalled(workspace_root, installation.schedule_id)


def format_command(command: Sequence[str]) -> str:
    return shlex.join(command)


def _validate_installable(definition: ScheduleDefinition) -> None:
    if definition.task == DEFAULT_TASK_PLACEHOLDER:
        raise ValueError(
            f"schedule {definition.id} still has the placeholder task; edit "
            f"{definition.path} before installing it"
        )
    if definition.kind != "once":
        return
    when = datetime.fromisoformat((definition.at or "").replace("Z", "+00:00"))
    if when <= datetime.now().astimezone(when.tzinfo):
        raise ValueError(
            f"one-shot schedule {definition.id} is in the past; update its --at time "
            "or run it explicitly with `livery schedule run --now`"
        )
