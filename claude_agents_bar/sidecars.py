"""Sidecar TSVs (``agent-state.*``) and JSONL transcript readers.

Two source families live here:

* The four ``~/.claude/agent-state.*`` sidecars maintained by the hook
  and the click/forget scripts — ``read_*`` to load, ``gc_*`` to prune
  stale rows under the per-file ``mkdir`` mutex.

* The ``~/.claude/projects/<slug>/<sid>.jsonl`` transcripts Claude Code
  writes for every session. We extract just enough per tick: the AI
  title (head scan), the freshest user prompt, the freshest tool_use,
  the latest usage block, and the most recent gitBranch.

A single :func:`_read_jsonl_tail` helper backs every tail-only signal
and is cached on ``(path, size, mtime_ns)`` for the lifetime of the
process — so the typical render loop opens each JSONL once for the head
and once for the tail, instead of four separate opens per session.

``ack_fresh`` lives here too because it's fundamentally a write to the
clicks sidecar that needs the same mutex; the thin CLI wrapper is in
:mod:`claude_agents_bar.actions`.
"""

from __future__ import annotations

import datetime as _dt
import functools
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import core
from .core import (
    ACTIVE_HOOK_STATES,
    HOOK_STATES,
    HookSnapshot,
    JSONL_TAIL_BYTES,
    JSONL_TITLE_SCAN_BYTES,
    JSONL_USER_TAIL_BYTES,
    RenderGroup,
    SUBAGENT_STATES,
    SubagentSnapshot,
    TranscriptMeta,
    _GIT_BRANCH_RE,
    _USAGE_BLOCK_RE,
    _clean_text,
    _content_to_title,
    _is_valid_agent_id,
    _is_valid_session_id,
    _warn,
)

# --------------------------------------------------------------------------- #
# Sidecar readers and parsers                                                  #
# --------------------------------------------------------------------------- #


def _read_iso_local(path: Path) -> _dt.datetime | None:
    """Read a single naive ISO-8601 local timestamp from ``path``.

    Returns ``None`` on missing / unreadable / unparseable / past
    timestamp. Shared between :func:`read_quiet_until` and
    :func:`read_quiet_bypass_until` — their sidecars have the same
    on-disk shape, only the semantic meaning of "future timestamp"
    differs (pause until then vs. bypass until then).
    """
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        dt = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    # Strip an accidental tzinfo so the comparison surface stays naive
    # local — bash always writes wall-clock without offsets.
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    if dt <= _dt.datetime.now():
        return None
    return dt


def read_quiet_until() -> _dt.datetime | None:
    """Return the ad-hoc pause deadline as a naive local ``datetime``,
    or ``None``.

    Reads :data:`core.QUIET_UNTIL_PATH` — one line of naive ISO-8601
    local time written by ``bin/app/quiet-pause.sh``. Missing /
    unparseable / past values read as ``None``; the menu treats that as
    "not paused" and clears the sidecar on the next *Resume* click.

    Returns a naive ``datetime`` (no tzinfo) deliberately: the bash
    writer formats wall-clock local time, and the renderer compares
    against ``datetime.datetime.now()`` which is also naive local. Same
    convention as the rest of the codebase's time arithmetic.
    """
    return _read_iso_local(core.QUIET_UNTIL_PATH)


def read_quiet_bypass_until() -> _dt.datetime | None:
    """Return the quiet-hours bypass deadline as a naive local ``datetime``,
    or ``None``.

    Inverse semantics of :func:`read_quiet_until`: when this returns a
    future timestamp, notifications fire even though the scheduled
    quiet window is active. Written by ``bin/app/quiet-bypass.sh`` with
    the end-of-current-window as deadline; auto-expires when the
    window does.
    """
    return _read_iso_local(core.QUIET_BYPASS_UNTIL_PATH)


def read_dismiss_ts() -> int:
    """Return the *Forget all sessions* cutoff, or ``0`` when unset.

    A missing, empty, or unparseable file means "no cutoff" — we'd rather
    show every live session than hide them all because of a corrupt byte.
    """
    try:
        return int(core.DISMISS_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def read_sidecar() -> dict[str, HookSnapshot]:
    """Load ``agent-state.tsv`` into a ``{session_id: HookSnapshot}`` map.

    The TSV is written by hooks under :data:`core._SIDECAR_LOCK_DIR`, but
    malformed rows can still appear (a half-written write that crashed, a
    leftover from a previous schema, etc.) so we treat every row as
    untrusted and silently skip anything that doesn't parse.
    """
    if not core.SIDECAR_PATH.exists():
        return {}
    try:
        raw = core.SIDECAR_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return _parse_sidecar(raw)


def _parse_sidecar(raw: str) -> dict[str, HookSnapshot]:
    """Decode the raw TSV text into a snapshot dict. Pure, easy to test."""
    snapshots: dict[str, HookSnapshot] = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        sid, state, ts_raw, kind, cwd = parts[:5]
        if not _is_valid_session_id(sid):
            continue
        if state not in HOOK_STATES:
            continue
        try:
            ts = int(ts_raw)
        except ValueError:
            continue
        state_since = ts
        if len(parts) >= 6:
            try:
                state_since = int(parts[5])
            except ValueError:
                pass
        snapshots[sid] = HookSnapshot(
            state=state,
            last_event_ts=ts,
            last_event_kind=kind,
            cwd=cwd,
            state_since=state_since,
        )
    return snapshots


def _live_session_ids() -> set[str]:
    """Return the set of session ids that still have a JSONL on disk.

    The transcript file is the source of truth for "this session exists" —
    the sidecar TSV is only a state cache. Code that wants to garbage-
    collect by liveness must compare against this set, not against the
    sidecar (a session may legitimately have a JSONL but no TSV row, e.g.
    one started before the hooks were installed).
    """
    live_ids: set[str] = set()
    if core.PROJECTS_DIR.exists():
        for project_dir in core.PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl in project_dir.glob("*.jsonl"):
                sid = jsonl.stem
                if not _is_valid_session_id(sid):
                    # Names with stray newlines/quotes/regex-metachars would
                    # later flow into shell args and grep regexes; refuse to
                    # surface them at all. See ``core._SESSION_ID_RE``.
                    continue
                live_ids.add(sid)
    return live_ids


def read_subagents_sidecar() -> dict[str, tuple[SubagentSnapshot, ...]]:
    """Load ``agent-state.subagents.tsv`` into ``{parent_sid: (snap, ...)}``.

    Rows are returned sorted by ``state_since`` ascending so the oldest
    subagent for a given parent comes first — matches the natural left-to-
    right submenu order and means the renderer doesn't need to re-sort.

    Same fail-open stance as :func:`read_sidecar`: a missing file returns
    ``{}``, a half-written row that doesn't parse is silently skipped.
    """
    if not core.SUBAGENTS_SIDECAR_PATH.exists():
        return {}
    try:
        raw = core.SUBAGENTS_SIDECAR_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return _parse_subagents_sidecar(raw)


def _parse_subagents_sidecar(raw: str) -> dict[str, tuple[SubagentSnapshot, ...]]:
    """Decode the raw TSV text into ``{parent_sid: (snap, ...)}``. Pure helper.

    Accepts both the 6-column legacy schema and the 7-column schema with
    ``first_event_ts``; absent column 7 is treated as ``None`` so the
    renderer can skip the runtime suffix instead of emitting a wrong
    value.
    """
    groups: dict[str, list[SubagentSnapshot]] = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        parent_sid, agent_id, agent_type, state, since_raw, ts_raw = parts[:6]
        if not _is_valid_session_id(parent_sid):
            continue
        if not _is_valid_agent_id(agent_id):
            continue
        if state not in SUBAGENT_STATES:
            continue
        try:
            ts = int(ts_raw)
            state_since = int(since_raw)
        except ValueError:
            continue
        first_event_ts: int | None = None
        if len(parts) >= 7:
            try:
                first_event_ts = int(parts[6])
            except ValueError:
                first_event_ts = None
        groups.setdefault(parent_sid, []).append(
            SubagentSnapshot(
                parent_sid=parent_sid,
                agent_id=agent_id,
                agent_type=agent_type,
                state=state,
                state_since=state_since,
                last_event_ts=ts,
                first_event_ts=first_event_ts,
            )
        )
    return {
        sid: tuple(sorted(snaps, key=lambda s: s.state_since))
        for sid, snaps in groups.items()
    }


def _subagent_lock(timeout_sec: float = 2.0):
    """Mutex shared with ``hooks/agent-state.sh`` for ``agent-state.subagents.tsv``."""
    return _mkdir_lock(core._SUBAGENTS_SIDECAR_LOCK_DIR, timeout_sec)


def _stale_subagent_keys(
    snapshots: dict[str, tuple[SubagentSnapshot, ...]],
    now: int,
) -> set[tuple[str, str]]:
    """Return ``(parent_sid, agent_id)`` rows the subagent sidecar should drop.

    A subagent row is stale when:

    * the parent's transcript no longer exists on disk (the whole session
      was deleted out of band — orphaned subagents belong with their
      parent in the bin), or
    * its ``last_event_ts`` is older than :attr:`Config.window_sec` (the
      row will never render again — same window as the main sidecar).
    """
    live_ids = _live_session_ids()
    stale: set[tuple[str, str]] = set()
    for parent_sid, snaps in snapshots.items():
        for snap in snaps:
            if parent_sid not in live_ids:
                stale.add((parent_sid, snap.agent_id))
            elif now - snap.last_event_ts > core.CONFIG.window_sec:
                stale.add((parent_sid, snap.agent_id))
    return stale


def _stale_sidecar_ids(snapshots: dict[str, HookSnapshot], now: int) -> set[str]:
    """Return session ids whose row should be removed from the sidecar.

    A row is stale when:

    * its transcript no longer exists on disk (the session was deleted out
      of band — typically by ``bin/app/delete-session.sh`` or by the user
      directly), or
    * its last event is older than :attr:`Config.window_sec` (the row will
      never be rendered again from this point on, so it's pure overhead).
    """
    live_ids = _live_session_ids()
    stale: set[str] = set()
    for sid, snap in snapshots.items():
        if sid not in live_ids:
            stale.add(sid)
        elif now - snap.last_event_ts > core.CONFIG.window_sec:
            stale.add(sid)
    return stale


# --------------------------------------------------------------------------- #
# Locks                                                                        #
# --------------------------------------------------------------------------- #


@contextmanager
def _mkdir_lock(lock_dir: Path, timeout_sec: float = 2.0) -> Iterator[None]:
    """Acquire ``lock_dir`` via atomic ``mkdir``.

    If the lock is held longer than ``timeout_sec`` we assume the previous
    holder crashed and steal it — keeps the menu from deadlocking on a
    stuck hook. Lock holders only run for microseconds, so a 2 s ceiling
    is generous in practice.
    """
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            lock_dir.mkdir()
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                try:
                    lock_dir.rmdir()
                except OSError:
                    pass
                continue
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except OSError:
            pass


def _sidecar_lock(timeout_sec: float = 2.0):
    """Mutex shared with ``hooks/agent-state.sh`` for ``agent-state.tsv``."""
    return _mkdir_lock(core._SIDECAR_LOCK_DIR, timeout_sec)


def _clicks_lock(timeout_sec: float = 2.0):
    """Mutex shared with ``bin/app/open-session.sh`` for ``agent-state.clicks``."""
    return _mkdir_lock(core._CLICKS_LOCK_DIR, timeout_sec)


def _forget_lock(timeout_sec: float = 2.0):
    """Mutex shared with ``bin/app/forget-session.sh`` for ``agent-state.forget``."""
    return _mkdir_lock(core._FORGET_LOCK_DIR, timeout_sec)


def _idle_reminders_lock(timeout_sec: float = 2.0):
    """Mutex on ``agent-state.idle-reminders``. Only the plugin tick writes
    it, but overlapping ticks could race, so the rewrite is serialised."""
    return _mkdir_lock(core._IDLE_REMINDERS_LOCK_DIR, timeout_sec)


def _usage_alerts_lock(timeout_sec: float = 2.0):
    """Mutex on ``agent-state.usage-alerts``. Only the plugin tick writes it,
    but overlapping ticks could race, so the rewrite is serialised."""
    return _mkdir_lock(core._USAGE_ALERTS_LOCK_DIR, timeout_sec)


# --------------------------------------------------------------------------- #
# Sidecar GC                                                                   #
# --------------------------------------------------------------------------- #


def _gc_two_col_sidecar(
    path: Path,
    stale: set[str],
    lock_cm,
    label: str,
) -> None:
    """Atomic ``{sid}\\t{ts}``-style sidecar prune: re-read under lock, drop
    matching ids, replace file atomically. Shared by clicks and forget — both
    are the same shape, only the path / lock / log label differ.
    """
    if not stale or not path.exists():
        return
    with lock_cm():
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        original = raw.splitlines()
        kept = [
            line
            for line in original
            if not (line.split("\t", 1)[:1] and line.split("\t", 1)[0] in stale)
        ]
        if len(kept) == len(original):
            return
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        try:
            tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            _warn(f"{label} gc failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass


def gc_subagents(stale: set[tuple[str, str]]) -> None:
    """Drop the given ``(parent_sid, agent_id)`` rows from the subagent sidecar.

    Same atomic re-read / re-write under mutex as :func:`gc_sidecar` but
    keyed on the first two columns instead of just the first. Cheap when
    there's nothing to drop.
    """
    path = core.SUBAGENTS_SIDECAR_PATH
    if not stale or not path.exists():
        return
    with _subagent_lock():
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        original = raw.splitlines()
        kept: list[str] = []
        for line in original:
            parts = line.split("\t", 2)
            if len(parts) < 2:
                kept.append(line)
                continue
            if (parts[0], parts[1]) in stale:
                continue
            kept.append(line)
        if len(kept) == len(original):
            return
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        try:
            tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            _warn(f"subagent sidecar gc failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass


def gc_sidecar(stale: set[str]) -> None:
    """Drop the given session ids from the sidecar, atomically.

    Takes the same mutex that ``hooks/agent-state.sh`` uses, so concurrent
    hook writes can't race with our rewrite. Cheap when there's nothing to
    drop: returns immediately without touching the filesystem.

    The TSV has 5–6 columns; we still only inspect column 0 (the session id),
    so the same generic helper used for the 2-column clicks/forget files
    applies here.
    """
    _gc_two_col_sidecar(core.SIDECAR_PATH, stale, _sidecar_lock, "sidecar")


def gc_forget(stale: set[str]) -> None:
    """Drop the given session ids from the forget sidecar, atomically.

    Mirrors :func:`gc_clicks`. We only prune rows whose transcript is gone —
    a forgotten row whose JSONL still exists must stay, otherwise the
    sessions would silently re-surface.
    """
    _gc_two_col_sidecar(core.FORGET_PATH, stale, _forget_lock, "forget")


def gc_clicks(stale: set[str]) -> None:
    """Drop the given session ids from the click sidecar, atomically.

    Mirrors :func:`gc_sidecar` but on a simpler two-column file. Cheap
    when there's nothing to drop.
    """
    _gc_two_col_sidecar(core.CLICKS_PATH, stale, _clicks_lock, "clicks")


def read_clicks() -> dict[str, int]:
    """Load the click sidecar into a ``{session_id: click_ts}`` map.

    Only the latest click per session is kept on disk (the recorder
    rewrites the row), so this is a straight dict load. Any unparseable
    row is dropped silently — same fail-open stance as :func:`read_sidecar`.
    """
    if not core.CLICKS_PATH.exists():
        return {}
    try:
        raw = core.CLICKS_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return _parse_clicks(raw)


def _parse_clicks(raw: str) -> dict[str, int]:
    """Decode click TSV text into ``{sid: ts}``. Pure helper."""
    out: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        sid, ts_raw = parts[0], parts[1]
        try:
            ts = int(ts_raw)
        except ValueError:
            continue
        out[sid] = ts
    return out


def read_forget() -> dict[str, int]:
    """Load the forget sidecar into a ``{session_id: forget_ts}`` map.

    Same two-column TSV shape as the clicks sidecar. Unparseable rows are
    dropped silently — we'd rather show a session that was meant to be
    forgotten than hide every row because of one corrupt byte.
    """
    if not core.FORGET_PATH.exists():
        return {}
    try:
        raw = core.FORGET_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return _parse_clicks(raw)


def read_idle_reminders() -> dict[str, tuple[int, int]]:
    """Load the idle-reminders sidecar into ``{sid: (stop_ts, fired_count)}``.

    Three-column TSV (``sid\tstop_ts\tfired_count``). A row missing a
    column or with non-integer numbers is dropped silently — same
    fail-open stance as :func:`read_clicks`; the worst case is one extra
    reminder, never a crashed menu.
    """
    if not core.IDLE_REMINDERS_PATH.exists():
        return {}
    try:
        raw = core.IDLE_REMINDERS_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, tuple[int, int]] = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        sid = parts[0]
        try:
            stop_ts = int(parts[1])
            fired = int(parts[2])
        except ValueError:
            continue
        out[sid] = (stop_ts, fired)
    return out


def write_idle_reminders(state: dict[str, tuple[int, int]]) -> None:
    """Atomically replace the idle-reminders sidecar with ``state``.

    Full rewrite (the map is tiny — one row per pending 🟢 session) under
    :func:`_idle_reminders_lock`, via a tmp file + ``replace`` like
    :func:`ack_fresh`. An empty ``state`` removes the file so a quiet
    machine leaves no sidecar behind. Best-effort: any OSError is logged
    and swallowed so a write failure never takes the menu down.
    """
    if not state:
        try:
            core.IDLE_REMINDERS_PATH.unlink()
        except OSError:
            pass
        return
    lines = [f"{sid}\t{stop_ts}\t{fired}" for sid, (stop_ts, fired) in state.items()]
    with _idle_reminders_lock():
        tmp = core.IDLE_REMINDERS_PATH.with_suffix(
            core.IDLE_REMINDERS_PATH.suffix + f".{os.getpid()}.tmp"
        )
        try:
            core.IDLE_REMINDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            tmp.replace(core.IDLE_REMINDERS_PATH)
        except OSError as exc:
            _warn(f"idle-reminders write failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass


def read_usage() -> core.Usage | None:
    """Load the subscription usage snapshot written by ``usage-sensor.sh``.

    Five-column TSV
    (``record_ts\tfive_used\tfive_resets_at\tseven_used\tseven_target``),
    one row. Returns ``None`` when the file is absent (API-key auth, or the
    sensor isn't wired up), unreadable, malformed, or carries a non-numeric
    field — same fail-open stance as the other readers: a missing or broken
    snapshot just hides the usage line and skips the alerts, never crashes
    the menu. ``five_resets_at`` is validated numeric but kept as the raw
    string (it doubles as the usage-alert window key).
    """
    if not core.USAGE_PATH.exists():
        return None
    try:
        raw = core.USAGE_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parts = raw.strip().split("\t")
    if len(parts) < 5:
        return None
    try:
        record_ts = int(parts[0])
        five_used = int(parts[1])
        int(parts[2])  # validate five_resets_at is numeric; keep the string
        seven_used = int(parts[3])
        float(parts[4])  # validate seven_target is numeric; keep the string
    except ValueError:
        return None
    return core.Usage(
        record_ts=record_ts,
        five_used=five_used,
        five_resets_at=parts[2],
        seven_used=seven_used,
        seven_target=parts[4],
    )


def read_usage_alerts() -> tuple[str, int] | None:
    """Load the usage-alert progress as ``(window_key, max_threshold_fired)``.

    Single-row two-column TSV (``five_resets_at\tmax_threshold_fired``).
    Returns ``None`` when absent / unreadable / malformed — meaning no
    threshold has fired for any window yet. ``window_key`` is the 5-hour
    window's ``resets_at`` string; :func:`usage_alerts.reconcile` resets the
    progress when it no longer matches the current snapshot's window.
    """
    if not core.USAGE_ALERTS_PATH.exists():
        return None
    try:
        raw = core.USAGE_ALERTS_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parts = raw.strip().split("\t")
    if len(parts) < 2 or not parts[0]:
        return None
    try:
        max_fired = int(parts[1])
    except ValueError:
        return None
    return (parts[0], max_fired)


def write_usage_alerts(state: tuple[str, int] | None) -> None:
    """Atomically replace the usage-alert progress sidecar with ``state``.

    ``state`` is ``(window_key, max_threshold_fired)`` or ``None`` to clear
    it (removes the file, like an empty :func:`write_idle_reminders`). Full
    rewrite under :func:`_usage_alerts_lock` via tmp + ``replace``.
    Best-effort: any OSError is logged and swallowed so a write failure never
    takes the menu down.
    """
    if state is None:
        try:
            core.USAGE_ALERTS_PATH.unlink()
        except OSError:
            pass
        return
    window_key, max_fired = state
    with _usage_alerts_lock():
        tmp = core.USAGE_ALERTS_PATH.with_suffix(
            core.USAGE_ALERTS_PATH.suffix + f".{os.getpid()}.tmp"
        )
        try:
            core.USAGE_ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(f"{window_key}\t{max_fired}\n", encoding="utf-8")
            tmp.replace(core.USAGE_ALERTS_PATH)
        except OSError as exc:
            _warn(f"usage-alerts write failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass


def ack_fresh(now: int) -> int:
    """Bulk-promote every currently-FRESH session to ACKNOWLEDGED.

    The set of FRESH sessions is taken from :func:`collect_sessions` so
    the action targets exactly what the menu shows in 🟢 — including
    sessions that have no sidecar row yet (they only exist as a JSONL
    on disk with a recent mtime). For each one we record a synthetic
    click at ``now``; the next plugin tick picks up those clicks and
    reclassifies the rows.

    Sessions that are already acknowledged, stale, or active are left
    alone — the action exists to clear the unread 🟢 badge, not to
    refresh every timer on the menu. Returns the number of rows
    promoted (0 when nothing matched).
    """
    # Local import to break the package-level cycle: ``render`` imports from
    # ``sidecars`` (read_*, gc_*, ack_fresh helpers), and ``collect_sessions``
    # in render needs to call back into sidecars to enumerate JSONLs. Importing
    # at call time means each side sees the other fully initialised.
    from .render import collect_sessions

    fresh_sids = [
        s.id for s in collect_sessions(now) if s.group is RenderGroup.FRESH
    ]
    if not fresh_sids:
        return 0

    with _clicks_lock():
        # Re-read inside the lock so a concurrent open-session.sh write
        # doesn't get clobbered by our merge.
        try:
            raw = (
                core.CLICKS_PATH.read_text(encoding="utf-8", errors="replace")
                if core.CLICKS_PATH.exists()
                else ""
            )
        except OSError:
            return 0
        merged = _parse_clicks(raw)
        for sid in fresh_sids:
            merged[sid] = now
        lines = [f"{sid}\t{ts}" for sid, ts in merged.items()]
        tmp = core.CLICKS_PATH.with_suffix(core.CLICKS_PATH.suffix + f".{os.getpid()}.tmp")
        try:
            core.CLICKS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            tmp.replace(core.CLICKS_PATH)
        except OSError as exc:
            _warn(f"ack-fresh write failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass
            return 0

    return len(fresh_sids)


# --------------------------------------------------------------------------- #
# JSONL transcript readers                                                     #
# --------------------------------------------------------------------------- #

#: Single tail window shared by every tail-only signal extractor below. Set to
#: the largest window any caller wanted (the user-prompt scan) so a single
#: read covers all three. Widening the window for the tool-use / usage / git
#: readers only improves recall — they still walk the buffer for the *last*
#: match.
_JSONL_TAIL_READ_BYTES = max(JSONL_USER_TAIL_BYTES, JSONL_TAIL_BYTES)


@functools.lru_cache(maxsize=512)
def _read_jsonl_tail_cached(path_str: str, size: int, mtime_ns: int) -> bytes:
    """Backing store for :func:`_read_jsonl_tail`. Keyed on the filesystem
    identity so a file that changes between calls invalidates naturally.
    """
    try:
        with Path(path_str).open("rb") as f:
            f.seek(max(0, size - _JSONL_TAIL_READ_BYTES))
            return f.read()
    except OSError:
        return b""


def _read_jsonl_tail(path: Path) -> bytes:
    """Last ~128 KB of ``path``, cached for the process by ``(path, size, mtime)``.

    The render path produces 3–4 tail-only signals per session (last user
    prompt, last tool_use, last usage block, fallback gitBranch); without this
    cache each one would re-stat and re-read the same trailing bytes.
    Returns ``b""`` for empty / missing / unreadable files so callers don't
    need their own try/except.
    """
    try:
        st = path.stat()
    except OSError:
        return b""
    if st.st_size == 0:
        return b""
    return _read_jsonl_tail_cached(str(path), st.st_size, st.st_mtime_ns)


def read_transcript_meta(jsonl_path: Path) -> TranscriptMeta:
    """Extract title and cwd from a session transcript.

    Title priority: session_title (parsed from response marker) → ai-title
    (Claude Code event) → latest user message → raw_title (first message).
    ``session_title`` is only parsed when
    :attr:`core.Config.use_session_titles_for_menubar` is on; off (the
    default) skips the per-tick parse and the menu shows ``ai-title`` — the
    same label VSCode displays. The spoken notifications parse the marker
    independently in Bash, so they're unaffected by this knob.

    The ``cwd`` returned here is the session's *initial* cwd; callers should
    prefer :attr:`HookSnapshot.cwd` when available, since sessions can change
    cwd mid-flight (subagents).
    """
    ai_title = ""
    raw_title = ""
    cwd = ""
    entrypoint = ""
    try:
        with jsonl_path.open("rb") as f:
            consumed = 0
            for raw in f:
                consumed += len(raw)
                if not ai_title and b'"type":"ai-title"' in raw:
                    if (title := _parse_ai_title(raw)) is not None:
                        ai_title = title
                    continue
                if cwd and raw_title and entrypoint:
                    if ai_title or consumed >= JSONL_TITLE_SCAN_BYTES:
                        break
                    continue
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not cwd and isinstance(event.get("cwd"), str):
                    cwd = event["cwd"]
                if not entrypoint and isinstance(event.get("entrypoint"), str):
                    entrypoint = event["entrypoint"]
                if not raw_title and event.get("type") == "user":
                    content = event.get("message", {}).get("content")
                    raw_title = _content_to_title(content)
    except OSError:
        pass
    if not ai_title:
        # The first ``ai-title`` sat past the head-scan window (bloated
        # early events). Claude Code re-emits it every turn, so the tail
        # almost always still carries a fresh one — far steadier than
        # letting the row fall through to the sliding user-prompt sources,
        # which visibly flap as tool output pushes the latest prompt out of
        # the tail window.
        ai_title = _latest_tail_ai_title(jsonl_path)
    # Opt-in (default off): the menu shows ai-title unless the user asks for
    # the marker name. When off we skip the parse entirely — keeps the tick
    # cheap and the title consistent with what VSCode shows.
    session_title = (
        _latest_session_title_from_response(jsonl_path)
        if core.CONFIG.use_session_titles_for_menubar
        else ""
    )
    return TranscriptMeta(
        session_title=session_title.strip(),
        ai_title=ai_title.strip(),
        raw_title=raw_title,
        cwd=cwd,
        entrypoint=entrypoint,
    )


def _parse_ai_title(raw: bytes) -> str | None:
    """Decode a single ``ai-title`` JSONL line; return ``None`` if unparseable."""
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    ai_title = event.get("aiTitle")
    return ai_title if isinstance(ai_title, str) else None


def _latest_tail_ai_title(jsonl_path: Path) -> str:
    """Return the freshest ``ai-title`` in the JSONL tail, or ``""``.

    Backs the fallback in :func:`read_transcript_meta` for sessions whose
    first ``ai-title`` was pushed past :data:`core.JSONL_TITLE_SCAN_BYTES`
    by bloated early events (most commonly a first message with pasted
    images). Claude Code re-emits ``ai-title`` on essentially every turn,
    so the already-cached tail buffer (see :func:`_read_jsonl_tail`)
    practically always carries a recent one. Reads the *last* match — the
    current topic, matching what the VSCode sidebar shows.
    """
    data = _read_jsonl_tail(jsonl_path)
    if not data:
        return ""
    last = ""
    for raw in data.splitlines():
        if b'"type":"ai-title"' not in raw:
            continue
        title = _parse_ai_title(raw)
        if title is not None and title.strip():
            last = title
    return last


#: Divider between the session name and the spoken summary inside a marker
#: line: a lone hyphen padded with spaces (``-- Name - Summary``). Distinct
#: from ``notify_summary_marker`` (the line *prefix*, ``"-- "`` by default),
#: which detects the line; this splits its two fields. Mirrored byte-for-byte
#: by the hooks (``hooks/_notify-common.sh``) so the menu name, the spoken
#: Stop summary, and the awaiting name+summary all parse identically.
_MARKER_FIELD_DIVIDER = " - "


def _parse_marker_line(line: str, marker: str) -> tuple[str, str] | None:
    """Split one summary-marker line into ``(name, summary)``.

    ``marker`` is ``notify_summary_marker`` (the line prefix). Leading and
    trailing markdown emphasis (``*``/``_`` runs) is stripped first, so
    ``*-- …*``, ``_-- …_`` and a bare ``-- …`` all parse. The line must then
    start with the marker; the remainder is split once on the first
    :data:`_MARKER_FIELD_DIVIDER` into the session name and the summary. A
    remainder without that divider is the legacy single-field form — summary
    only, empty name. Returns ``None`` when ``line`` isn't a marker line.
    """
    if not marker:
        return None
    stripped = line.strip().strip("*_").strip()
    if not stripped.startswith(marker):
        return None
    rest = stripped[len(marker):].strip()
    name, divider, summary = rest.partition(_MARKER_FIELD_DIVIDER)
    if not divider:
        return "", rest
    return name.strip(), summary.strip()


def _session_name_from_reply(text: str, marker: str) -> str:
    """Session name from a reply's *closing* marker line, or ``""``.

    Only the last non-blank line is inspected — the authoring convention is
    "the closing line is the marker" — so an earlier ``-- ``-ish line (a list
    item, a code fence) can't false-match. Single-field replies (no name
    field) yield ``""`` so the menu title falls through to ``ai_title``.
    """
    line = ""
    for candidate in text.splitlines():
        if candidate.strip():
            line = candidate
    parsed = _parse_marker_line(line, marker)
    return parsed[0] if parsed else ""


def _latest_session_title_from_response(jsonl_path: Path) -> str:
    """Session name parsed from the latest assistant reply's marker line.

    Gated on ``notify_summary_marker``: when the marker is disabled (``null``
    / ``""``) the menu never pays the per-tick parse and this returns ``""``,
    so the title falls through to ``ai_title``. Otherwise it scans the
    already-cached JSONL tail (no extra disk read; a cheap byte prefilter
    skips non-assistant lines before ``json.loads``) and keeps the *last*
    reply that carried a name, so the title tracks the current turn rather
    than a stale earlier one.
    """
    marker = core.CONFIG.notify_summary_marker
    if not marker:
        return ""
    data = _read_jsonl_tail(jsonl_path)
    if not data:
        return ""
    name = ""
    for raw in data.splitlines():
        if b'"type":"assistant"' not in raw:
            continue
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if event.get("type") != "assistant":
            continue
        content = event.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if not texts:
            continue
        candidate = _session_name_from_reply("\n".join(texts), marker)
        if candidate:
            name = candidate
    return name


def current_git_branch(cwd: str) -> str:
    """Return the branch currently checked out in ``cwd``, or empty string.

    Reads ``.git/HEAD`` directly — no ``git`` subprocess, no PATH dependency.
    Transparently follows the worktree indirection where ``.git`` is a file
    of the form ``gitdir: <path-to-real-gitdir>``.
    """
    if not cwd:
        return ""
    try:
        marker = Path(cwd) / ".git"
        if not marker.exists():
            return ""
        if marker.is_dir():
            head_file = marker / "HEAD"
        else:
            indirection = marker.read_text(encoding="utf-8", errors="replace").strip()
            if not indirection.startswith("gitdir:"):
                return ""
            head_file = Path(indirection.split(":", 1)[1].strip()) / "HEAD"
        head = head_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    ref_prefix = "ref: refs/heads/"
    if head.startswith(ref_prefix):
        return head[len(ref_prefix):]
    return head[:7]  # detached HEAD → short SHA


def is_worktree_checkout(cwd: str) -> bool:
    """Return ``True`` when ``cwd`` is a git *worktree* checkout.

    A linked worktree stores ``.git`` as a *file* whose content begins with
    ``gitdir: <path-to-real-gitdir>`` rather than as the usual ``.git``
    directory. We only test that marker shape — the actual gitdir target
    isn't followed here (callers that need the branch use
    :func:`current_git_branch`). Fail-soft: empty ``cwd`` or any ``OSError``
    yields ``False``.
    """
    if not cwd:
        return False
    try:
        marker = Path(cwd) / ".git"
        if not marker.is_file():
            return False
        head = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return head.lstrip().startswith("gitdir:")


def fallback_git_branch_from_jsonl(jsonl_path: Path) -> str:
    """Latest ``gitBranch`` seen in the JSONL tail, or empty string.

    Used when the session's cwd is no longer a git repo (moved, deleted,
    unmounted) so we still surface some branch context to the user.
    """
    data = _read_jsonl_tail(jsonl_path)
    if not data:
        return ""
    matches = _GIT_BRANCH_RE.findall(data)
    if not matches:
        return ""
    return matches[-1].decode("utf-8", errors="replace")


#: System-injected ``type:"user"`` events that aren't really the user
#: typing — Claude Code stores them in the transcript for continuity
#: but they shouldn't surface as the row title.
_USER_SYS_PREFIXES = (
    "<system-reminder",
    "<command-name",
    "<command-message",
    "<command-stdout",
    "<command-stderr",
    "<local-command-stdout",
    "<ide_opened_file",
    "<ide_selection",
    "[Request interrupted",
)


def _user_prompt_text(content: object) -> str:
    """Return the raw prompt text for a ``type:"user"`` event, or ``""``.

    Filters out the noise that Claude Code stores as user-events alongside
    real prompts: ``tool_result`` payloads (continuity for assistant tool
    calls), IDE/harness wrappers (``<system-reminder>``, ``<ide_*>``,
    ``<command-*>``) and the synthetic ``[Request interrupted …]`` line
    Claude Code injects when a tool call is cancelled. What's left is
    what the user actually typed — exactly what we want behind the
    aiTitle fallback.
    """
    if isinstance(content, str):
        text = content
    elif isinstance(content, list) and content:
        first = content[0] if isinstance(content[0], dict) else {}
        if first.get("type") != "text":
            return ""
        text = first.get("text", "")
    else:
        return ""
    if not isinstance(text, str):
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith(_USER_SYS_PREFIXES):
        return ""
    return _clean_text(stripped)


def last_user_message_preview(jsonl_path: Path) -> str:
    """Return a one-line preview of the freshest user prompt, or ``""``.

    Walks the shared tail buffer (see :func:`_read_jsonl_tail`) and keeps
    the last ``"type":"user"`` event whose content survives
    :func:`_user_prompt_text`. The window can occasionally miss the very
    last prompt if a single turn dumped more than ~128 KB of tool output
    afterwards; in that case the row falls back to whatever earlier title
    source the transcript yielded.

    Used purely as the aiTitle fallback in :attr:`TranscriptMeta.display_title`.
    """
    data = _read_jsonl_tail(jsonl_path)
    if not data:
        return ""
    last = ""
    for raw in data.splitlines():
        if b'"type":"user"' not in raw:
            continue
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if event.get("type") != "user":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        text = _user_prompt_text(message.get("content"))
        if text:
            last = text
    return last


#: Short, recognisable "preview" input key per tool. Picks the first input
#: argument that's likely to mean something at a glance — file path for
#: editors, command for shells, query/pattern for search tools. Tools
#: missing from the map render with just the tool name in the tooltip.
_TOOL_INPUT_PREVIEW_KEY = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
    "Grep": "pattern",
    "Glob": "pattern",
    "WebFetch": "url",
    "WebSearch": "query",
    "ToolSearch": "query",
    "Task": "description",
    "Agent": "description",
    "AskUserQuestion": "header",
    "Skill": "skill",
    "ScheduleWakeup": "reason",
    "Monitor": "command",
}


def _summarise_tool_use(name: str, input_obj: object) -> str:
    """Format one ``tool_use`` chunk as a single tooltip line.

    Used as the main row's NSMenuItem tooltip — surfaces *what Claude
    is doing right now* (``Read: main.py``, ``Bash: pytest …``) on
    hover. No truncation: tooltips have plenty of room and the full
    command/path is the whole point. Whitespace is collapsed so the
    tooltip stays a single readable line; NSMenuItem.toolTip respects
    ``\\n`` but multi-line tooltips crowd the menu and the preview is
    meant to be glanceable. Returns ``""`` for a chunk with no
    rendered tool name (caller skips rendering).
    """
    if not isinstance(name, str) or not name:
        return ""
    if not isinstance(input_obj, dict):
        return name
    preview_key = _TOOL_INPUT_PREVIEW_KEY.get(name)
    candidate = input_obj.get(preview_key) if preview_key else None
    if not isinstance(candidate, str) or not candidate.strip():
        # Generic fallback: the first string value the tool was called
        # with — typically the most meaningful arg for tools not in the
        # map above (and a sensible default for new tools we haven't
        # explicitly modelled).
        for value in input_obj.values():
            if isinstance(value, str) and value.strip():
                candidate = value
                break
    if not isinstance(candidate, str) or not candidate.strip():
        return name
    preview = " ".join(candidate.split())
    return f"{name}: {preview}"


def last_tool_use_summary(jsonl_path: Path) -> str:
    """Return a one-line summary of the freshest assistant ``tool_use``, or ``""``.

    Walks the shared tail buffer in order, keeping the last
    ``"type":"tool_use"`` chunk that yields a parseable name. Surfaced as
    the main row's NSMenuItem tooltip so a hover answers *what is Claude
    doing right now?* without expanding the submenu.
    """
    data = _read_jsonl_tail(jsonl_path)
    if not data:
        return ""
    last_summary = ""
    for raw in data.splitlines():
        if b'"type":"tool_use"' not in raw:
            continue
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for chunk in content:
            if not isinstance(chunk, dict):
                continue
            if chunk.get("type") != "tool_use":
                continue
            summary = _summarise_tool_use(chunk.get("name", ""), chunk.get("input"))
            if summary:
                last_summary = summary
    return last_summary


def read_subagent_meta(meta_path: Path) -> dict | None:
    """Return the parsed ``agent-<id>.meta.json`` for a subagent, or ``None``.

    Claude Code writes a tiny sibling JSON next to every subagent
    transcript carrying the ``Task`` tool's ``description`` (the short
    human-readable summary the parent passed) and ``agentType``. Both
    are pre-resolved by the runtime, so reading the meta file is much
    cheaper than walking the subagent's JSONL for the first user
    message. Fail-soft like every other reader in this module.
    """
    try:
        raw = meta_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def count_tool_uses(jsonl_path: Path) -> int:
    """Return the number of ``tool_use`` chunks across the whole transcript.

    Subagent transcripts are small (typically a few KB — one Task does
    one job and returns), so a full-file scan is cheap and gives us
    correct totals rather than the tail-bound approximation
    :func:`last_tool_use_summary` settles for. Used only for the
    subagent rollup; the parent's render path stays tail-only.

    Fails soft to ``0`` on missing / unreadable files so the menu still
    renders something.
    """
    count = 0
    try:
        with jsonl_path.open("rb") as f:
            for raw in f:
                # Cheap pre-filter: skip the JSON parse entirely on lines
                # that can't possibly carry a tool_use. The string match
                # is a strict subset of valid tool_use lines, so any line
                # missing it has zero of them.
                if b'"type":"tool_use"' not in raw:
                    continue
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for chunk in content:
                    if isinstance(chunk, dict) and chunk.get("type") == "tool_use":
                        count += 1
    except OSError:
        return 0
    return count


def last_session_model(jsonl_path: Path) -> str | None:
    """Return the model from the most recent ``"model":"..."`` match in the
    tail, or ``None`` when no match exists.

    Claude Code writes the model alongside every assistant event's usage
    block, so the same tail buffer that powers :func:`last_usage_tokens`
    carries this signal too. We take the *last* match (mixed-model
    sessions — user switched mid-stream via ``/model`` — should answer
    "what am I jumping into", not "where did the work happen").

    ``None`` on empty / unreadable file, or on transcripts whose tail
    doesn't contain a parseable ``"model":"..."`` (older sessions or a
    session that only has the user's first prompt). Callers degrade by
    omitting the model row and falling the badge through to ⓜ.
    """
    data = _read_jsonl_tail(jsonl_path)
    if not data:
        return None
    matches = list(core._MODEL_RE.finditer(data))
    if not matches:
        return None
    return matches[-1].group(1).decode("utf-8", errors="replace")


def last_usage_tokens(jsonl_path: Path) -> int | None:
    """Return the live context size from the freshest ``usage`` block, or ``None``.

    Walks the shared tail buffer for the last ``"usage":{…}`` match. The
    returned value is ``input_tokens + cache_creation_input_tokens +
    cache_read_input_tokens`` — the same sum Claude Code uses to gauge
    "how full is the window right now". Cache reads dominate this; that
    is expected, and is why the result *can't* meaningfully be aggregated
    across all events (cache contents repeat from one turn to the next).

    ``None`` is returned for empty files, unreadable files, or transcripts
    whose tail doesn't yet contain a parseable usage block (a session that
    only has the user's first prompt, no assistant reply). Callers should
    omit the indicator row in that case rather than rendering ``0k``.
    """
    data = _read_jsonl_tail(jsonl_path)
    if not data:
        return None
    matches = _USAGE_BLOCK_RE.findall(data)
    if not matches:
        return None
    inp, cache_creation, cache_read = (int(x) for x in matches[-1])
    return inp + cache_creation + cache_read
