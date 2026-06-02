# Spec 0002 — Quiet hours

* Status: **Implemented in 1.1.0** &nbsp;·&nbsp; bypass channel added post-spec
* Date: 2026-05-26

## Why

The notification chime + `say` voice + banner are useful during a
working day and hostile at 02:00. macOS Focus / DnD swallows the
banner but Sonoma+ still passes `say` through; the chime ignores
DnD entirely. We need plugin-side suppression.

## What the user sees

Tools submenu gains a Notifications block:

```
─────────────────────────────
Notifications
  ✔ Quiet hours: 23:00 — 09:00 (active, 6h 12m left)
  Pause for 1 hour
  Pause until tomorrow morning
  Resume now
─────────────────────────────
```

- Status line is always present. Reads either *Quiet hours: off*,
  *Quiet hours: 23:00 — 09:00 (off, 6h until start)*, or
  *Quiet hours: paused for 23m more*.
- `Pause for 1 hour` and `Pause until tomorrow morning` appear
  only when notifications are currently on.
- `Resume now` appears only when paused.

## How it works

Two complementary mechanisms:

1. **Scheduled** via `quiet_hours` config — a string like
   `"23:00-09:00"` (or `null` to disable). Window wraps midnight
   when start > end.
2. **Ad-hoc** via sidecar `~/.claude/agent-state.quiet-until` —
   ISO-8601 local timestamp. `Pause *` actions write it, `Resume
   now` unlinks it.

Both `hooks/notify-stop.sh` and `hooks/notify-wait.sh` read the
sidecar first, then the scheduled window. If either says "quiet
now", the hook does nothing past its argument parsing — exits 0.

When quiet:

- Sound: suppressed if `"sound"` is in `quiet_hours_silences`.
- `say`: suppressed if `"voice"` is in `quiet_hours_silences`.
- `terminal-notifier` banner: suppressed if `"banner"` is in
  `quiet_hours_silences`.

Default `quiet_hours_silences` is `["sound", "voice"]` — quiet mutes
audio but the banner still appears, so a quiet window never makes the
user miss an event outright. Add `"banner"` for full silence; list
only `"voice"` to keep the chime.

## Config

```jsonc
{
  "quiet_hours": "23:00-09:00",        // null disables scheduled
  "quiet_hours_silences": ["sound", "voice"]
}
```

Defaults: `quiet_hours: "23:00-08:00"`, `quiet_hours_silences:
["sound", "voice"]` — a hands-off night window so the menu doesn't
ding/speak while the user is asleep, while the banner still appears so
nothing is missed. Set `quiet_hours` to `null` to disable the schedule
entirely.

## Menu actions

New entries under `bin/app/`:

- `quiet-pause.sh <duration>` — writes
  `agent-state.quiet-until = now + duration`. `duration` is
  `1h` / `tomorrow` / explicit ISO.
- `quiet-resume.sh` — unlinks the sidecar.
- `quiet-bypass.sh` — writes `agent-state.quiet-bypass-until = end
  of the current scheduled window` (see *Bypass channel* below).
- `quiet-bypass-cancel.sh` — unlinks the bypass sidecar.

`Pause until tomorrow morning` resolves to the *end* of the
configured `quiet_hours` window if defined, otherwise 09:00 local.

## Bypass channel

Inverse of *pause*: a temporary opt-in to fire notifications *during*
the scheduled quiet window. Useful when the user is up late on
deadline and wants the menu to behave normally for the rest of *this*
night without permanently editing `quiet_hours`.

Surface (added below the pause/resume entries):

- `Bypass until window ends` — visible only while the schedule is
  currently active and no bypass is held.
- `Cancel bypass` — visible only while a bypass is held.

Status line: `Quiet hours: bypassed for {duration} more`.

Implementation mirrors the pause sidecar, with two differences:

1. **Sidecar path**: `~/.claude/agent-state.quiet-bypass-until`
   (separate file so pause and bypass are independent).
2. **Deadline**: always pinned to the *end* of the current window
   when written. Once the window closes the bypass auto-expires
   (the sidecar timestamp is in the past, the reader treats it as
   absent), so the user doesn't have to remember to cancel.

**Precedence when both pause and bypass are held simultaneously:**
pause wins. "Do not bother me" is treated as more recent or more
explicit than "do bother me even during quiet"; the status line
keeps showing both *remaining* numbers so the user can resolve the
contradiction by cancelling whichever is wrong.

Both hooks (`notify-stop.sh`, `notify-wait.sh`) consult the bypass
sidecar through the shared `_compute_quiet_state` in
`hooks/_notify-common.sh` and unset `QUIET_NOW` when bypass is in
effect and no pause overrides it.

## Edge cases

- Window wraps midnight — handled by treating start > end as
  *active when now ≥ start OR now < end*.
- Sidecar timestamp in the past — treated as not paused. The hook
  doesn't bother unlinking it; the next `Resume now` or
  `Pause *` cleans it up.
- DST — calculations done in local wall-clock time via
  `datetime.datetime.now()`. We don't try to be clever; a 23:00
  start on a spring-forward day means 23:00 local, full stop.
- Multiple plugins / tools using the same sidecar — not supported;
  we own this file.

## Out of scope (v1)

- Per-day-of-week schedules (`weekdays only`). Doubles the config
  surface for marginal benefit.
- Multi-window (`08:00-09:30 + 18:00-23:00`). Same reasoning.
- Auto-pause when macOS Focus mode is enabled. Possible via
  `defaults read com.apple.controlcenter`, but the API is
  unofficial and Focus already kills the banner — diminishing
  returns.

## Technical feasibility

**Confidence:** high &nbsp;·&nbsp; **Estimated effort:** ~1 day

**Confirmed:**
- All required primitives exist: bash `date +%s`, Python
  `datetime`, sidecar reads/writes follow the existing
  `agent-state.tsv` pattern.
- Hooks already gate their actions on config flags
  (`notify_on_stop`, `notify_on_wait`). Adding one more gate is a
  ~10-line diff per hook.
- Tools-submenu status line + click handlers fit
  [PLUGIN.md § Adding a submenu action](../../PLUGIN.md). Existing
  `ack-fresh.sh` / `forget-sessions.sh` are templates.

**Needs verification:** none material.

**Risks:**
- DST edge: if the user's `quiet_hours` straddles 02:00 on a
  spring-forward day, that minute simply doesn't exist; the hook
  fires through it. Acceptable — the next minute is still inside
  the window.
- Time-format parsing has a long tail (`23:00`, `23.00`, `11pm`).
  We accept only `HH:MM` 24h; reject everything else with a
  warning. Documented in [docs/configuration.md](../configuration.md).

**Mitigations:**
- Strict `HH:MM` regex in the config validator; out-of-range
  values fall back to default (same pattern as
  `context_warning_threshold` today).
- Sidecar timestamp is ISO-8601 with seconds — unambiguous across
  locales.
