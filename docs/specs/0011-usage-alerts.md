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

## Where the data comes from — the periodic `get_usage` fetch

> **Revised in 1.5.0.** The original design held a hidden background `claude`
> TUI in a detached `screen` and scraped `rate_limits` off its `statusLine`
> stdin ([ADR-0018](../adr/0018-usage-sensor-statusline-chain.md)), because that
> was the only place Claude Code exposed them. It isn't any more — see
> [ADR-0020](../adr/0020-usage-via-sdk-get-usage.md). The alerts, the snapshot
> format and the master switch below are unchanged; only the source moved.

The `claude` CLI answers a `get_usage` control request over the SDK control
protocol with the account's live `rate_limits`:

```bash
printf '{"type":"control_request","request_id":"r","request":{"subtype":"get_usage"}}' \
  | claude -p --verbose --input-format stream-json --output-format stream-json
```

~1.7 s, no inference (`total_cost_usd: 0`), no quota, no transcript, no hooks,
no folder trust.

`claude_agents_bar/usage_monitor.reconcile(now)` runs on the plugin tick (after
`usage_alerts`, same crash-isolation) and owns no process:

* master `usage_monitor` **off** → nothing at all.
* **on**, less than `usage_fetch_interval_sec` since the last fetch → nothing.
* **on**, due → stamp `agent-state.usage.fetch` (**before** spawning, so a hung
  fetch can't stack) and spawn `claude-agents.5s.py --usage-fetch` detached.

`usage_monitor.fetch(now)` takes the cheapest source that works: Claude Code's
own `cachedUsageUtilization` in `~/.claude.json` when it's fresher than the
fetch interval (no process at all), else the live call, else that same cache up
to an hour old. When every source fails it writes nothing — the previous
snapshot ages out and the lines hide themselves.

Snapshot row (`record_ts  five_used  five_resets_at  seven_used  seven_resets_at`):
`*_resets_at` / `record_ts` are unix epoch seconds; `five_used`/`seven_used` are
**floored** integer percentages (matching Claude Code's own usage view — a 50 %
threshold fires only at a real ≥ 50 %, not 49.6 %). `resets_at` arrives as an
ISO-8601 string and is converted here. `record_ts` is when the data was
*fetched*: a cached payload keeps Claude Code's timestamp, so it can't
masquerade as fresh.

## Master switch

`usage_monitor` (`"on"` default / `"off"`) — config knob + `usage_monitor_enabled()`
gate. **On by default** (zero-config, nothing to install). There is no menu
toggle since 1.5.0: the fetch spends no quota, so there is no background cost
worth a checkbox. Off → no fetch, usage lines hidden, alerts silenced.
`notify_on_usage` is a **sub-flag** under it (alerts on/off while the feature
runs). Interval knob: `usage_fetch_interval_min` (default 3, floored at 1).

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
`now - record_ts > 2 × usage_fetch_interval_sec` (fetches stopped landing and
the snapshot froze). The string is localized (`menu.usage`) across all 8 locales.

## Config knobs

* `usage_monitor` (`"on"` default / `"off"`) — **master**; mode string like
  `keep_awake`, with sidecar override + *Tools* toggle.
* `usage_fetch_interval_min` (default 3, floored at 1) → `usage_fetch_interval_sec`.
* `notify_on_usage` (bool, default true) — alerts sub-flag under the master.
* `notify_usage_title` / `notify_usage_phrase_threshold` /
  `notify_usage_phrase_threshold_reset` / `notify_usage_phrase_critical` /
  `notify_sound_usage` — read directly by the bash notifier.

## Acceptance

1. `bash -n hooks/notify-usage.sh bin/install/setup.sh bin/install/teardown.sh`
   exits 0.
2. The `get_usage` control request returns `rate_limits` with `five_hour` /
   `seven_day` (`utilization` + ISO `resets_at`) and reports
   `total_cost_usd: 0` — no inference, no transcript under `~/.claude/projects`,
   no `session-env` entry, no hook rows in `agent-state.tsv`, and no trust
   prompt from an untrusted cwd. *(verified 2026-08-27, CLI 2.1.231)*
3. `snapshot_from_rate_limits` floors percentages (`10.9 → 10`), converts ISO
   `resets_at` (offset and `Z` forms) to epoch, refuses a payload with no
   usable 5-hour window, and degrades a missing weekly window to `0\t0`.
   `rate_limits_from_response` skips noise/error frames and returns `None` for
   `rate_limits_available: false`.
4. `fetch`: a cache fresher than the interval short-circuits the CLI entirely
   and keeps *its own* `fetchedAtMs` as `record_ts`; a stale cache falls through
   to the call; a failed call falls back to an hour-old cache; with every source
   dead nothing is written and the previous snapshot is left alone.
   `reconcile`: first tick fetches, marker written before the spawn, no second
   fetch inside the interval, another one past it, none at all when off.
5. `read_usage` round-trips a valid row and returns `None` on absent / `<5`
   columns / non-numeric / empty. `read/write_usage_alerts` round-trip;
   `write(None)` removes the file; corrupt → `None`.
6. `usage_alerts.reconcile`: off → no-op (sidecar untouched); no snapshot →
   no-op; expired window → no-op; first cross at 52 → `_fire(52,"A")` + writes
   `(window,50)`; no re-fire at 55 within the same window; boundary 50 fires;
   48→72 collapses to a single `_fire(72,"A")` + `(window,70)`; 96 with prior
   90 → `_fire(96,"B")` + `(window,95)`; a new `resets_at` resets the counter.
7. `render._print_usage_lines`: fresh snapshot → two grey `--` lines whose bars
   and percentages align in the same columns, zone colours at ≥60/≥85, a
   non-zero percentage never rendering an empty bar, and no bare `|` in a
   label; absent / expired / stale / feature off → nothing; no weekly window →
   session row only.
8. Full `unittest` suite green (614 tests, incl. the above).
9. **Manual GUI required before release** (automated checks can't exercise
   `say`, the banner, or SwiftBar): confirm the two Statistics lines show the
   right `%`/time and match the IDE extension's usage panel; cross a real
   threshold and confirm one banner + voice; confirm quiet hours / *Banner
   only* mute it like the other notifications; on an upgrade from 1.4.x confirm
   `setup` restored the original `statusLine` and left no `cab-usage-mon`
   session behind.

## Out of scope

* Threshold alerts for the **weekly** window — weekly gets a line, never an
  alert.
* A weekly pacing target — the line shows the raw weekly used-% and reset, like
  the IDE extension's panel; no self-imposed pacing model.
* Surfacing the rest of the `get_usage` payload (`seven_day_opus/sonnet`,
  `model_scoped[]`, `extra_usage` credits, the CLI-computed `behaviors`
  request/session counts). Available, deliberately not shown yet.
