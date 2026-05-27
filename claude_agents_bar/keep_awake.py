"""Caffeinate lifecycle owned by the plugin (spec 0003).

The plugin gets to inhibit display/idle sleep on the user's behalf so a
long agentic loop with the lid open doesn't hit display sleep mid-tool.
``/usr/bin/caffeinate -i`` is the macOS primitive; we own at most one
detached instance and reconcile its lifecycle on every 5 s tick.

State on disk
-------------

Two sidecars under ``~/.claude/``:

* ``agent-state.keep-awake.mode`` — single line, one of
  ``off``/``auto``/``always``. Written by ``bin/app/keep-awake-set.sh``;
  read here. Absence falls back to :attr:`core.Config.keep_awake`.

* ``agent-state.caffeinate`` — single decimal PID we spawned. Liveness
  is re-checked every tick via ``os.kill(pid, 0)`` plus a ``/bin/ps -p``
  comm check so PID reuse can't trick us into signalling a different
  process.

The plugin process exits after each tick — ``start_new_session=True``
on spawn detaches the child from our process group so the
``caffeinate`` survives the parent's exit and we re-adopt the PID from
the sidecar on the next tick.

Why a separate module
---------------------

Render needs to *report* the keep-awake state (Tools submenu); the CLI
dispatcher needs to *mutate* it (``--keep-awake <mode>``); and the
main render loop needs to *reconcile* it. Pulling those three concerns
out of :mod:`render` / :mod:`actions` / :mod:`__init__` keeps each
caller thin and the lifecycle logic in one place.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Iterable

from . import core
from .core import _KEEP_AWAKE_MODES, _warn

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

#: Absolute path to ``caffeinate``. Hardcoded — the macOS-shipped binary
#: is always at this path on every release we support, and pinning it
#: removes any PATH-dependency surprise when SwiftBar launches us with a
#: minimal environment.
_CAFFEINATE = "/usr/bin/caffeinate"

#: ``ps`` for the PID-reuse defence. ``-p <pid> -o comm=`` prints just the
#: command name without a header, which is exactly what we compare against
#: ``"caffeinate"`` before sending SIGTERM.
_PS = "/bin/ps"

#: How long to wait for ``SIGTERM`` to be acknowledged before escalating
#: to ``SIGKILL``. Caffeinate has no significant teardown work; 1 s is
#: generous and bounds the worst-case reconcile latency.
_SIGTERM_GRACE_SEC = 1.0


# --------------------------------------------------------------------------- #
# Mode                                                                         #
# --------------------------------------------------------------------------- #


def current_mode() -> str:
    """Return the live keep-awake mode.

    Sidecar takes precedence over config — once the user clicks a mode
    in the menu, *that's* the runtime truth; config is only consulted on
    first launch when no sidecar exists yet. Unknown values fall back to
    config; a config in turn that's already been validated to one of
    :data:`core._KEEP_AWAKE_MODES`.
    """
    try:
        raw = core.KEEP_AWAKE_MODE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        raw = ""
    if raw in _KEEP_AWAKE_MODES:
        return raw
    return core.CONFIG.keep_awake


def write_mode(mode: str) -> int:
    """Persist a new mode to the sidecar and return 0 on success.

    Refuses any value outside :data:`core._KEEP_AWAKE_MODES` — the menu
    only ever asks for the three legal values, so an out-of-band call
    with garbage is a programming error worth surfacing rather than
    silently absorbing.
    """
    if mode not in _KEEP_AWAKE_MODES:
        _warn(f"keep_awake: refusing invalid mode {mode!r}")
        return 1
    try:
        core.KEEP_AWAKE_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.KEEP_AWAKE_MODE_PATH.write_text(mode + "\n", encoding="utf-8")
    except OSError as exc:
        _warn(f"keep_awake: write_mode failed: {exc}")
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Process management                                                           #
# --------------------------------------------------------------------------- #


def _read_pid() -> int | None:
    """Return the PID stored in the sidecar, or ``None`` if absent / bad."""
    try:
        raw = core.KEEP_AWAKE_PID_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _clear_pid() -> None:
    """Best-effort unlink of the PID sidecar."""
    try:
        core.KEEP_AWAKE_PID_PATH.unlink()
    except OSError:
        pass


def _is_alive(pid: int) -> bool:
    """``True`` if ``os.kill(pid, 0)`` doesn't raise ``ESRCH``.

    Distinguishes "process exists" from "we own a stale PID". A live PID
    we *don't* own (PID reuse) is still flagged via :func:`_is_caffeinate`
    before we send any signal.
    """
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        # PermissionError means the PID belongs to another user — we can't
        # signal it, and it's almost certainly not the caffeinate we
        # spawned. Treat as not-ours-so-not-alive (gets cleared from the
        # sidecar).
        return False
    except OSError:
        return False


def _is_caffeinate(pid: int) -> bool:
    """``True`` if the running process at ``pid`` is ``caffeinate``.

    Defensive against PID reuse — between ticks the macOS PID counter
    might roll over and the PID we stored now belongs to (say) a user
    process. ``ps -p <pid> -o comm=`` prints the executable basename
    without a header; we anchor on it before any kill.
    """
    try:
        result = subprocess.run(
            [_PS, "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=1.0, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip().endswith("caffeinate")


def _spawn() -> int | None:
    """Spawn a detached ``caffeinate -i`` and write its PID. Return PID or ``None``.

    ``start_new_session=True`` puts the child into its own process group
    and session, severing it from SwiftBar so it survives our exit.
    stdio is wired to ``/dev/null`` so a stray write can't deadlock on a
    closed pipe — caffeinate never writes anything anyway, but explicit
    detachment is the documented contract.
    """
    try:
        proc = subprocess.Popen(
            [_CAFFEINATE, "-i"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"keep_awake: caffeinate spawn failed: {exc}")
        return None
    try:
        core.KEEP_AWAKE_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.KEEP_AWAKE_PID_PATH.write_text(f"{proc.pid}\n", encoding="utf-8")
    except OSError as exc:
        _warn(f"keep_awake: failed to write PID sidecar: {exc}")
        # Best-effort kill the orphan — we lost track of it, so leaving
        # it running would leak a process per tick.
        try:
            proc.terminate()
        except OSError:
            pass
        return None
    return proc.pid


def _kill(pid: int) -> None:
    """SIGTERM, wait briefly, SIGKILL if still alive. Always unlink the sidecar.

    The grace period covers an unlikely but real scenario: caffeinate
    blocked in a syscall that defers signal delivery. SIGKILL is the
    backstop. Both signals are wrapped so a missing process (raced to
    exit on its own) doesn't crash the reconcile loop.
    """
    if not _is_caffeinate(pid):
        # The PID has been reused or the process is already gone. Just
        # drop our reference; signalling an unrelated process is the
        # bug we are explicitly defending against.
        _clear_pid()
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        _clear_pid()
        return
    deadline = time.monotonic() + _SIGTERM_GRACE_SEC
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            _clear_pid()
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    _clear_pid()


# --------------------------------------------------------------------------- #
# Reconcile                                                                    #
# --------------------------------------------------------------------------- #


def _decide_should_run(mode: str, sessions: Iterable) -> bool:
    """Should we be holding sleep right now?

    * ``off``     → never.
    * ``always``  → always.
    * ``auto``    → iff any session is in ``working`` state (the parent
                    rollup from spec 0004 already folds live subagents
                    into the parent's ``hook_state``, so this single
                    check covers both direct work and Task spawns).

    ``waiting`` deliberately doesn't count: a session blocked on a
    permission prompt needs the *user* to act, and if the user is away
    nothing about the screen staying lit will help.
    """
    if mode == "off":
        return False
    if mode == "always":
        return True
    return any(getattr(s, "hook_state", "") == "working" for s in sessions)


def reconcile(sessions: Iterable) -> None:
    """Run one reconcile pass: spawn / kill / leave alone as needed.

    Called once per render tick from :func:`main`. Cheap when the
    decision matches the current state (the common case) — just a
    sidecar read and a ``kill(pid, 0)`` liveness check.
    """
    mode = current_mode()
    should_run = _decide_should_run(mode, sessions)

    pid = _read_pid()
    is_running = pid is not None and _is_alive(pid) and _is_caffeinate(pid)
    if pid is not None and not is_running:
        # Stale PID file — caffeinate is gone (we died after spawn but
        # before the write completed, or someone killed it from the
        # outside, or PID got reused). Clear and re-decide.
        _clear_pid()

    if should_run and not is_running:
        _spawn()
        return
    if not should_run and is_running:
        _kill(pid)  # type: ignore[arg-type]
        return
    # No-op: state matches.


def shutdown() -> None:
    """Stop any caffeinate we own. Used by teardown / uninstall."""
    pid = _read_pid()
    if pid is not None:
        _kill(pid)
