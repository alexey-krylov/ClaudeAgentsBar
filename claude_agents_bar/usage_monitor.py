"""Usage monitor — periodic ``get_usage`` fetch over the SDK control protocol.

The Claude.ai ``rate_limits`` (the rolling 5-hour and 7-day subscription
windows) come from ``GET /api/oauth/usage``, which the ``claude`` CLI wraps and
exposes over the **SDK control protocol**: feed one
``{"type":"control_request","request":{"subtype":"get_usage"}}`` line into a
``claude -p --input-format stream-json --output-format stream-json`` process and
it answers with the account's live rate limits, then exits. Measured cost: a
~1.7 s process that runs **no inference** (``total_cost_usd: 0``), spends no
quota, creates no transcript, and fires no hooks. See ADR-0020.

That replaced the pre-1.5.0 arrangement (ADR-0018), which held a real
interactive ``claude`` TUI open in a detached ``screen`` and scraped
``rate_limits`` off the statusLine stdin, recycling the session every 10 min to
keep the numbers moving. Same data, without the background session, the
statusLine wrapper in ``settings.json``, or the quota those recycles burned.

Two sources, cheapest first
---------------------------

Any native ``claude`` process caches its own ``/api/oauth/usage`` response in
``~/.claude.json`` under ``cachedUsageUtilization`` (write-throttled to 5 min).
When that cache happens to be fresher than our fetch interval we just read it
and skip spawning anything. Otherwise we run the fetch. If the fetch fails
(offline, CLI missing, timeout) we fall back to the same cache with the CLI's
own one-hour staleness bound, and failing that write nothing — the previous
snapshot ages out on its own and the menu lines simply disappear.

Lifecycle mirrors :mod:`keep_awake`: a per-tick :func:`reconcile` called from
``__init__.main`` decides whether a fetch is due and spawns it **detached**, so
the 5 s tick never blocks on the ~1.7 s call. The fetch itself runs as
``claude-agents.5s.py --usage-fetch`` and is the sole writer of
:data:`core.USAGE_PATH`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import core
from .core import _warn

#: The one control request we send. ``request_id`` is arbitrary — a single
#: request per process, so there's nothing to correlate.
_GET_USAGE_REQUEST = json.dumps(
    {
        "type": "control_request",
        "request_id": "cab_usage",
        "request": {"subtype": "get_usage"},
    }
)

#: Hard ceiling on the fetch subprocess. Measured at ~1.7 s; 30 s is "the CLI
#: is wedged", not "the network is slow" (the CLI's own HTTP timeout is 5 s).
_FETCH_TIMEOUT_SEC = 30.0

#: Fallback staleness bound for ``cachedUsageUtilization`` when the fetch
#: itself failed — the same one hour the CLI applies to its own cache reads.
_CACHE_FALLBACK_MAX_AGE_SEC = 3600

#: Leftovers of the pre-1.5.0 monitor (ADR-0018). ``setup`` clears these on
#: upgrade, but a user who runs ``brew upgrade`` **without** re-running ``setup``
#: would otherwise keep a background ``claude`` alive forever — so the fetch
#: retires them too. Their absence is the "nothing to do" fast path.
_LEGACY_PING_PATH = core.HOME / ".claude" / "agent-state.usage-monitor.ping"
_LEGACY_MONITOR_DIR = core.HOME / ".claude" / "cab-usage-monitor"

#: Where the ``claude`` binary might live when SwiftBar's stripped PATH doesn't
#: have it. SwiftBar runs plugins with a minimal environment, so ``which`` alone
#: misses a Homebrew or native install more often than not.
_CLAUDE_CANDIDATES = (
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
    core.HOME / ".claude" / "local" / "claude",
    core.HOME / ".local" / "bin" / "claude",
)


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #


def _iso_to_epoch(value: object) -> int:
    """ISO-8601 ``resets_at`` → unix epoch seconds; 0 when unusable.

    The API hands back ``"2026-08-27T16:49:59.947591+00:00"``. Python 3.9's
    ``fromisoformat`` parses that offset form but **not** a trailing ``Z``, so
    normalise it first — the server has used both spellings historically and
    the cost of covering it is one ``replace``. Anything else (``None``, a
    number, a malformed string) reads as 0, which callers treat as "no reset
    time known".
    """
    if not isinstance(value, str) or not value:
        return 0
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


def _window(rate_limits: object, key: str) -> tuple[int, int]:
    """One window's ``(utilization %, resets_at epoch)``; ``(-1, 0)`` if absent.

    ``utilization`` is floored to an int so it matches Claude Code's own usage
    view (which never rounds a percentage up) and so a 50 % alert threshold
    fires at a real 50 %, not at 49.6 %. A ``-1`` percentage means the window
    isn't in the payload at all (``null`` for a plan that doesn't have it),
    which :func:`snapshot_from_rate_limits` rejects.
    """
    if not isinstance(rate_limits, dict):
        return -1, 0
    window = rate_limits.get(key)
    if not isinstance(window, dict):
        return -1, 0
    used = window.get("utilization")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return -1, 0
    return int(used), _iso_to_epoch(window.get("resets_at"))


def snapshot_from_rate_limits(rate_limits: object, record_ts: int) -> str | None:
    """Render the five-column snapshot row, or ``None`` if unusable.

    ``record_ts\tfive_used\tfive_resets_at\tseven_used\tseven_resets_at`` — the
    same layout the statusLine sensor wrote before ADR-0020, so
    :func:`sidecars.read_usage`, the alert escalation (which keys on
    ``five_resets_at``) and the menu lines all carry over untouched.

    Requires the 5-hour window with a real reset time; that's the one the menu
    and the alerts are built around. The 7-day window is optional and degrades
    to ``0\t0`` — a plan without it still gets a session line.
    """
    five_used, five_reset = _window(rate_limits, "five_hour")
    if five_used < 0 or five_reset <= 0:
        return None
    seven_used, seven_reset = _window(rate_limits, "seven_day")
    if seven_used < 0:
        seven_used, seven_reset = 0, 0
    return "\t".join(
        str(v) for v in (record_ts, five_used, five_reset, seven_used, seven_reset)
    )


def rate_limits_from_response(stdout: str) -> object | None:
    """Pull ``rate_limits`` out of a ``get_usage`` control response.

    The CLI answers on stdout with newline-delimited JSON; we want the
    ``control_response`` whose ``subtype`` is ``success``. Everything else on
    the stream (init frames, an error response, a stray log line) is skipped,
    and a payload that reports ``rate_limits_available: false`` — API-key auth,
    Bedrock, Vertex — yields ``None`` so nothing gets written.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "control_response":
            continue
        response = event.get("response")
        if not isinstance(response, dict) or response.get("subtype") != "success":
            continue
        payload = response.get("response")
        if not isinstance(payload, dict):
            continue
        if payload.get("rate_limits_available") is False:
            return None
        return payload.get("rate_limits")
    return None


def cached_rate_limits(
    claude_json: object, now: int, max_age_sec: int
) -> tuple[object, int] | None:
    """``(rate_limits, fetched_ts)`` from ``cachedUsageUtilization``, or ``None``.

    Claude Code stores the raw ``/api/oauth/usage`` body here along with
    ``fetchedAtMs``; any native ``claude`` run refreshes it. We return the
    timestamp so the caller can record **when the data was actually fetched**
    rather than when we read it — the menu's staleness gate keys on that, so a
    cached snapshot must not masquerade as fresh. A cache older than
    ``max_age_sec``, or one from the future (clock skew), reads as absent.
    """
    if not isinstance(claude_json, dict):
        return None
    cached = claude_json.get("cachedUsageUtilization")
    if not isinstance(cached, dict):
        return None
    fetched_ms = cached.get("fetchedAtMs")
    if not isinstance(fetched_ms, (int, float)) or isinstance(fetched_ms, bool):
        return None
    fetched_ts = int(fetched_ms // 1000)
    age = now - fetched_ts
    if age < 0 or age > max_age_sec:
        return None
    utilization = cached.get("utilization")
    if not isinstance(utilization, dict):
        return None
    return utilization, fetched_ts


# --------------------------------------------------------------------------- #
# I/O                                                                          #
# --------------------------------------------------------------------------- #


def _claude_bin() -> str | None:
    """Absolute path to the ``claude`` CLI, or ``None`` when it isn't found."""
    found = shutil.which("claude")
    if found:
        return found
    for candidate in _CLAUDE_CANDIDATES:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _read_claude_json() -> object | None:
    """Parse ``~/.claude.json``; ``None`` on any read/parse failure.

    It's a large file owned by Claude Code — we only ever read it, and a
    partial write racing our read just means "no cache this time".
    """
    try:
        raw = core.CLAUDE_JSON_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _query_claude() -> object | None:
    """Run the ``get_usage`` control request; ``rate_limits`` or ``None``.

    Runs from ``$HOME`` — the request needs no project context, and in headless
    ``-p`` mode there's no trust prompt to satisfy (verified: an untrusted
    directory answers fine and gets no entry in ``~/.claude.json``). stdin is
    the single request line; the CLI exits on its own once it has answered.
    """
    binary = _claude_bin()
    if binary is None:
        _warn("usage_monitor: `claude` not found in PATH — no usage data")
        return None
    try:
        result = subprocess.run(
            [
                binary,
                "-p",
                "--verbose",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
            ],
            input=_GET_USAGE_REQUEST + "\n",
            capture_output=True,
            text=True,
            timeout=_FETCH_TIMEOUT_SEC,
            cwd=str(core.HOME),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"usage_monitor: get_usage failed: {exc}")
        return None
    return rate_limits_from_response(result.stdout)


def _write_snapshot(row: str) -> None:
    """Replace :data:`core.USAGE_PATH` atomically; best-effort."""
    tmp = core.USAGE_PATH.with_suffix(core.USAGE_PATH.suffix + f".{os.getpid()}.tmp")
    try:
        core.USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(row, encoding="utf-8")
        tmp.replace(core.USAGE_PATH)
    except OSError as exc:
        _warn(f"usage_monitor: snapshot write failed: {exc}")
        try:
            tmp.unlink()
        except OSError:
            pass


def _read_fetch_ts() -> int:
    """Epoch of the last fetch we started, or 0 if absent / unreadable / bad."""
    try:
        raw = core.USAGE_FETCH_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _write_fetch_ts(now: int) -> None:
    """Record that a fetch just started; best-effort."""
    try:
        core.USAGE_FETCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        core.USAGE_FETCH_PATH.write_text(f"{now}\n", encoding="utf-8")
    except OSError as exc:
        _warn(f"usage_monitor: fetch-marker write failed: {exc}")


# --------------------------------------------------------------------------- #
# Fetch (out-of-band) + reconcile (per-tick)                                   #
# --------------------------------------------------------------------------- #


def fetch(now: int) -> int:
    """Refresh the usage snapshot. Entry point for ``--usage-fetch``.

    Cheapest source first: Claude Code's own cache when it's fresher than our
    interval (any ``claude`` the user ran already paid for it), then the live
    ``get_usage`` call, then the same cache under the CLI's one-hour bound if
    the call failed. Returns 0 when a snapshot was written, 1 otherwise — the
    caller is a detached process nobody waits on, so the code is for a manual
    run. Also retires any pre-1.5.0 leftovers on the way past — see
    :func:`_retire_legacy`.
    """
    _retire_legacy()
    claude_json = _read_claude_json()
    cached = cached_rate_limits(
        claude_json, now, core.CONFIG.usage_fetch_interval_sec
    )
    if cached is not None:
        rate_limits, record_ts = cached
        row = snapshot_from_rate_limits(rate_limits, record_ts)
        if row is not None:
            _write_snapshot(row)
            return 0

    rate_limits = _query_claude()
    if rate_limits is not None:
        row = snapshot_from_rate_limits(rate_limits, now)
        if row is not None:
            _write_snapshot(row)
            return 0

    # The call failed or answered without usable limits — a stale-but-recent
    # cache still beats a menu with no numbers at all.
    stale = cached_rate_limits(claude_json, now, _CACHE_FALLBACK_MAX_AGE_SEC)
    if stale is not None:
        rate_limits, record_ts = stale
        row = snapshot_from_rate_limits(rate_limits, record_ts)
        if row is not None:
            _write_snapshot(row)
            return 0
    return 1


def _retire_legacy() -> None:
    """Kill a pre-1.5.0 background session left behind by a setup-less upgrade.

    ``setup`` is the documented migration, but nothing forces a user to run it
    after ``brew upgrade`` — and the ADR-0018 monitor is a real ``claude`` TUI
    that keeps recycling and spending quota until something quits it. The fetch
    is the natural place to do it: out-of-band, every few minutes, and it only
    costs a pair of ``exists()`` calls once the leftovers are gone.

    Deliberately does **not** touch ``settings.json`` — unwiring the statusLine
    is ``setup``'s job (it holds the saved original), and ``doctor`` says so.
    """
    if not (_LEGACY_PING_PATH.exists() or _LEGACY_MONITOR_DIR.is_dir()):
        return
    kill_legacy_screen()
    try:
        _LEGACY_PING_PATH.unlink()
    except OSError:
        pass
    try:
        _LEGACY_MONITOR_DIR.rmdir()  # only when empty — never eat user files
    except OSError:
        pass


def _spawn_fetch() -> None:
    """Start ``--usage-fetch`` detached so the tick doesn't wait on it."""
    plugin = core.PLUGIN_DIR / "claude-agents.5s.py"
    try:
        subprocess.Popen(
            [sys.executable, str(plugin), "--usage-fetch"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"usage_monitor: fetch spawn failed: {exc}")


def reconcile(now: int) -> None:
    """Spawn a usage fetch when one is due. Called every tick.

    The marker is written **before** the spawn, so a fetch that hangs (or a
    machine that wakes from sleep into a burst of ticks) can't stack up
    processes: at most one fetch per ``usage_fetch_interval_sec``, whatever
    happens to the child. Same crash-isolation contract as the other
    reconcilers — the caller wraps this so a hiccup never takes the menu down.
    """
    if not core.usage_monitor_enabled():
        return
    if now - _read_fetch_ts() < core.CONFIG.usage_fetch_interval_sec:
        return
    _write_fetch_ts(now)
    _spawn_fetch()


def shutdown() -> None:
    """Stop anything the old monitor left running — called from ``teardown``.

    Nothing runs in the background any more, so this only kills a leftover
    pre-1.5.0 ``screen`` session (ADR-0018) and drops the fetch marker. Safe
    and silent when ``screen`` isn't installed or no session exists.
    """
    kill_legacy_screen()
    try:
        core.USAGE_FETCH_PATH.unlink()
    except OSError:
        pass


def kill_legacy_screen() -> int:
    """Quit every leftover ``cab-usage-mon`` screen session; count killed.

    Upgrade path from the ADR-0018 monitor: those sessions are real ``claude``
    TUIs that would otherwise linger forever, spending quota on nothing. Called
    from :func:`shutdown` and from ``setup`` on every install/upgrade.
    """
    screen = shutil.which("screen") or "/usr/bin/screen"
    suffix = "." + core._LEGACY_USAGE_MONITOR_SCREEN
    try:
        listing = subprocess.run(
            [screen, "-ls"],
            capture_output=True, text=True, timeout=2.0, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    killed = 0
    for line in listing.splitlines():
        for token in line.split():
            # session token is "<pid>.<name>"; match the name exactly.
            if not token.endswith(suffix) or not token[: -len(suffix)].isdigit():
                continue
            try:
                subprocess.run(
                    [screen, "-S", token, "-X", "quit"],
                    capture_output=True, timeout=2.0, check=False,
                )
                killed += 1
            except (OSError, subprocess.SubprocessError):
                pass
    return killed
