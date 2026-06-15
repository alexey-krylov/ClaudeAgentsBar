# Spec 0010 — Speech serialization lock

* Status: **Implemented**
* Date: 2026-06-15

## Why

Spoken notifications overlap. Every notify path runs its speech in its own
detached background `say(1)`:

* `hooks/notify-stop.sh` / `notify-wait.sh` / `notify-idle.sh` →
  `_emit_notification` in `hooks/_notify-common.sh`,
* the *Remind* row click → `bin/app/remind-session.sh`.

There is no coordination between them. When two events land in the same
moment — a session finishing (Stop) just as the plugin fires an idle
reminder for a *different* session (spec 0008), or a *Remind* click while a
notification is mid-sentence — two `say` processes talk at once. The voices
mix and you can't tell which session is speaking or what it said, which
defeats the whole point of the spoken summary (spec 0005).

The goal: **one `say` speaks at a time**, with a short, configurable pause
between utterances so they don't run together as one breath; and a stale
utterance that has queued too long is dropped rather than read out behind
reality.

## Why a `mkdir` lock, not a daemon or `flock`

The project is deliberately stateless — no daemon, no IPC, every tick
rebuilds from disk (see PLUGIN.md). So the only place a cross-process mutex
can live is the filesystem. macOS ships **no `flock(1)`**, so the primitive
is an **atomic `mkdir`**: `mkdir` of an existing directory fails atomically,
which is exactly a test-and-set. The lock dir is
`~/.claude/agent-state.say.lock`.

It is transient — created when a process is about to speak, removed when it
finishes — so unlike the other `~/.claude/agent-state.*` sidecars it holds
no user state and teardown ignores it.

## How it works

Two helpers in `hooks/_notify-common.sh`, shared by `_emit_notification`
and `remind-session.sh`:

* **`_say_lock_acquire`** — spins on `mkdir` until it owns the lock.
  Returns `0` when held (the caller speaks, then must release); returns `1`
  when this utterance has waited longer than `notify_say_stale_sec` and
  should be dropped unspoken.
* **`_say_lock_release`** — holds the lock for `notify_say_gap_sec`, then
  `rmdir`s it. Holding *before* releasing is what creates the inter-utterance
  pause: the next waiter can't `mkdir` until the dir is gone.

Because `say` blocks the foreground of the speaking subshell, the lock is
held for the entire spoken duration plus the gap — correct serialization.
The 1 s lead in `_emit_notification` (so the chime plays before the voice)
stays *outside* the lock, so it's a per-notification cost, not a queued one.

*Remind* acquires the lock **once** around its whole was→now read, so a
concurrent notification can't wedge between the two phrases.

### Crash recovery — stealing a dead holder's lock

If a speaking process dies (killed, crash) it leaves the lock dir behind. A
waiter must be able to steal it, else speech wedges permanently. The lock
dir stores the **holder's pid**; a waiter steals (`rm -rf` + retry) when
`kill -0 <pid>` shows that pid is gone.

Getting the holder's own pid is the subtle part: inside a `( ) &` subshell,
`$$` is the **parent** shell's pid (a bash quirk), and bash 3.2 — what
`/usr/bin/python3`-era macOS `/bin/bash` is — has no `BASHPID`. The holder
records its real pid with `sh -c 'echo $PPID'`: the forked `sh`'s parent is
the speaking subshell, so its `$PPID` is exactly the pid we want.

`_SAY_LOCK_CEILING` (120 s, a hook constant) is a backstop for the rare case
where the dead holder's pid was reused by an unrelated live process: a lock
dir older than the ceiling is stolen regardless of pid liveness.

## Scope — speech only

Only `say` is serialized. The chime (`afplay`) and the `terminal-notifier`
banner still fire immediately and in parallel, as before — short chimes
overlapping isn't a problem and banners are visual. Quiet hours (spec 0002),
the *Banner only* audio master switch, and `notify_voice: "off"` still
suppress speech upstream of the lock; the lock only orders what does get
spoken.

## Config

Both knobs are **hook-only** (read with `jq` in Bash, like
`notify_voice` / `notify_sound_*`); they are not part of the Python
`core.Config` dataclass because the menu never speaks.

| Knob | Default | Meaning |
|---|---|---|
| `notify_say_gap_sec` | `1` | Pause held after each spoken notification before the next may start, in seconds (fractions ok). `0` keeps serialization but adds no pause. |
| `notify_say_stale_sec` | `30` | An utterance that has waited for the lock longer than this is dropped unspoken. |
