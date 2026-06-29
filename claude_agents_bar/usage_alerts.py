"""Subscription usage alerts — warn as the 5-hour limit window fills up.

The Claude.ai subscription meters a rolling 5-hour window. Claude Code
exposes its ``used_percentage`` / ``resets_at`` only on the statusLine
stdin — never in a transcript or a hook payload — so the bundled
``hooks/usage-sensor.sh`` (a statusLine wrapper) captures it into the
``agent-state.usage`` sidecar. This module reads that snapshot on the
SwiftBar tick and fires a one-shot notification each time the window first
crosses an escalating threshold.

Thresholds: 50/60/70/80/90 % each get a "you've hit N%" alert (template A),
and 95 % gets a distinct "almost exhausted — only a refresh restores it"
alert (template B). Each threshold fires once per window; a new ``resets_at``
(the window rolled over) resets the progress so the next window alerts from
50 % again.

The usage is *account-wide*, not per-session — so unlike idle reminders the
state here is a single ``(window_key, max_threshold_fired)`` pair, not a
per-session map. Like idle reminders, the work on the tick is deliberately
cheap (read a tiny sidecar, compare ints, ``Popen`` a detached script); all
the banner/speech work happens off the tick inside ``notify-usage.sh``.

A multi-threshold jump between ticks (e.g. 48 % → 72 %) collapses into a
**single** notification at the actual current percentage, mirroring the
idle-reminder catch-up — one nudge, not a burst of banners and queued speech.
"""

from __future__ import annotations

import subprocess

from . import core, sidecars
from .core import _warn

#: used-% thresholds that each fire a "template A" alert, once per window.
_THRESHOLDS = (50, 60, 70, 80, 90)
#: the final "template B" alert — almost exhausted, only a refresh restores it.
_CRITICAL = 95


def _fire(pct: int, kind: str, reset_secs: int) -> None:
    """Spawn a detached ``notify-usage.sh`` for one usage alert.

    ``start_new_session=True`` severs the child from SwiftBar so it outlives
    our tick; stdio goes to ``/dev/null`` — same detachment contract as
    :mod:`idle_reminders`. The alert is account-wide (no session), so we pass
    the current percentage, the template kind (``"A"`` / ``"B"``), and the
    seconds until the 5-hour window resets (the 70 %+ and critical phrases quote
    it); the script does its own config / quiet-hours gating. Invoked via
    ``/bin/bash <path>`` so it doesn't depend on the executable bit surviving
    distribution.
    """
    script = core.PLUGIN_DIR / "hooks" / "notify-usage.sh"
    try:
        subprocess.Popen(
            ["/bin/bash", str(script), str(pct), kind, str(reset_secs)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"usage_alerts: notify spawn failed: {exc}")


def reconcile(now: int) -> None:
    """Fire any due usage alert and persist the escalation progress.

    Reads the snapshot ``usage-sensor.sh`` wrote; does nothing when the
    feature is off, no snapshot exists (API-key auth / no sensor), or the
    window has already expired (``now >= resets_at`` — stale data the sensor
    hasn't refreshed since Claude Code went idle).

    Progress is a single ``(window_key, max_threshold_fired)`` pair keyed by
    the window's ``resets_at``: a different key means the window rolled over,
    so the counter resets to 0 and the fresh window alerts from 50 % again.
    Only the highest newly-crossed threshold fires (collapsing a
    multi-threshold catch-up into one alert), and the notification carries the
    *actual* current percentage, not the threshold value.
    """
    if not core.usage_monitor_enabled() or not core.CONFIG.notify_on_usage:
        # Master switch off (whole feature dark) or alerts sub-flag off. Leave
        # any existing sidecar alone — cheap, and it'll be overwritten the
        # moment the user re-enables the feature.
        return

    usage = sidecars.read_usage()
    if usage is None:
        return  # API-key auth, or the sensor isn't wired up.
    if now >= int(usage.five_resets_at):
        return  # Window expired; the snapshot is stale, don't alert on it.

    pct = usage.five_used
    window = usage.five_resets_at

    prev = sidecars.read_usage_alerts()
    max_fired = prev[1] if (prev is not None and prev[0] == window) else 0

    crossed = [t for t in (*_THRESHOLDS, _CRITICAL) if pct >= t and t > max_fired]
    if not crossed:
        # Nothing new to announce. Still persist the window rollover (so a new
        # window starts from a 0 counter) — but only when the stored state has
        # actually changed, to avoid a needless write every tick.
        if prev != (window, max_fired):
            sidecars.write_usage_alerts((window, max_fired))
        return

    top = max(crossed)  # collapse a multi-threshold catch-up into one alert.
    reset_secs = max(0, int(usage.five_resets_at) - now)
    _fire(pct, "B" if top >= _CRITICAL else "A", reset_secs)
    sidecars.write_usage_alerts((window, top))
