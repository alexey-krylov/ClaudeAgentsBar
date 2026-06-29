# Spec 0011 — Subscription usage monitor (line + alerts)

* Status: **Implemented**
* Date: 2026-06-29

## Why

The Claude.ai subscription meters a rolling **5-hour usage window**. Nothing
in the menu told you how close that window was to exhaustion until Claude Code
itself started warning — by which point you've already lost the session. This
adds, behind one master switch (on by default):

1. **A live usage line** in the main menu showing 5-hour and weekly usage.
2. **Escalating threshold alerts** — a one-shot notification when the 5-hour
   window's `used_percentage` first crosses 50/60/70/80/90 % (template A) and a
   distinct final alert at 95 % (template B).

## Where the data comes from — the background monitor

`rate_limits` is exposed by Claude Code **only** on a `statusLine` stdin, and
**only inside a real interactive TUI** (not `claude -p`, not headless, not the
VSCode extension — verified). So we hold a **hidden background `claude`
session** in a detached `screen` and read its status line. See
[ADR-0018](../adr/0018-usage-sensor-statusline-chain.md) for the full rationale.

`claude_agents_bar/usage_monitor.reconcile(now)` runs on the plugin tick (after
`usage_alerts`, same crash-isolation), mirroring `keep_awake`'s lifecycle:

* master `usage_monitor` **off** → kill the session if running; nothing else.
* **on**, session dead → `screen -dmS cab-usage-mon … claude --model <haiku>`
  in the trusted folder `~/.claude/cab-usage-monitor` (no window).
* **on**, alive → **recycle** the session (kill + respawn) every
  `usage_ping_interval_sec` (default 10 min). A long-lived session's
  `rate_limits` go stale (the server only refreshes them on a fresh API
  response, and stuff-pinging a live TUI proved unreliable), so cycling forces a
  new first response with current `used_percentage` (account-wide — catches
  VSCode usage too); `refreshInterval` only re-renders between recycles.

The session is tracked by its unique `screen` **name**, not a PID (no PID-reuse
ambiguity). `setup` wires the sensor as that session's `statusLine`, sets
`statusLine.refreshInterval = 8`, creates the trusted folder, and marks it
trusted in `~/.claude.json`. The sensor `hooks/usage-sensor.sh` writes the
snapshot and chains any pre-existing user statusLine; `teardown` reverses all of
it and `--usage-monitor-shutdown` quits the session.

Snapshot row (`record_ts  five_used  five_resets_at  seven_used  seven_resets_at`):
`*_resets_at` / `record_ts` are unix epoch seconds; `five_used`/`seven_used` are
**floored** integer percentages (matching Claude Code's USAGE view — a 50 %
threshold fires only at a real ≥ 50 %, not 49.6 %).

## Master switch

`usage_monitor` (`"on"` default / `"off"`) — config knob + sidecar override
(`agent-state.usage-monitor.mode`, written by *Tools → Usage monitor*) +
`usage_monitor_enabled()` gate. **On by default** (zero-config — `setup` trusts
the work folder); it runs a real background `claude` and pings spend a little
Haiku quota, so flip it off if you'd rather not. Off → background
session stopped, usage line hidden, alerts silenced. `notify_on_usage` is a
**sub-flag** under it (alerts on/off while the monitor runs). New knobs:
`usage_ping_interval_min` (floored at 5), `usage_ping_model` (safe charset, it
goes into a shell command).

"Session" throughout means the 5-hour window — **not** a Claude Code chat or
the context window.

## Part 1 — threshold alerts

`claude_agents_bar/usage_alerts.reconcile(now)` runs each tick (right after the
idle-reminder reconcile, same crash-isolation), reusing the
finished-once-per-window pattern of spec 0008:

* 50/60 fire `notify_usage_phrase_threshold` (default `"Session limit at
  {pct}%"`); 70/80/90 fire `notify_usage_phrase_threshold_reset` (default
  `"Session limit at {pct}%, resets in {until}h"`) which also quotes the hours
  left; 95 fires `notify_usage_phrase_critical` (default `"Session limit almost
  exhausted, resets in {until}h"`). `{pct}` → percentage (the actual value, not
  the threshold), `{until}` → whole hours until reset (the time unit lives in
  the phrase so it localizes). Banner title is `notify_usage_title` (default
  `"Current usage"`). All defaults English; override any to localize.
* Progress is one `(window_key, max_threshold_fired)` row in
  `agent-state.usage-alerts`, keyed by the window's `resets_at`. A different key
  means the window rolled over → counter resets to 0 → the fresh window alerts
  from 50 % again.
* **Collapse**: a multi-threshold jump between ticks (48 % → 72 %) fires a
  **single** banner at the actual 72 % (kind A) and jumps the counter to 70 —
  not three back-to-back banners (protects against speech-queue spam, same
  rationale as spec 0008).
* The notification carries the **actual** current percentage, not the threshold.
* **Off / inert** when `notify_on_usage` is false, when there's no snapshot
  (API-key auth / no sensor), or when the window has expired
  (`now >= five_resets_at` — stale snapshot).

`hooks/notify-usage.sh` is the thin notifier (fired by the plugin via
`/bin/bash` with positional `<pct> <kind>`, like `notify-idle.sh`). It is
**account-wide**, so it passes empty session url / id / cwd to
`_emit_notification` (a non-clickable banner with no project subtitle). It
honors **quiet hours and the Banner-only audio switch identically to
stop/wait/idle** — same `_compute_quiet_state` + `_notify_audio_enabled`
pipeline (`quiet_hours_silences` default `["sound","voice"]` mutes audio but
keeps the banner; add `"banner"` for full silence). Default chime
`notify_sound_usage` = `Glass` (distinct from Hero/Funk/Submarine).

## Part 2 — the usage line

`render._print_usage_line` prints one grey, **passive** (non-clickable)
``--`` sub-item under a top-level *Statistics* menu (next to *Today…* and the
monitor toggle), mirroring Claude Code's own USAGE view:

```
Session: 22% · 3h · Week: 8% · 4d
```

`22%` = `five_used`, `3h` = time until `five_resets_at`, `8%` = `seven_used`,
`4d` = time until `seven_resets_at`. Reset times are localized and rounded —
10-minute precision under a day (RU `3ч` / `2ч 20м` via `_humanize_age`), whole
days at a day or more (`4д`, like USAGE's "Resets in 4d"). The session/week
numbers are **colour-coded**: bare (grey) below 60 %, **yellow ≥60 %, red ≥85 %**
— ANSI spans on the numbers (`ansi=true` on the line; the escapes contain no
`{}` so `_t().format` leaves them alone). Separator is a middle dot `·`, not a
pipe (a `|` in a SwiftBar label would truncate it). A ``--`` sub-item rather than
top-level because SwiftBar gives every top-level item a refresh/about submenu and
an arrow — this is just text.

Shown only when the **monitor is on** (`usage_monitor_enabled()`),
`notify_on_usage` notwithstanding, and the snapshot is fresh. Two staleness
gates hide it gracefully: `now >= five_resets_at` (window expired) and
`now - record_ts > 2 × usage_ping_interval_sec` (the background session died and
the snapshot froze). The string is localized (`menu.usage`) across all 8 locales.

## Config knobs

* `usage_monitor` (`"on"` default / `"off"`) — **master**; mode string like
  `keep_awake`, with sidecar override + *Tools* toggle.
* `usage_ping_interval_min` (default 10, floored at 5) → `usage_ping_interval_sec`.
* `usage_ping_model` (default Haiku) — `[A-Za-z0-9._-]` only (shell-interpolated).
* `notify_on_usage` (bool, default true) — alerts sub-flag under the master.
* `notify_usage_title` / `notify_usage_phrase_threshold` /
  `notify_usage_phrase_threshold_reset` / `notify_usage_phrase_critical` /
  `notify_sound_usage` — read directly by the bash notifier.

## Acceptance

1. `bash -n hooks/usage-sensor.sh hooks/notify-usage.sh
   bin/install/setup.sh bin/install/teardown.sh` exits 0.
2. `usage-sensor.sh` fed a payload with `rate_limits.five_hour.used_percentage`
   18 / `seven_day` 17 writes `…\t18\t…\t17\t<seven_reset>`; a dropping fraction
   floors (`18.6 → 18`, `49.9 → 49`); a payload with no `rate_limits` writes no
   sidecar; the chain proxies the original statusLine's stdout. *(verified)*
3. `setup`'s statusLine wrap is idempotent and `teardown` restores the
   byte-identical original. *(verified via isolated jq round-trip)*
4. `read_usage` round-trips a valid row and returns `None` on absent / `<5`
   columns / non-numeric / empty. `read/write_usage_alerts` round-trip;
   `write(None)` removes the file; corrupt → `None`.
5. `reconcile`: off → no-op (sidecar untouched); no snapshot → no-op; expired
   window → no-op; first cross at 52 → `_fire(52,"A")` + writes `(window,50)`;
   no re-fire at 55 within the same window; boundary 50 fires; 48→72 collapses
   to a single `_fire(72,"A")` + `(window,70)`; 96 with prior 90 →
   `_fire(96,"B")` + `(window,95)`; a new `resets_at` resets the counter.
6. `render._print_usage_line`: snapshot present → one grey `--` line with the
   substituted `{sess}/{until}/{wk}/{wk_until}` and no bare `|` in the label;
   absent / expired / monitor off → nothing.
7. Full `unittest` suite green (372 tests, incl. the above).
8. **Manual GUI required before release** (automated checks can't exercise the
   live statusLine, `say`, the banner, or SwiftBar): wire the sensor via
   `setup`, confirm the Tools line shows the right `%`/time and matches Claude
   Code's own USAGE view; cross a real threshold and confirm one banner + voice;
   confirm quiet hours / *Banner only* mute it like the other notifications;
   confirm the chained original statusLine still renders; run `teardown` and
   confirm the original statusLine is restored.

## Out of scope

* Threshold alerts for the **weekly** window — weekly is shown in the Tools
  line only, never alerted.
* A weekly pacing target — the line shows the raw weekly used-% and reset, like
  Claude Code's own USAGE view; no self-imposed pacing model.
* A Tools-menu toggle — the switch is the `notify_on_usage` config knob (like
  `notify_on_stop` / `notify_on_wait`).
