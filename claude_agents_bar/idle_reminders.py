"""Tick-fired session reminders — re-nudge the user about a session that sits.

Two of them, sharing one escalation engine:

* 🟢 **idle** (spec 0008) — the session *finished* and the user never came
  back to it. Bounded on its own: ``Config.fresh_sec`` promotes the row out
  of FRESH, which ends the schedule.
* 🔴 **blocked** (spec 0017) — the session is *waiting* on a tool-approval
  prompt. Nothing bounds this one; the doubling is the bound.

Stop / PermissionRequest notifications fire once, on the Claude Code hook
event. There is no event that fires "20 minutes after the session finished"
or "you are still blocked", and the project runs no daemon — so a *time-based*
reminder has to ride the only periodic heartbeat there is: the SwiftBar tick,
which re-runs the plugin every 5 s whether or not the menu is open.

:func:`reconcile` and :func:`reconcile_blocked` are those hooks. Called once
per tick from ``__init__.main`` (right after :func:`keep_awake.reconcile`),
each walks the already-built session list, finds the sessions that have
crossed their next escalation threshold, and fires a detached notify script
for each. Progress is persisted per feature in its own sidecar
(``agent-state.idle-reminders`` / ``agent-state.blocked-reminders``) so a
reminder isn't re-sent every tick.

The schedule doubles: reminder *k* (k = 1, 2, …) is due once
``now - episode_ts >= interval * 2**(k-1)``. For the idle reminder the count
is additionally bounded by how long *fresh* lasts: reminder 2 is due at
``2 * interval``, which with the stock 30-min interval and 60-min
``fresh_sec`` is the exact instant the row stops being FRESH — so out of the
box the idle reminder fires **once**, at 30 min. Raise ``fresh_minutes`` or
shorten the interval to get more out of the doubling. For the blocked reminder
there is no such ceiling, so the doubling alone thins it out: with the default
10-min interval it fires at 10 min, 30 min, 1 h 10 m, 2 h 30 m, … and fades.

Cost discipline (the tick is the hot path): this module does only cheap work
— read a tiny sidecar, compare timestamps, and ``Popen`` a detached shell
script. All transcript parsing (the session name + summary spoken in the
reminder) happens inside the notify script, off the tick.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Iterable

from . import core, sidecars
from .core import PERMISSION_EVENT_KIND, RenderGroup, Session, _warn


def _spawn(script_name: str, session: Session, label: str) -> None:
    """Spawn a detached notify script for one session.

    ``start_new_session=True`` severs the child from SwiftBar so it
    outlives our tick, and stdio is wired to ``/dev/null`` — mirrors the
    detachment contract in :mod:`keep_awake`. The script does its own
    config / quiet-hours gating; we only pass the session id and cwd
    (cwd lets the banner click raise the right editor window). Invoked via
    ``/bin/bash <path>`` so it doesn't depend on the script's executable
    bit surviving distribution.
    """
    script = core.PLUGIN_DIR / "hooks" / script_name
    try:
        subprocess.Popen(
            ["/bin/bash", str(script), session.id, session.cwd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"{label}: notify spawn failed: {exc}")


def _fire(session: Session) -> None:
    """Fire one 🟢 idle reminder. Patched wholesale by the tests."""
    _spawn("notify-idle.sh", session, "idle_reminders")


def _fire_blocked(session: Session) -> None:
    """Fire one 🔴 blocked reminder. Patched wholesale by the tests.

    Deliberately the **same** script the first permission-prompt notification
    came from. ``notify-wait.sh`` is normally a Claude Code hook reading a
    JSON payload on stdin; given positional arguments instead it announces
    the session the same way — same phrase list, same chime, same ❓ banner.
    A repeat should be indistinguishable from the original, so there is no
    second script and no second set of phrase/sound knobs to keep in sync.
    """
    _spawn("notify-wait.sh", session, "blocked_reminders")


def _reconcile(
    sessions: Iterable[Session],
    now: int,
    *,
    interval: int,
    selects: Callable[[Session], bool],
    episode_of: Callable[[Session], int],
    path: Path,
    lock,
    fire: Callable[[Session], None],
    label: str,
) -> None:
    """Shared escalation engine for both reminders.

    Rebuilds the sidecar state from scratch each tick, keeping a row only
    for selected sessions that have actually been nudged (``fired >= 1``).
    Sessions that no longer qualify (clicked, promoted, unblocked, or gone)
    simply drop out of the rebuilt map, so the write prunes them — no
    separate GC pass.

    A new ``episode_ts`` for a session (it finished a fresh turn, or entered
    a new waiting episode) resets its counter, restarting the schedule. When
    several thresholds have been crossed since the last tick — e.g. the
    machine slept across them — the counter jumps straight to the current
    level but only **one** reminder is sent, so a catch-up is a single nudge,
    not a back-to-back burst of banners + speech.

    ``interval <= 0`` means the feature is off: return without touching the
    sidecar. Leaving a stale file behind is cheap, and it is overwritten the
    moment the user re-enables the feature.
    """
    if interval <= 0:
        return

    # Hold the sidecar lock across the whole read→decide→fire→write. SwiftBar
    # runs the plugin concurrently — the scheduled 5-s tick plus any
    # ``swiftbar://refreshallplugins`` fired by a hook or a menu action — so
    # without this two ticks could both read the same ``fired`` count for a
    # session, both cross its next threshold, and fire the same reminder twice
    # (a double banner + double speech). Serialising the section makes the
    # second tick observe the first's write and stay quiet. Reads/writes
    # inside use the unlocked helpers because the mkdir lock is not reentrant.
    with lock():
        previous = sidecars._read_reminders(path)
        updated: dict[str, tuple[int, int]] = {}

        for session in sessions:
            if not selects(session):
                continue
            episode_ts = episode_of(session)
            if episode_ts <= 0:
                # No usable anchor (the hook never stamped a transition) —
                # skip rather than treat the epoch as the episode start and
                # fire immediately.
                continue
            prev = previous.get(session.id)
            fired = prev[1] if prev is not None and prev[0] == episode_ts else 0

            elapsed = now - episode_ts
            # How many doubling thresholds the elapsed time has crossed.
            target = fired
            while elapsed >= interval * (2 ** target):
                target += 1

            # Collapse a multi-threshold catch-up — e.g. the machine slept
            # across several intervals — into a SINGLE reminder: advance the
            # counter to the current level but spawn at most one notify per
            # tick, so the user gets one nudge, not a back-to-back burst of
            # banners + speech.
            if target > fired:
                fire(session)
                fired = target

            if fired >= 1:
                updated[session.id] = (episode_ts, fired)

        if updated != previous:
            sidecars._write_reminders_locked(path, updated, label)


def reconcile(sessions: Iterable[Session], now: int) -> None:
    """Fire any due 🟢 idle reminders and persist the escalation progress.

    Selects sessions in :attr:`RenderGroup.FRESH` — finished, not yet
    clicked. For such a row ``last_event_ts`` *is* the Stop timestamp (no
    click has landed since, and no active-state floor applies), so it pins
    the green episode.
    """
    _reconcile(
        sessions,
        now,
        interval=core.CONFIG.notify_idle_interval_sec,
        selects=lambda s: s.group is RenderGroup.FRESH,
        episode_of=lambda s: s.last_event_ts,
        path=core.IDLE_REMINDERS_PATH,
        lock=sidecars._idle_reminders_lock,
        fire=_fire,
        label="idle-reminders",
    )


def reconcile_blocked(sessions: Iterable[Session], now: int) -> None:
    """Fire any due 🔴 blocked reminders and persist the escalation progress.

    Selects sessions that are ``waiting`` **because of a
    ``PermissionRequest``** — Claude is sitting on a tool-approval prompt and
    cannot move until the user answers.

    The ``last_event_kind`` half of that test is load-bearing, not belt and
    braces. ``hooks/settings-hooks.json`` maps *two* events onto the
    ``waiting`` state, and Claude Code fires the other one,
    ``Notification``, when the prompt has merely been idle for a while. A
    selector on the state alone would therefore re-announce "I'm blocked"
    every ten minutes at a session nobody is blocked on — and announce it as
    a *repeat* of a first notification that never happened, because
    ``notify-wait.sh`` is registered on ``PermissionRequest`` only. A repeat
    has to have the same trigger as the thing it repeats.

    The episode anchor is :attr:`Session.state_since`, the moment the row
    entered ``waiting``; ``last_event_ts`` would not do, because it advances
    while the session waits and would keep resetting the counter.

    Unlike the idle reminder this has no natural ceiling — a prompt can stand
    for hours — which is exactly why it exists: the one-shot
    ``hooks/notify-wait.sh`` banner fires on the event and never again, so a
    missed banner used to mean an agent blocked indefinitely.
    """
    _reconcile(
        sessions,
        now,
        interval=core.CONFIG.notify_blocked_interval_sec,
        selects=lambda s: (
            s.hook_state == "waiting"
            and s.last_event_kind == PERMISSION_EVENT_KIND
        ),
        episode_of=lambda s: s.state_since,
        path=core.BLOCKED_REMINDERS_PATH,
        lock=sidecars._blocked_reminders_lock,
        fire=_fire_blocked,
        label="blocked-reminders",
    )
