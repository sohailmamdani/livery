# Scheduling agent work

Livery owns a portable schedule definition and the resulting dispatch history.
The operating system owns waking Livery at the right time:

- macOS: a per-user LaunchAgent in `~/Library/LaunchAgents/`
- Linux: a service and timer in `~/.config/systemd/user/`

There is no Livery daemon, server, crontab edit, or root installation.

## Create and activate a schedule

```sh
livery schedule new morning-brief \
  --agent briefing \
  --weekdays 08:30 \
  --task "Produce the morning briefing and verify every published link."

livery schedule install morning-brief --dry-run
livery schedule install morning-brief
```

The first command writes `schedules/morning-brief.md`. Commit that file when
the schedule should travel with the workspace. Pulling it on another machine
does not activate it; `schedule install` is always explicit and host-local.

Other trigger forms are:

```sh
livery schedule new daily-check --agent watchdog --daily 09:00 --task "..."
livery schedule new weekly-review --agent reviewer --weekly fri --time 16:00 --task "..."
livery schedule new poll --agent watcher --every 30m --task "..."
livery schedule new one-off --agent researcher --at 2026-08-20T14:00:00-07:00 --task "..."
```

Times for recurring calendar schedules use the host's local timezone. One-shot
timestamps require an RFC 3339 timezone offset or `Z`.

## Definition format

```yaml
---
id: morning-brief
assignee: briefing
trigger:
  kind: calendar
  days: [mon, tue, wed, thu, fri]
  time: "08:30"
overlap: skip
missed_run: once
worktree: false
---

Produce the morning briefing and verify every published link.
```

`overlap` is `skip` or `allow`. The safe default is `skip`, implemented with
an atomic per-schedule lock. Stale locks left by dead processes are reclaimed.
`allow` also permits overlapping manual invocations; native launchd/systemd
may still coalesce a timer event while its own job unit is active.

`missed_run` is `once` or `skip`. Calendar timers configured as `once` catch
up at most one occurrence after sleep or downtime. Livery's occurrence ledger
prevents duplicate work even if the native scheduler fires twice. Fixed
intervals never replay every missed tick; they resume from the native user
scheduler's next interval.

`worktree: true` creates a unique worktree for every run. It is off by default
because recurring worktrees accumulate until the operator reviews and removes
them.

## Execution and evidence

The native job runs the installed Livery executable with an absolute workspace
path:

```sh
livery schedule run --workspace /absolute/workspace morning-brief
```

Installation snapshots the current `PATH` into the native job so the scheduled
Livery process can find Codex, Claude, `uv`, and other runtime binaries without
depending on interactive shell startup files. Rerun `schedule install` or
`schedule sync` after moving those tools. Livery deliberately does not copy
arbitrary environment variables or secrets into native job files; use the
runtime's normal user config, credential store, or keychain for authentication.

Each invocation reloads the current schedule and agent files, creates a unique
dispatch attempt, runs normal dispatch hooks, waits for the runtime, and
records the exit status. Runtime files live under:

```text
.livery/schedules/runs/<schedule-id>/<attempt-id>/
```

Use these commands to inspect it:

```sh
livery schedule list --format json
livery schedule status morning-brief --format json
livery schedule logs morning-brief
livery dispatch status --format json
```

The tracked definition is the desired task. `.livery/schedules/` is ignored
host/runtime state: installation receipts, locks, occurrence state, and logs.

## Native behavior

On macOS, calendar schedules use `StartCalendarInterval`; launchd runs a
calendar event missed during sleep after wake. One-shot jobs use the full
month/day/time fields available to launchd, while Livery's occurrence ledger
makes the logically one-shot definition idempotent across later years.

On Linux, calendar schedules use `OnCalendar=` and `Persistent=true` for
`missed_run: once`. Fixed intervals use `OnActiveSec=` plus
`OnUnitActiveSec=`. User-level systemd
must be available. On a server where schedules must run while the user is
logged out, the administrator may need to enable linger for that user; Livery
does not request elevated privileges or change linger itself.

## Managing installations

```sh
livery schedule disable morning-brief   # stop triggers, retain native files
livery schedule enable morning-brief
livery schedule sync --dry-run           # preview all tracked schedules
livery schedule sync                     # install/update all tracked schedules
livery schedule uninstall morning-brief # remove native files, retain markdown
```

`sync` does not delete native jobs whose definition disappeared. This avoids
surprising destructive reconciliation; uninstall those explicitly.
