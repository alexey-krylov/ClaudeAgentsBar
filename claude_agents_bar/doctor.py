"""CLI diagnostics — what ``claude-agents-bar doctor`` runs.

Eight independent checks the user (or a Homebrew formula test, or CI) can
read in plain text. Each returns ``(status, message)`` where status is
``"ok"`` / ``"warn"`` / ``"err"``. ``_run_doctor`` formats the lot,
prints a greppable line per check, and exits non-zero only when something
is genuinely broken (a missing ``settings.json``) so a passing run is
silent enough to splice into other scripts.

This module is opt-in: nothing under :mod:`claude_agents_bar.render`
imports it, so the SwiftBar hot path doesn't pay for it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import core, sidecars


#: Hook events the plugin needs registered in ``~/.claude/settings.json``.
#: The doctor warns when any of these is missing; ``setup.sh`` writes
#: all five. ``SessionStart`` is intentionally absent: it fires on
#: every IDE tab switch (with ``source=resume``), so registering it
#: would put untouched sessions into the menu just because the user
#: clicked on them. The five hooks below all reflect actual agent
#: activity. State keywords on the hook command line live in
#: ``hooks/settings-hooks.json`` in the plugin repo.
_REQUIRED_HOOK_EVENTS = frozenset((
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
))

#: Editor URL scheme → expected .app bundle. Used by the doctor to warn
#: when the configured editor isn't actually installed (the top
#: symptom from docs/troubleshooting.md). Aliases the canonical map in
#: :mod:`core` so the doctor and the row-click focus logic can't drift.
_EDITOR_SCHEME_APP = core.EDITOR_SCHEME_APP


def _doctor_check_tsv_freshness(now: int) -> tuple[str, str]:
    """Did the hook write a TSV row within the last hour?"""
    if not core.SIDECAR_PATH.exists():
        return "warn", (
            f"TSV missing ({core.SIDECAR_PATH}); has any Claude Code session "
            "run since install?"
        )
    try:
        mtime = int(core.SIDECAR_PATH.stat().st_mtime)
    except OSError as exc:
        return "err", f"can't stat {core.SIDECAR_PATH}: {exc}"
    age_sec = now - mtime
    if age_sec > 3600:
        mins = age_sec // 60
        return "warn", (
            f"TSV last updated {mins}m ago — hooks may not be firing, "
            "or no sessions have been active in the last hour"
        )
    return "ok", f"TSV fresh (updated {age_sec}s ago)"


def _has_agent_state_hook(entries: object) -> bool:
    """``True`` iff any matcher under an event runs ``agent-state.sh``."""
    if not isinstance(entries, list):
        return False
    for matcher in entries:
        if not isinstance(matcher, dict):
            continue
        for hook in matcher.get("hooks", []):
            if isinstance(hook, dict) and "agent-state.sh" in hook.get("command", ""):
                return True
    return False


def _doctor_check_hook_registration() -> tuple[str, str]:
    """Are all five required hooks pointing at ``agent-state.sh``?"""
    settings_path = core.HOME / ".claude" / "settings.json"
    if not settings_path.exists():
        return "err", f"{settings_path} missing (run `claude-agents-bar setup`)"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "err", f"can't parse {settings_path}: {exc}"
    hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
    missing = sorted(
        e for e in _REQUIRED_HOOK_EVENTS
        if not _has_agent_state_hook(hooks.get(e))
    )
    if missing:
        return "warn", (
            f"hooks missing for {len(missing)} event(s): {', '.join(missing)} "
            "— re-run `claude-agents-bar setup`"
        )
    return "ok", "all 5 hook events registered"


def _doctor_check_swiftbar_plugin() -> tuple[str, str]:
    """Is the plugin file visible inside SwiftBar's plugins directory?"""
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "read", "com.ameba.SwiftBar", "PluginDirectory"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "warn", f"can't query SwiftBar PluginDirectory: {exc}"
    if result.returncode != 0:
        return "warn", (
            "SwiftBar PluginDirectory not set — open SwiftBar.app and "
            "pick a plugin folder, then re-run `claude-agents-bar setup`"
        )
    plugin_dir = Path(result.stdout.strip()).expanduser()
    if not plugin_dir.is_dir():
        return "warn", f"PluginDirectory {plugin_dir} doesn't exist on disk"
    candidates = list(plugin_dir.glob("claude-agents.*.py"))
    if not candidates:
        return "warn", (
            f"no claude-agents.*.py in {plugin_dir} — "
            "re-run `claude-agents-bar setup`"
        )
    return "ok", f"plugin linked in {plugin_dir}"


def _doctor_check_sidecar_permissions() -> tuple[str, str]:
    """Can the current user read/write every ``~/.claude/agent-state.*`` file?"""
    pattern_dir = core.HOME / ".claude"
    if not pattern_dir.exists():
        # Nothing to check yet — fresh install before any hook fires.
        return "ok", f"{pattern_dir} not present yet (no hook fires yet)"
    bad = []
    for path in pattern_dir.glob("agent-state.*"):
        # We don't care about ``.lock.d`` directories — they're transient.
        if path.suffix == ".d":
            continue
        if not os.access(path, os.R_OK):
            bad.append(f"{path.name} not readable")
        elif not os.access(path, os.W_OK):
            bad.append(f"{path.name} not writable")
    if bad:
        return "warn", "; ".join(bad)
    return "ok", "sidecars readable + writable"


#: WARN above this many files of either kind. Below it the directory is still
#: readable by hand, which is the thing the litter actually costs you.
_LITTER_WARN_THRESHOLD = 10


def _doctor_check_state_dir_litter() -> tuple[str, str]:
    """Is ``~/.claude`` accumulating orphaned temps / stale settings backups?

    Two leaks the user can't see without ``ls``-ing the directory and noticing
    by eye (issue #3): ``agent-state*.<pid>`` temp files stranded by a killed
    writer, and ``settings.json.bak.<timestamp>`` copies from every setup run.
    Both are self-limiting now — the render tick sweeps dead-PID temps and the
    installers rotate backups — so a high count here means pre-fix litter that
    was never cleaned, and the message says how to clear it.
    """
    state_dir = core.HOME / ".claude"
    if not state_dir.exists():
        return "ok", f"{state_dir} not present yet"
    try:
        names = [entry.name for entry in os.scandir(state_dir)]
    except OSError as exc:
        return "warn", f"can't scan {state_dir}: {exc}"
    temps = [n for n in names if sidecars._TEMP_LITTER_RE.match(n)]
    backups = [n for n in names if n.startswith("settings.json.bak.")]
    problems = []
    if len(temps) > _LITTER_WARN_THRESHOLD:
        problems.append(
            f"{len(temps)} orphaned agent-state temp files "
            "(the render tick prunes dead-PID ones older than 5 min)"
        )
    if len(backups) > _LITTER_WARN_THRESHOLD:
        problems.append(
            f"{len(backups)} settings.json backups — keep the newest 5: "
            "ls -t ~/.claude/settings.json.bak.* | tail -n +6 | tr '\\n' '\\0' | xargs -0 rm -f"
        )
    if problems:
        return "warn", "; ".join(problems)
    return "ok", (
        f"{len(temps)} temp files, {len(backups)} settings backups in {state_dir}"
    )


def _doctor_check_editor_app() -> tuple[str, str]:
    """Is the .app that handles the configured ``editor_url_scheme`` installed?"""
    scheme = core.CONFIG.editor_url_scheme
    expected = _EDITOR_SCHEME_APP.get(scheme)
    if expected is None:
        return "ok", f"editor scheme {scheme!r} (custom; not auto-checked)"
    if Path(expected).exists():
        return "ok", f"editor scheme {scheme!r} → {expected}"
    return "warn", (
        f"editor scheme {scheme!r} expects {expected} which isn't installed "
        "— clicks on rows will do nothing until you install it or change "
        "``editor_url_scheme`` in the config"
    )


def _doctor_check_terminal_notifier() -> tuple[str, str]:
    """Is terminal-notifier installed (needed for notify-stop.sh)?"""
    result = subprocess.run(
        ["which", "terminal-notifier"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return "ok", f"terminal-notifier found at {result.stdout.strip()}"
    return "warn", (
        "terminal-notifier not found — completion notifications won't fire. "
        "Install with: brew install terminal-notifier. "
        "Or set ``notify_on_stop: false`` in the config to silence this warning."
    )


def _legacy_statusline_wired() -> bool:
    """Is the pre-1.5.0 usage sensor still this machine's ``statusLine``?

    Fail-soft: an absent / unreadable / non-dict settings file reads as "no".
    """
    try:
        data = json.loads(
            (Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    status_line = data.get("statusLine")
    if not isinstance(status_line, dict):
        return False
    command = status_line.get("command")
    return isinstance(command, str) and "usage-sensor.sh" in command


def _doctor_check_usage(now: int) -> tuple[str, str]:
    """Is the subscription-usage snapshot arriving, and from where?

    Since ADR-0020 there is no daemon to inspect: the plugin spawns a
    ``get_usage`` fetch every ``usage_fetch_interval_sec`` and the only question
    is whether a fresh snapshot lands. When it doesn't, the two things that
    actually break it are a ``claude`` binary the plugin can't find (SwiftBar
    runs with a stripped PATH) and API-key auth (``rate_limits`` need a
    Claude.ai plan). Read-only: we look, never fetch.
    """
    if not core.usage_monitor_enabled():
        return "ok", 'off ("usage_monitor": "off" in the config)'

    from . import sidecars, usage_monitor  # lazy: keeps the render path clean

    # An upgrade from 1.4.x that never re-ran `setup` leaves the old sensor
    # wired as the statusLine, pointing at a script this version doesn't ship.
    # Usage itself still works (the fetch is independent), but every session
    # runs a dead command, so say so — only `setup` can unwire it safely (it
    # holds the saved original).
    if _legacy_statusline_wired():
        return "warn", (
            "your ~/.claude/settings.json still runs the retired 1.4 usage "
            "sensor as its statusLine — that script is gone, so every session "
            "runs a dead command. Fix: claude-agents-bar setup (restores the "
            "status line you had before)"
        )

    usage = sidecars.read_usage()
    interval = core.CONFIG.usage_fetch_interval_sec
    if usage is not None and now - usage.record_ts <= 2 * interval:
        return "ok", f"live (session {usage.five_used}% used)"

    binary = usage_monitor._claude_bin()
    if binary is None:
        return "err", (
            "no `claude` binary found — the usage fetch can't run. SwiftBar "
            "runs plugins with a stripped PATH, so a CLI installed somewhere "
            "unusual is invisible to it; symlink it into /usr/local/bin"
        )
    age = "no snapshot yet" if usage is None else f"snapshot {now - usage.record_ts}s old"
    return "warn", (
        f"{age} — a fetch runs every {interval}s via {binary}; re-check shortly. "
        "If it never lands, check you're on Claude.ai auth (rate_limits need a "
        "plan; API-key / Bedrock / Vertex sessions have none): "
        "claude-agents-bar usage shows what happens"
    )


def _run_doctor() -> int:
    """Run the in-plugin doctor checks. Returns non-zero only on hard errors."""
    now = int(time.time())
    checks = (
        ("hooks/", _doctor_check_hook_registration()),
        ("tsv/", _doctor_check_tsv_freshness(now)),
        ("plugin/", _doctor_check_swiftbar_plugin()),
        ("perms/", _doctor_check_sidecar_permissions()),
        ("litter/", _doctor_check_state_dir_litter()),
        ("editor/", _doctor_check_editor_app()),
        ("notify/", _doctor_check_terminal_notifier()),
        ("usage/", _doctor_check_usage(now)),
    )
    any_err = False
    label_width = max(len(name) for name, _ in checks)
    for name, (status, message) in checks:
        # ``[ok]`` / ``[warn]`` / ``[err]`` keeps the output greppable and
        # the colour-free output readable in CI logs / Homebrew formula tests.
        tag = f"[{status}]"
        print(f"{tag:<6} {name:<{label_width}}  {message}")
        if status == "err":
            any_err = True
    return 1 if any_err else 0
