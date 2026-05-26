# Spec 0002 — Quiet hours

* Status: Draft
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

Default `quiet_hours_silences` is the full list — quiet means
silent. Users who want to keep just the banner (no chime, no
voice) drop `"banner"`.

## Config

```jsonc
{
  "quiet_hours": "23:00-09:00",        // null disables scheduled
  "quiet_hours_silences": ["banner", "sound", "voice"]
}
```

Defaults: `quiet_hours: null`, full-silence list. Current behaviour
preserved when the config is missing or unset.

## Menu actions

New entries under `bin/app/`:

- `quiet-pause.sh <duration>` — writes
  `agent-state.quiet-until = now + duration`. `duration` is
  `1h` / `tomorrow` / explicit ISO.
- `quiet-resume.sh` — unlinks the sidecar.

`Pause until tomorrow morning` resolves to the *end* of the
configured `quiet_hours` window if defined, otherwise 09:00 local.

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
