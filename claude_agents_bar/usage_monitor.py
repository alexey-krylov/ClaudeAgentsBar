"""Usage monitor — a background ``claude`` session that sources live usage.

The Claude.ai ``rate_limits`` (5-hour / 7-day subscription windows) are exposed
by Claude Code **only** on the statusLine stdin, and **only** inside a real
interactive TUI — ``claude -p`` / headless / the VSCode extension never invoke
the status line (verified empirically). So the only way to pull live usage is
to hold a real interactive ``claude`` session open. We do that **invisibly** in
a detached ``screen``: it renders no window, but it's a genuine TTY, so the
status line fires and ``hooks/usage-sensor.sh`` writes the snapshot the plugin
reads. See ADR-0018.

Lifecycle mirrors :mod:`keep_awake` exactly — a per-tick ``reconcile`` started
from ``__init__.main`` decides whether the background session should be running
(``usage_monitor`` master switch) and spawns / kills it. The difference: we
track the session **by its unique ``screen`` name**, not a PID, so there's no
PID-reuse ambiguity and no PID sidecar.

``refreshInterval`` (set in settings.json by ``setup``) keeps the status line —
and thus ``record_ts`` — fresh on a timer. But the server only refreshes
``used_percentage`` in *response to a request*, so an idle session would show
stale percentages. To keep the numbers current (and catch usage from the user's
VSCode work — the limits are account-wide), ``reconcile`` periodically pings the
session with a cheap prompt every ``usage_ping_interval_sec``.

Cost discipline: the tick stays cheap — a ``screen -ls`` parse, an int compare,
and an occasional ``screen -X`` — never a synchronous ``claude`` call.
"""

from __future__ import annotations

import shlex
import subprocess

from . import core
from .core import _warn

_SCREEN = "/usr/bin/screen"
_NAME = core._USAGE_MONITOR_SCREEN


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #


def _session_tokens(screen_ls_stdout: str, name: str) -> list[str]:
    """Every ``<pid>.<name>`` token in ``screen -ls`` output for ``name``.

    ``screen -ls`` prints one indented line per session as ``<pid>.<name>\t
    (Detached|Attached)``. We match the ``.<name>`` token anchored on a tab or
    end-of-token so ``cab-usage-mon`` doesn't also match ``cab-usage-mon-x``.
    Returns the full tokens (with PID) so callers can address each session
    individually — ``screen -S <name>`` is ambiguous when several share a name,
    so killing duplicates needs the PID-qualified token. Pure so it can be
    unit-tested against real ``screen -ls`` text.
    """
    suffix = "." + name
    tokens = []
    for line in screen_ls_stdout.splitlines():
        for tok in line.split():
            # session token is "<pid>.<name>" — match the name exactly so
            # "cab-usage-mon" doesn't also match "cab-usage-mon-2".
            if tok.endswith(suffix) and tok[: -len(suffix)].isdigit():
                tokens.append(tok)
    return tokens


# --------------------------------------------------------------------------- #
# Process management (by screen-session name, not PID)                         #
# --------------------------------------------------------------------------- #


def _live_sessions() -> list[str]:
    """Full ``<pid>.<name>`` tokens of our running detached sessions.

    ``screen -ls`` exits 1 when there are no sessions — that's normal, we parse
    stdout regardless. Any failure to run ``screen`` reads as "none" so the
    monitor degrades to off rather than crashing the tick. The list length is
    the live session count (used to detect and collapse duplicates).
    """
    try:
        result = subprocess.run(
            [_SCREEN, "-ls"],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return _session_tokens(result.stdout, _NAME)


def _spawn() -> None:
    """Start the detached ``claude`` session in the trusted working dir.

    ``screen -dmS`` detaches immediately (no window). The inner command cds into
    the trusted folder (so no trust prompt blocks the TUI), sets a real TERM and
    window size, and ``exec``s ``claude`` under the cheap ping model. Detached
    from SwiftBar via ``start_new_session=True`` like :mod:`keep_awake`.
    """
    model = core.CONFIG.usage_ping_model
    workdir = str(core.USAGE_MONITOR_DIR)
    inner = (
        f"cd {shlex.quote(workdir)} && "
        "export TERM=xterm-256color && stty cols 200 rows 50 2>/dev/null; "
        f"exec claude --model {shlex.quote(model)}"
    )
    try:
        core.USAGE_MONITOR_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [_SCREEN, "-dmS", _NAME, "/bin/bash", "-lc", inner],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"usage_monitor: spawn failed: {exc}")


def _kill(tokens: list[str] | None = None) -> None:
    """Quit our detached session(s) (best-effort) and clear the ping marker.

    Addresses each session by its full ``<pid>.<name>`` token — ``screen -S
    <name> -X quit`` is ambiguous (and a no-op) when several sessions share the
    name, which is exactly how duplicates pile up. Pass ``tokens`` to reuse a
    listing the caller already has; otherwise we look them up.
    """
    if tokens is None:
        tokens = _live_sessions()
    for tok in tokens:
        try:
            subprocess.run(
                [_SCREEN, "-S", tok, "-X", "quit"],
                capture_output=True, timeout=2.0, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    _clear_ping()


# --------------------------------------------------------------------------- #
# Refresh-timestamp sidecar                                                    #
# --------------------------------------------------------------------------- #


def _read_ping() -> int:
    """Last ping epoch from the sidecar, or 0 if absent / unreadable / bad."""
    try:
        raw = core.USAGE_MONITOR_PING_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _write_ping(now: int) -> None:
    """Record the last ping time; best-effort."""
    try:
        core.USAGE_MONITOR_PING_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.USAGE_MONITOR_PING_PATH.write_text(f"{now}\n", encoding="utf-8")
    except OSError as exc:
        _warn(f"usage_monitor: ping-marker write failed: {exc}")


def _clear_ping() -> None:
    """Best-effort unlink of the ping sidecar."""
    try:
        core.USAGE_MONITOR_PING_PATH.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Reconcile (per-tick) + shutdown                                              #
# --------------------------------------------------------------------------- #


def reconcile(now: int) -> None:
    """Keep the background session live and its usage fresh.

    * monitor off → kill every session if any is running; otherwise nothing.
    * monitor on, no session (crashed / never started) → spawn one.
    * monitor on, **duplicates** (>1 session) → collapse to one fresh session.
      Duplicates accumulate from a spawn race (``screen -dmS`` registers a few
      ms after the call, so two quick reconciles can each see "none" and both
      spawn) and used to survive because the old single-``-S``-by-name kill
      can't disambiguate them. Each background session is a real Haiku
      ``claude`` process, so leaking them quietly burns quota — collapse on
      sight.
    * monitor on, exactly one session → **recycle** it (kill + respawn) once an
      interval has elapsed. A long-lived session's `rate_limits` go stale
      because the server only refreshes them on a fresh API response, and
      stuff-pinging an existing TUI proved unreliable (it eventually stops
      accepting input and the numbers freeze). Cycling the session forces a new
      first response with current usage — the robust "watch it and restart it"
      the data depends on.

    Same crash-isolation contract as the other reconcilers: the caller wraps
    this in try/except so a hiccup never takes the menu down. The
    ``usage-monitor.ping`` sidecar records when the session was last
    spawned/recycled.
    """
    mode = core.usage_monitor_mode()
    sessions = _live_sessions()

    if mode != "on":
        if sessions:
            _kill(sessions)
        return

    if not sessions:
        _spawn()  # a fresh session yields a first response within ~20 s
        _write_ping(now)
        return

    if len(sessions) > 1:
        # Leaked duplicates — kill them all and start exactly one.
        _kill(sessions)
        _spawn()
        _write_ping(now)
        return

    if now - _read_ping() >= core.CONFIG.usage_ping_interval_sec:
        _kill(sessions)
        _spawn()
        _write_ping(now)


def shutdown() -> None:
    """Stop the background session — called from teardown."""
    _kill()
