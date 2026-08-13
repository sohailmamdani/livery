from __future__ import annotations

import json
import plistlib
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from livery.attempts import list_attempts
from livery.cli import app
from livery.init import init_workspace
from livery.schedule import (
    DAY_NAMES,
    create_schedule,
    find_schedule,
    list_schedules,
    load_schedule,
    occurrence_for,
    parse_duration,
    run_schedule,
    schedule_lock,
    schedule_state,
)
from livery.schedule_backends import (
    install_schedule,
    load_installation,
    preview_installation,
    render_launchd_plist,
    render_systemd_units,
    set_schedule_enabled,
    uninstall_schedule,
)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    init_workspace(target=workspace, name="workspace")
    agent = workspace / "agents" / "briefing"
    agent.mkdir()
    (agent / "agent.md").write_text(
        "---\nruntime: codex\nmodel: test-model\ncwd: " + str(tmp_path) + "\n---\n"
    )
    (agent / "AGENTS.md").write_text("You prepare concise briefings.\n")
    return workspace


def _daily(workspace: Path, task: str = "Prepare the evening briefing.") -> Path:
    return create_schedule(
        workspace_root=workspace,
        schedule_id="evening-brief",
        assignee="briefing",
        task=task,
        kind="calendar",
        time_value="18:00",
        days=DAY_NAMES[:5],
    )


def test_create_and_load_portable_schedule(tmp_path):
    workspace = _workspace(tmp_path)
    path = _daily(workspace)

    definition = load_schedule(path)

    assert definition.id == "evening-brief"
    assert definition.assignee == "briefing"
    assert definition.kind == "calendar"
    assert definition.days == DAY_NAMES[:5]
    assert definition.time == "18:00"
    assert "evening briefing" in definition.task
    assert [item.id for item in list_schedules(workspace)] == ["evening-brief"]
    assert find_schedule(workspace, "evening").id == "evening-brief"
    with pytest.raises(ValueError, match="not found"):
        find_schedule(workspace, "../../outside")


@pytest.mark.parametrize("value,seconds", [("30m", 1800), ("2h", 7200), ("1d", 86400)])
def test_parse_duration(value, seconds):
    assert parse_duration(value) == seconds


def test_schedule_validation_rejects_bad_id_and_empty_task(tmp_path):
    workspace = _workspace(tmp_path)
    with pytest.raises(ValueError, match="schedule id"):
        create_schedule(
            workspace_root=workspace,
            schedule_id="Bad ID",
            assignee="briefing",
            task="task",
            kind="calendar",
            time_value="18:00",
            days=DAY_NAMES,
        )
    with pytest.raises(ValueError, match="empty task"):
        create_schedule(
            workspace_root=workspace,
            schedule_id="empty",
            assignee="briefing",
            task="",
            kind="calendar",
            time_value="18:00",
            days=DAY_NAMES,
        )


def test_calendar_occurrence_uses_latest_eligible_local_time(tmp_path):
    definition = load_schedule(_daily(_workspace(tmp_path)))
    now = datetime(2026, 8, 13, 19, 30, tzinfo=timezone.utc)  # Thursday

    occurrence, delay = occurrence_for(definition, now=now, manual=False)

    assert occurrence == "2026-08-13T18:00:00+00:00"
    assert delay == 5400


def test_overlap_lock_rejects_live_second_holder(tmp_path):
    workspace = _workspace(tmp_path)
    with (
        schedule_lock(workspace, "evening-brief") as first,
        schedule_lock(workspace, "evening-brief") as second,
    ):
        assert first is True
        assert second is False


def test_missed_skip_is_recorded_without_dispatch(tmp_path):
    workspace = _workspace(tmp_path)
    path = create_schedule(
        workspace_root=workspace,
        schedule_id="skip-late",
        assignee="briefing",
        task="Prepare briefing.",
        kind="calendar",
        time_value="18:00",
        days=DAY_NAMES,
        missed_run="skip",
    )
    result = run_schedule(
        workspace_root=workspace,
        definition=load_schedule(path),
        now=datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc),
    )

    assert result.outcome == "missed-skipped"
    assert schedule_state(workspace, "skip-late")["skipped_count"] == 1
    assert list_attempts(workspace) == []


def test_managed_run_creates_unique_attempt_and_deduplicates_occurrence(
    tmp_path, monkeypatch
):
    workspace = _workspace(tmp_path)
    definition = load_schedule(_daily(workspace))

    def fake_runtime_command(**kwargs):
        output = shlex.quote(str(kwargs["output_path"]))
        body = "=== DISPATCH_SUMMARY ===\\nStatus: done\\nSummary: complete\\n=== END DISPATCH_SUMMARY ===\\n"
        return f"printf {shlex.quote(body)} > {output}"

    monkeypatch.setattr("livery.schedule.build_runtime_command", fake_runtime_command)
    now = datetime(2026, 8, 13, 18, 0, tzinfo=timezone.utc)

    first = run_schedule(workspace_root=workspace, definition=definition, now=now)
    second = run_schedule(workspace_root=workspace, definition=definition, now=now)

    assert first.outcome == "succeeded"
    assert first.attempt_id
    assert Path(first.output_path).is_file()
    assert second.outcome == "already-ran"
    attempts = list_attempts(workspace)
    assert len(attempts) == 1
    assert attempts[0].schedule_id == "evening-brief"
    assert attempts[0].scheduled_for == "2026-08-13T18:00:00+00:00"
    assert attempts[0].trigger == "schedule"
    assert attempts[0].summary_excerpt == ["Status: done", "Summary: complete"]


def test_managed_run_fails_when_runtime_omits_summary(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    definition = load_schedule(_daily(workspace))

    def fake_runtime_command(**kwargs):
        output = shlex.quote(str(kwargs["output_path"]))
        return f"printf 'no summary\\n' > {output}"

    monkeypatch.setattr("livery.schedule.build_runtime_command", fake_runtime_command)
    result = run_schedule(workspace_root=workspace, definition=definition, manual=True)

    assert result.outcome == "failed"
    assert result.exit_code == 1
    attempt = list_attempts(workspace)[0]
    assert attempt.trigger == "manual"
    assert attempt.failure_detail == "runtime exited without DISPATCH_SUMMARY"


def test_managed_run_preserves_agent_reported_blocked_status(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    definition = load_schedule(_daily(workspace))

    def fake_runtime_command(**kwargs):
        output = shlex.quote(str(kwargs["output_path"]))
        body = "=== DISPATCH_SUMMARY ===\\nStatus: blocked\\nSummary: needs auth\\n=== END DISPATCH_SUMMARY ===\\n"
        return f"printf {shlex.quote(body)} > {output}"

    monkeypatch.setattr("livery.schedule.build_runtime_command", fake_runtime_command)
    result = run_schedule(workspace_root=workspace, definition=definition, manual=True)

    assert result.outcome == "blocked"
    assert result.exit_code == 0
    assert list_attempts(workspace)[0].status.value == "blocked"


def test_launchd_render_uses_calendar_interval_and_unique_workspace_identity(tmp_path):
    workspace = _workspace(tmp_path)
    definition = load_schedule(_daily(workspace))
    identity, body, path = render_launchd_plist(
        definition,
        workspace_root=workspace,
        home=tmp_path / "home",
        command=("/opt/livery", "schedule", "run", definition.id),
    )
    parsed = plistlib.loads(body)

    assert identity in path.name
    assert parsed["Label"] == identity
    assert len(parsed["StartCalendarInterval"]) == 5
    assert parsed["ProgramArguments"][0] == "/opt/livery"
    assert parsed["WorkingDirectory"] == str(workspace)
    assert parsed["RunAtLoad"] is True
    assert parsed["EnvironmentVariables"]["PATH"]


def test_systemd_render_uses_persistent_calendar_timer(tmp_path):
    workspace = _workspace(tmp_path)
    definition = load_schedule(_daily(workspace))
    identity, service, timer, service_path, timer_path = render_systemd_units(
        definition,
        workspace_root=workspace,
        home=tmp_path / "home",
        command=("/opt/livery", "schedule", "run", definition.id),
    )

    assert identity in service_path.name
    assert timer_path.name == f"{identity}.timer"
    assert 'ExecStart="/opt/livery" "schedule" "run" "evening-brief"' in service
    assert 'Environment="PATH=' in service
    assert "OnCalendar=Mon,Tue,Wed,Thu,Fri *-*-* 18:00:00" in timer
    assert "Persistent=true" in timer


def test_systemd_interval_has_initial_and_recurring_triggers(tmp_path):
    workspace = _workspace(tmp_path)
    path = create_schedule(
        workspace_root=workspace,
        schedule_id="poll",
        assignee="briefing",
        task="Poll the source.",
        kind="interval",
        interval_seconds=1800,
    )
    definition = load_schedule(path)
    _, _, timer, _, _ = render_systemd_units(
        definition,
        workspace_root=workspace,
        home=tmp_path / "home",
        command=("/opt/livery", "schedule", "run", definition.id),
    )

    assert definition.missed_run == "skip"
    assert "OnActiveSec=1800s" in timer
    assert "OnUnitActiveSec=1800s" in timer
    assert "Persistent=" not in timer


def test_launchd_install_is_user_scoped_and_clears_disabled_override(tmp_path):
    workspace = _workspace(tmp_path)
    definition = load_schedule(_daily(workspace))
    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    installation = install_schedule(
        definition,
        workspace_root=workspace,
        platform_name="darwin",
        home=tmp_path / "home",
        command=("/opt/livery", "schedule", "run", definition.id),
        runner=fake_run,
    )

    assert installation.backend == "launchd"
    assert installation.files[0].is_file()
    assert any(call[:2] == ["launchctl", "enable"] for call in calls)
    assert any(call[:2] == ["launchctl", "bootstrap"] for call in calls)
    assert schedule_state(workspace, definition.id)["installed_at"]


def test_past_one_shot_cannot_be_installed(tmp_path):
    workspace = _workspace(tmp_path)
    path = create_schedule(
        workspace_root=workspace,
        schedule_id="past",
        assignee="briefing",
        task="Run once.",
        kind="once",
        at="2000-01-01T12:00:00Z",
    )
    with pytest.raises(ValueError, match="in the past"):
        preview_installation(
            load_schedule(path),
            workspace_root=workspace,
            platform_name="darwin",
            home=tmp_path / "home",
        )


def test_placeholder_task_cannot_be_installed(tmp_path):
    workspace = _workspace(tmp_path)
    path = create_schedule(
        workspace_root=workspace,
        schedule_id="draft",
        assignee="briefing",
        task="Describe the scheduled task here.",
        kind="calendar",
        time_value="09:00",
        days=DAY_NAMES,
    )
    with pytest.raises(ValueError, match="placeholder task"):
        preview_installation(
            load_schedule(path),
            workspace_root=workspace,
            platform_name="darwin",
            home=tmp_path / "home",
        )


def test_linux_install_enable_disable_and_uninstall_are_scoped(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    definition = load_schedule(_daily(workspace))
    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "livery.schedule_backends.shutil.which", lambda _: "/bin/systemctl"
    )
    installation = install_schedule(
        definition,
        workspace_root=workspace,
        platform_name="linux",
        home=tmp_path / "home",
        command=("/opt/livery", "schedule", "run", definition.id),
        runner=fake_run,
    )
    assert installation.backend == "systemd-user"
    assert all(path.is_file() for path in installation.files)
    assert "schedules/" in (workspace / ".livery" / ".gitignore").read_text()
    assert load_installation(workspace, definition.id) == installation
    assert [
        "systemctl",
        "--user",
        "enable",
        "--now",
        f"{installation.identity}.timer",
    ] in calls

    set_schedule_enabled(installation, enabled=False, runner=fake_run)
    assert calls[-1][2] == "disable"
    uninstall_schedule(installation, workspace_root=workspace, runner=fake_run)
    assert all(not path.exists() for path in installation.files)
    assert load_installation(workspace, definition.id) is None
    assert "installed_at" not in schedule_state(workspace, definition.id)


def test_preview_does_not_write_native_files(tmp_path):
    workspace = _workspace(tmp_path)
    definition = load_schedule(_daily(workspace))
    preview = preview_installation(
        definition,
        workspace_root=workspace,
        platform_name="darwin",
        home=tmp_path / "home",
        command=("/opt/livery", "schedule", "run", definition.id),
    )
    assert preview.backend == "launchd"
    assert all(not path.exists() for path in preview.files)


def test_schedule_cli_new_and_list_json(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "schedule",
            "new",
            "morning-brief",
            "--agent",
            "briefing",
            "--weekdays",
            "08:30",
            "--task",
            "Prepare the morning briefing.",
            "--format",
            "json",
        ],
    )
    assert created.exit_code == 0, created.stdout + created.stderr
    payload = json.loads(created.stdout)
    assert payload["schedule"]["id"] == "morning-brief"
    assert payload["schedule"]["trigger"]["days"] == list(DAY_NAMES[:5])

    listed = runner.invoke(app, ["schedule", "list", "--format", "json"])
    assert listed.exit_code == 0, listed.stdout + listed.stderr
    rows = json.loads(listed.stdout)["schedules"]
    assert [row["id"] for row in rows] == ["morning-brief"]
    assert rows[0]["installation"] is None
