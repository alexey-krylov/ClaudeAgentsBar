# Spec 0008 — Idle-session reminders

* Status: **Implemented**
* Date: 2026-06-13

## Why

`Stop` (spec 0005/0007) announces a session **once**, the moment it
finishes. But a session you don't come back to just sits in the menu
🟢 green, unread — easy to forget among several. There was no nudge to
say "this one's still waiting for you."

This adds an escalating reminder: a finished, **unread** (not yet
clicked) session is re-announced at doubling intervals — 20, 40, 80, …
minutes after it finished — until you act on it.

## Why it rides the tick, not a hook

Spec 0005/0007 notifications are driven by Claude Code hook events
(`Stop`, `PermissionRequest`). There is **no** event that fires "N
minutes after the session finished," and the project deliberately runs
no daemon (stateless, no IPC — see PLUGIN.md). The only periodic
heartbeat is the SwiftBar tick: SwiftBar re-runs the plugin every 5 s
whether or not the menu is open.

So the reminder is **plugin-driven**, not hook-driven.
`claude_agents_bar/idle_reminders.py::reconcile(sessions, now)` is called
once per tick from `main` (right after `keep_awake.reconcile`, reusing
the session list already built for the menu). It finds the green-and-
unread sessions that have crossed their next interval and fires
`hooks/notify-idle.sh` for each.

`notify-idle.sh` is therefore **not** a registered Claude Code hook — it
is absent from `settings-hooks.json`, and takes its session id + cwd as
positional arguments rather than a JSON payload on stdin. It is invoked
via `/bin/bash <path>` so it doesn't depend on its executable bit
surviving distribution.

## The schedule

Reminder *k* (k = 1, 2, …) is due once

```
now - stop_ts >= interval * 2**(k-1)
```

where `interval = notify_idle_interval_min` (default **20 min**) and
`stop_ts` is when the session finished. So: 20, 40, 80, 160, … minutes.

**The count is not a separate knob** — it's bounded by how long the row
stays green. `_classify` auto-promotes 🟢 FRESH → 🔵 ACKNOWLEDGED after
`fresh_sec` (default 60 min) even without a click, and `reconcile` only
ever considers `RenderGroup.FRESH` sessions. With the defaults
(interval 20, fresh 60) that yields **two** reminders, at 20 and 40 min
(80 > 60 is past the green window). Raising `fresh_minutes` allows more;
shortening `notify_idle_interval_min` fits more in.

A click (or *Tools → Acknowledge all*) moves the session out of the green
group, ending the schedule. A new finished turn gives a fresh `stop_ts`,
which restarts it from reminder #1.

## The knob

`notify_idle_interval_min` (minutes) is the base interval **and** the
on/off switch:

| Value | Effect |
|---|---|
| absent | default 20 — feature **on** |
| `> 0` | base interval in minutes |
| `0` / `null` / negative | feature **off** |

It's parsed by hand in `Config._from_mapping` (not the generic `take`
helper): an explicit `null` must map to *off*, not *keep default*, and a
negative value clamps to off rather than scheduling a reminder in the
past. Internally it's stored as `notify_idle_interval_sec` (0 = off, so
`reconcile` returns early).

Two bash-only knobs shape the announcement (read directly by the script,
like `notify_phrases` / `notify_sound_*`):

* `notify_idle_phrases` — spoken/banner phrases, default
  `["Don't forget me", "Still unread", "Pending review", "Your turn"]`.
* `notify_sound_idle` — chime, default `"Submarine"` (distinct from
  `Hero` = done, `Funk` = awaiting).

## State tracking

`~/.claude/agent-state.idle-reminders`, a `{sid → (stop_ts, fired_count)}`
TSV (`sid\tstop_ts\tfired_count`), records how far each green session's
schedule has progressed so a reminder isn't re-sent every tick.

`reconcile` rebuilds the map from scratch each tick, keeping a row only
for sessions that are FRESH *now* and have fired ≥ 1 reminder. Sessions
that left the green group (clicked / promoted / gone) simply drop out, so
the rewrite prunes them — no separate GC pass. A stored `stop_ts` that
differs from the session's current one means the session finished again,
so the counter resets. Writes are atomic (tmp + `replace`) under a
`mkdir` lock (`_IDLE_REMINDERS_LOCK_DIR`), matching the other sidecars.

## Cost discipline

The tick is the hot path, so `reconcile` does only cheap work: read the
small sidecar, compare timestamps, and `Popen` a detached script. All
transcript parsing (the session name + summary spoken in the reminder)
happens inside `notify-idle.sh`, off the tick — the same back-pressure
rule as the Remind action (spec 0006).

## DRY: shared emit

Adding a third notification surface would have tripled the copy-pasted
chime + `say` + banner block. Instead that tail and the random-phrase
picker were factored into `_emit_notification` / `_pick_phrase` in
`hooks/_notify-common.sh`; `notify-stop.sh`, `notify-wait.sh` and
`notify-idle.sh` are thin shims that set sound / phrases / title and call
them. Behaviour of the existing two hooks is unchanged. `notify-idle.sh`
is composed exactly like `notify-wait.sh` (phrase → name → summary,
`name — summary` banner, name+summary via `_marker_fields_latest`), since
a finished session's last turn carries its closing marker.

## Verification

* `Config` unit tests: `notify_idle_interval_min` → `_sec`
  (20 → 1200, fractional, 0 / null / negative → 0, non-number / bool →
  default, absent → default).
* sidecar tests: `read`/`write_idle_reminders` round-trip, empty state
  removes the file, corrupt rows dropped.
* `reconcile` tests (with `_fire` patched out): feature-off does nothing;
  fires past the first threshold and records `(stop_ts, 1)`; no fire
  before; non-FRESH ignored; no re-fire within the same window; second
  reminder at the doubled threshold; a new `stop_ts` resets the counter;
  catch-up fires each missed threshold after a tick gap; a session that
  left FRESH is pruned from the sidecar.
* `bash -n` on `_notify-common.sh` + all three notify scripts; full
  `unittest` suite green.
* Manual GUI required before release (automated checks can't exercise
  `say`, the banner, or the live tick): a green session left past the
  interval gets a chime + spoken name/summary + banner; clicking it stops
  further reminders; quiet hours / *Banner only* mute the audio.

## Out of scope

* A *Tools* menu toggle — the config knob (like `notify_on_stop` /
  `notify_on_wait`) is the switch; no live menu control.
* A separate "max reminders" knob — the count is emergent from
  `fresh_minutes`.
* Reminders for 🔵 acknowledged or ⚪ stale sessions — only the green,
  never-clicked group is in scope.
