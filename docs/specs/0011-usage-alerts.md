# Spec 0011 — Subscription usage alerts + Tools usage line

* Status: **Implemented**
* Date: 2026-06-24

## Why

The Claude.ai subscription meters a rolling **5-hour usage window**. Nothing
in the menu told you how close that window was to exhaustion until Claude Code
itself started warning — by which point you've already lost the session. This
adds two things, both fed by the same data:

1. **Escalating threshold alerts** — a one-shot notification when the 5-hour
   window's `used_percentage` first crosses 50/60/70/80/90 % (template A) and a
   distinct final alert at 95 % (template B).
2. **A static usage line** in the Tools submenu showing the live 5-hour and
   weekly usage at a glance.

## Where the data comes from

`rate_limits` is on the statusLine stdin only — see
[ADR-0018](../adr/0018-usage-sensor-statusline-chain.md) for why this forces a
statusLine sensor (`hooks/usage-sensor.sh`) that writes the
`agent-state.usage` sidecar, and how it chains the user's original statusLine.

Snapshot row (`record_ts  five_used  five_resets_at  seven_used  seven_target`):
`*_resets_at` and `record_ts` are unix epoch seconds; `five_used`/`seven_used`
are **floored** integer percentages (matching Claude Code's own USAGE view —
it never shows a percentage higher than the real one, and a 50 % threshold then
fires only at a real ≥ 50 %, not 49.6 %); `seven_target` is the weekly pacing
target (a string, may be fractional like `69.5`).

"Session" throughout means the 5-hour window — **not** a Claude Code chat or
the context window.

## Part 1 — threshold alerts

`claude_agents_bar/usage_alerts.reconcile(now)` runs each tick (right after the
idle-reminder reconcile, same crash-isolation), reusing the
finished-once-per-window pattern of spec 0008:

* Thresholds 50/60/70/80/90 fire **template A** (`notify_usage_phrase_threshold`,
  default `"Session limit at {pct}%"`, `{pct}` → current percentage); 95 fires
  **template B** (`notify_usage_phrase_critical`, default
  `"Session limit almost exhausted — only a refresh restores it"`).
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

## Part 2 — the Tools usage line

`render._print_usage_line` prints one grey, inactive line under *Tools → Stats
today* whenever a snapshot exists and the window hasn't expired:

```
Session: 63% · 2h48m · Week: 7%/69.5%
```

`63%` = `five_used`, `2h48m` = time until `five_resets_at` (formatted
`1d4h`/`2h48m`/`42m`/`<1m`), `7%` = `seven_used`, `69.5%` = `seven_target`. The
separator is a middle dot `·`, **not** a pipe — a `|` in a SwiftBar label is the
label/params delimiter and would truncate the line. Shown **regardless of
`notify_on_usage`** (that gates only the alerts); absent or stale snapshot →
no line (graceful). The string is localized (`menu.usage`) across all 8 locales.

### Weekly pacing target

`seven_target` is a personal office-hours pacing model computed **in the
sensor** (`WK_CUM` cumulative-target lookup by weekday in `WEEK_TZ` relative to
`WEEK_RESET_HOUR`), so the Python side carries no timezone/date logic. Edit the
constants at the top of `hooks/usage-sensor.sh` to match your own schedule.

## Config knobs

* `notify_on_usage` (bool, default true) — the alerts on/off switch (like
  `notify_on_stop` / `notify_on_wait`). Parsed by hand in `_from_mapping`
  (non-bool → keep default), mirroring `notify_audio`.
* `notify_usage_phrase_threshold` / `notify_usage_phrase_critical` /
  `notify_sound_usage` — read directly by the bash notifier, like the other
  `notify_*` knobs.

## Acceptance

1. `bash -n hooks/usage-sensor.sh hooks/notify-usage.sh
   bin/install/setup.sh bin/install/teardown.sh` exits 0.
2. `usage-sensor.sh` fed a payload with `rate_limits.five_hour.used_percentage`
   18 / `seven_day` 17 writes `…\t18\t…\t17\t<target>`; a dropping fraction
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
   substituted `{sess}/{until}/{wk}/{target}` and no bare `|` in the label;
   absent / expired → nothing.
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
* Porting the weekly pacing model into Python config — it lives in the sensor
  (bash); the plugin only displays the precomputed target.
* A Tools-menu toggle — the switch is the `notify_on_usage` config knob (like
  `notify_on_stop` / `notify_on_wait`).
