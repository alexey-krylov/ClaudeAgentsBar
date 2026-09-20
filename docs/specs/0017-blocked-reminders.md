# Spec 0017 — Blocked-session reminders

* Status: **Implemented**
* Date: 2026-09-20

## Why

`hooks/notify-wait.sh` fires **once**, on the `PermissionRequest` /
`Notification` event: chime, spoken phrase, banner. If you are in a meeting,
on another screen, or simply looking elsewhere when it lands, that is the
only time anyone tells you. The agent then stands on the prompt until you
happen to glance at the menu bar.

That is the most expensive silence the bar can keep. A 🟢 finished session
you have not read costs you *latency* — the work is done, it is waiting for
you to collect it, and [spec 0008](./0008-idle-reminders.md) already
re-nudges about it. A 🔴 blocked session costs you *throughput*: nothing is
happening, and nothing will, until you answer. The cheaper case had a
reminder; the expensive one did not.

The Claude Code extension 2.1.276 does not fill this in either — it shows
state, it does not push. Pushing is what the bar is for.

## What it does

While a session's hook state is `waiting`, the plugin re-fires the *same*
notification on a doubling schedule, starting
`notify_blocked_interval_min` minutes after the session entered that state:

| Repeat | Due at (default 10 min) |
|---|---|
| 1st | 10 min |
| 2nd | 30 min |
| 3rd | 1 h 10 m |
| 4th | 2 h 30 m |
| 5th | 5 h 10 m |

Answering the prompt moves the session out of `waiting`, and its row drops
out of the sidecar on the next tick — the schedule simply stops.

**It is the same notification, not a variant.** The plugin re-runs
`hooks/notify-wait.sh` — the very script the first announcement came from —
so the repeat carries the same `notify_wait_phrases`, the same
`notify_sound_wait`, the same ❓ banner and the same click target. A
separate script would have meant a second phrase list, a second chime and a
second thing to keep in sync for no gain; a repeat that looks different
from the thing it repeats is just a new notification the user has to learn.

**The doubling is the bound.** The idle reminder is bounded by
`fresh_sec`: the row leaves 🟢 on its own and the reminders end with it.
Nothing does that here — a permission prompt can stand for a day — so the
schedule has to thin itself out, which doubling does: five nudges cover the
first five hours and the sixth is four hours after that.

Default **10 minutes**, deliberately shorter than the idle reminder's 30:
being blocked is worse than being unread.

## Why it rides the tick, not a hook

Same reason as spec 0008. There is no Claude Code event for "you are still
blocked", and the project runs no daemon, so the only periodic heartbeat is
the SwiftBar tick. `claude_agents_bar/idle_reminders.reconcile_blocked` runs
there, on the session list the menu was going to build anyway.

`hooks/notify-wait.sh` therefore has **two input shapes**. As a registered
Claude Code hook it reads a JSON payload on stdin and takes no arguments;
as the reminder it takes `<session-id> <cwd>` positionally and reads no
stdin. Arguments are the discriminator, because Claude Code never passes
any. The plugin invokes it as `/bin/bash <path>` so a lost executable bit
can't silence it, and the argument form doubles as the manual smoke test.

## Only a real permission prompt counts

`hooks/settings-hooks.json` maps **two** events onto the `waiting` state:
`PermissionRequest` and `Notification`. They are not the same thing —
Claude Code also fires `Notification` when the prompt has simply been idle
for a while — and only `PermissionRequest` gets the first `notify-wait.sh`
announcement.

So the selector tests both the state and `last_event_kind`. On the state
alone the feature would re-announce "I'm blocked" every ten minutes at an
ordinary finished session nobody is waiting on, and announce it as a
*repeat* of a notification that never happened. It would also displace the
idle reminder, since such a row is no longer FRESH.

The rule is simply: **a repeat has the same trigger as the thing it
repeats.** `Session.last_event_kind` was added to carry that trigger up
from the TSV.

## The episode anchor

The escalation counter is keyed on **when the session entered `waiting`** —
`Session.state_since`, added for this — not on `last_event_ts`.

That distinction is load-bearing. `last_event_ts` advances while a session
waits (the hook writes a row on events that arrive during the prompt), so
keying on it would look like a *new* waiting episode on almost every tick,
reset the counter to zero, and either re-fire constantly or never escalate.
`state_since` is preserved by the hook across consecutive events of the same
state, so it stays pinned to the moment the prompt appeared.

A session that answers one prompt and hits another gets a new `state_since`,
which correctly restarts the schedule.

A row whose `state_since` is `0` — a legacy TSV written before the hook
stamped transitions — is **skipped**, not treated as "blocked since 1970".

## State tracking

`~/.claude/agent-state.blocked-reminders`, three tab-separated columns:

```
<session_id>\t<waiting_since>\t<fired_count>
```

Identical shape to `agent-state.idle-reminders`, and the two share their
reader/writer (`sidecars._read_reminders` / `_write_reminders_locked`) and
their escalation engine (`idle_reminders._reconcile`). They do **not** share
a file: each feature's progress has to survive the other being switched off.

The sidecar is rebuilt from scratch each tick, keeping only rows that have
actually been nudged, so an unblocked session is pruned by the rewrite — no
separate GC pass. The whole read→decide→fire→write runs under
`_blocked_reminders_lock`, because SwiftBar runs the plugin concurrently
(scheduled tick plus hook-fired `refreshallplugins`) and two ticks must not
both decide the same step is due.

## The knob

One, because the repeat borrows everything else from the first
announcement:

| Key | Default | Read by |
|---|---|---|
| `notify_blocked_interval_min` | `10` | Python (the tick) |

`0` / `null` / a negative value all mean "announce once, never repeat".
`notify_on_wait: false` silences the first announcement and, since it is
the same script, every repeat with it. Like `notify_idle_interval_min`
this can't go through the generic `take()` helper — an explicit `null` has
to mean "off", not "keep the default" — so both intervals share one
hand-rolled coercion in `Config._from_mapping`.

## Cost discipline

The tick is the hot path. This module reads one tiny sidecar, compares
integers, and `Popen`s a detached script. All transcript parsing (the name
and summary that get spoken) happens inside the bash script, off the tick.

## Out of scope

* A cap on the number of reminders — the doubling already provides one.
* Escalating differently per session, or per project.
* Anything for `working` sessions: nobody is blocked on the user there, and
  the watchdog already handles a `working` row that stops emitting events.
* Repeating a `Notification`-induced wait. If that event ever becomes worth
  announcing, it needs its own first notification before it can have a
  repeat.
