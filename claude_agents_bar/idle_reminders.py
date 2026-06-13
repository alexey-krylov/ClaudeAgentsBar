"""Idle-session reminders — re-nudge the user about 🟢 sessions left unread.

Stop / PermissionRequest notifications fire once, on the Claude Code hook
event. There is no event that fires "20 minutes after the session
finished", and the project runs no daemon — so a *time-based* reminder
has to ride the only periodic heartbeat there is: the SwiftBar tick, which
re-runs the plugin every 5 s whether or not the menu is open.

:func:`reconcile` is that hook. Called once per tick from
``__init__.main`` (right after :func:`keep_awake.reconcile`), it walks the
already-built session list, finds the 🟢 *fresh* (finished, not yet
clicked) ones that have crossed their next escalation threshold, and fires
``hooks/notify-idle.sh`` for each. Progress is persisted in the
``agent-state.idle-reminders`` sidecar so a reminder isn't re-sent every
tick.

The schedule doubles: reminder *k* (k = 1, 2, …) is due once
``now - stop_ts >= interval * 2**(k-1)``. Because we only ever consider
🟢 FRESH sessions, the number of reminders is naturally bounded by how
long *fresh* lasts (``Config.fresh_sec``, default 60 min) — a click or the
fresh→ack auto-promotion ends the schedule. With the default 20-min
interval and 60-min fresh window that's two nudges (at 20 and 40 min).

Cost discipline (the tick is the hot path): this module does only cheap
work — read a tiny sidecar, compare timestamps, and ``Popen`` a detached
shell script. All transcript parsing (the session name + summary spoken in
the reminder) happens inside ``notify-idle.sh``, off the tick.
"""

from __future__ import annotations

import subprocess
from typing import Iterable

from . import core, sidecars
from .core import RenderGroup, Session, _warn


def _fire(session: Session) -> None:
    """Spawn a detached ``notify-idle.sh`` for one session.

    ``start_new_session=True`` severs the child from SwiftBar so it
    outlives our tick, and stdio is wired to ``/dev/null`` — mirrors the
    detachment contract in :mod:`keep_awake`. The script does its own
    config / quiet-hours gating; we only pass the session id and cwd
    (cwd lets the banner click raise the right editor window). Invoked via
    ``/bin/bash <path>`` so it doesn't depend on the script's executable
    bit surviving distribution.
    """
    script = core.PLUGIN_DIR / "hooks" / "notify-idle.sh"
    try:
        subprocess.Popen(
            ["/bin/bash", str(script), session.id, session.cwd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"idle_reminders: notify spawn failed: {exc}")


def reconcile(sessions: Iterable[Session], now: int) -> None:
    """Fire any due idle reminders and persist the escalation progress.

    Rebuilds the sidecar state from scratch each tick, keeping a row only
    for 🟢 FRESH sessions that have actually been nudged (``fired >= 1``).
    Sessions that have left FRESH (clicked, promoted, or gone) simply drop
    out of the rebuilt map, so the write prunes them — no separate GC pass.

    A new ``stop_ts`` for a session (it finished a fresh turn) resets its
    counter, restarting the schedule. The while-loop fires every threshold
    crossed since the last tick, which matters only if the machine was
    asleep across one — normally a single reminder comes due at a time.
    """
    interval = core.CONFIG.notify_idle_interval_sec
    if interval <= 0:
        # Feature off. Leave any existing sidecar alone — cheap, and it'll
        # be overwritten the moment the user re-enables the feature.
        return

    previous = sidecars.read_idle_reminders()
    updated: dict[str, tuple[int, int]] = {}

    for session in sessions:
        if session.group is not RenderGroup.FRESH:
            continue
        # For a FRESH row last_event_ts is the Stop timestamp (no click has
        # landed since, and no active-state floor applies) — i.e. when the
        # green episode began.
        stop_ts = session.last_event_ts
        prev = previous.get(session.id)
        fired = prev[1] if prev is not None and prev[0] == stop_ts else 0

        elapsed = now - stop_ts
        while elapsed >= interval * (2 ** fired):
            _fire(session)
            fired += 1

        if fired >= 1:
            updated[session.id] = (stop_ts, fired)

    if updated != previous:
        sidecars.write_idle_reminders(updated)
