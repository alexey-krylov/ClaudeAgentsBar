"""Constants, configuration, i18n, domain types, and pure formatters.

This module is the dependency root of the :mod:`claude_agents_bar` package —
every other submodule imports from here, and ``core.py`` itself imports
nothing from siblings. Keep it that way: a cycle here would deadlock the
plugin at module-load time, which SwiftBar surfaces as an empty menu.

Layout mirrors the legacy ``claude-agents.5s.py`` ordering: paths and
structural constants first, then :class:`Config` and i18n (loaded once at
import time), then dataclass types, then pure helpers. The split into
``sidecars`` / ``render`` / ``doctor`` / ``actions`` happens *above* this
file — see the package ``__init__`` for the public surface.
"""

from __future__ import annotations

import datetime as _dt
import enum
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, fields, replace
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths and structural constants                                               #
# --------------------------------------------------------------------------- #

HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"
SIDECAR_PATH = HOME / ".claude" / "agent-state.tsv"

#: Cutoff timestamp set by ``bin/app/forget-sessions.sh`` (the *Tools → Forget
#: all sessions* action). Sessions whose latest activity is at or before this
#: moment are filtered out of the rendered menu; live sessions reappear on
#: their next hook event.
DISMISS_PATH = HOME / ".claude" / "agent-state.dismiss"

#: ``{session_id: click_ts}`` sidecar maintained by ``bin/app/open-session.sh``.
#: One row per session, last click wins. Used to decide whether an idle
#: session has been "acknowledged" by the user — see :class:`RenderGroup`.
CLICKS_PATH = HOME / ".claude" / "agent-state.clicks"

#: Mutex on :data:`CLICKS_PATH`, shared between plugin (gc) and the click
#: recorder. Same ``mkdir``-based scheme as the main sidecar lock.
_CLICKS_LOCK_DIR = CLICKS_PATH.with_suffix(CLICKS_PATH.suffix + ".lock.d")

#: ``{session_id: forget_ts}`` sidecar maintained by ``bin/app/forget-session.sh``
#: (the per-row *Forget* action). A session whose ``last_event_ts`` is at or
#: before its ``forget_ts`` is filtered out of the menu — same cutoff semantics
#: as :data:`DISMISS_PATH` but per-session instead of global. A fresh hook
#: event or click pushes ``last_event_ts`` past the cutoff and the row
#: re-surfaces, which is the intended escape hatch if the user wants the row
#: back. Use the per-row *Delete session…* action for permanent removal.
FORGET_PATH = HOME / ".claude" / "agent-state.forget"

#: Ad-hoc quiet-hours pause sidecar maintained by ``bin/app/quiet-pause.sh``
#: and ``bin/app/quiet-resume.sh``. Holds a single naive ISO-8601 local
#: timestamp (``"2026-05-26T23:00:00"``); a value in the future means
#: notifications are paused until then. Absence / past timestamp / corrupt
#: byte all read as "not paused". See ``docs/specs/0002-quiet-hours.md``.
QUIET_UNTIL_PATH = HOME / ".claude" / "agent-state.quiet-until"

#: Ad-hoc quiet-hours *bypass* sidecar maintained by
#: ``bin/app/quiet-bypass.sh`` and ``bin/app/quiet-bypass-cancel.sh``.
#: Inverse of :data:`QUIET_UNTIL_PATH`: a value in the future means
#: notifications fire *even during* the scheduled quiet window (the
#: user wants to be reachable for the rest of the current window).
#: Same naive ISO-8601 local format. Absence / past timestamp / corrupt
#: byte all read as "not bypassed".
QUIET_BYPASS_UNTIL_PATH = HOME / ".claude" / "agent-state.quiet-bypass-until"

#: PID file for the detached ``caffeinate -i`` process the plugin owns
#: when ``keep_awake`` is enabled. Holds a single decimal PID. Liveness is
#: re-checked every tick via ``os.kill(pid, 0)`` plus a ``ps -p`` comm
#: check so PID reuse can't trick us into signalling an unrelated process.
#: See ``docs/specs/0003-keep-awake.md``.
KEEP_AWAKE_PID_PATH = HOME / ".claude" / "agent-state.caffeinate"

#: User-facing keep-awake mode override written by ``bin/app/keep-awake-set.sh``.
#: Single line, one of ``off``/``auto``/``always``. Absence / unknown value
#: falls back to :attr:`Config.keep_awake`, which is the config's source of
#: truth for first launch — once the user clicks a mode in the menu the
#: sidecar takes precedence over config so toggling doesn't require an
#: edit.
KEEP_AWAKE_MODE_PATH = HOME / ".claude" / "agent-state.keep-awake.mode"

#: User-facing override for :attr:`Config.multi_workspace_mode`, written by
#: ``bin/app/multi-workspace-set.sh`` (the *Tools → Multi-workspace mode*
#: checkbox). Single line, ``on`` or ``off``. Absence / unknown value falls
#: back to the config knob — the same first-launch-default-then-sidecar
#: precedence as :data:`KEEP_AWAKE_MODE_PATH`, so toggling from the menu
#: doesn't require rewriting (and reformatting) the user's ``config.json``.
#: Read by both the plugin (dropdown) and the notify hooks (banners).
MULTI_WORKSPACE_MODE_PATH = HOME / ".claude" / "agent-state.multi-workspace.mode"

#: User-facing override for :attr:`Config.notify_audio`, written by
#: ``bin/app/notify-audio-set.sh`` (the *Tools → Notifications →
#: Banner + voice / Banner only* radio pair). Single line, ``on`` or
#: ``off``. Absence / unknown value falls back to the config knob — the
#: same first-launch-default-then-sidecar precedence as
#: :data:`MULTI_WORKSPACE_MODE_PATH`, so switching the notification mode
#: from the menu doesn't require rewriting the user's ``config.json``.
#: Read by both the plugin (menu checkmarks) and the notify hooks (which
#: mute the chime + ``say`` when off).
NOTIFY_AUDIO_MODE_PATH = HOME / ".claude" / "agent-state.notify-audio.mode"

#: Mutex on :data:`FORGET_PATH`, shared between plugin (gc) and
#: ``bin/app/forget-session.sh``. Same ``mkdir``-based scheme as the other sidecar
#: locks.
_FORGET_LOCK_DIR = FORGET_PATH.with_suffix(FORGET_PATH.suffix + ".lock.d")

#: Directory used as a mutex on the sidecar by both the plugin (cleanup) and
#: ``hooks/agent-state.sh`` (writes). ``mkdir`` is atomic on every POSIX
#: filesystem and doesn't need ``util-linux``, unlike ``flock``.
_SIDECAR_LOCK_DIR = SIDECAR_PATH.with_suffix(SIDECAR_PATH.suffix + ".lock.d")

#: TSV that records every subagent (``Task``) the parent has spawned, written
#: by the same ``hooks/agent-state.sh`` script when the hook payload carries
#: an ``agent_id`` field. One row per ``(parent_sid, agent_id)``; the same
#: schema documented in :data:`SubagentSnapshot`.
#:
#: Lives next to :data:`SIDECAR_PATH` so all sidecar locks share a directory.
#: The plugin reads it on every tick to (a) keep the parent ACTIVE while any
#: subagent is live and (b) render the 🤖×N badge + the subagent block in the
#: parent's submenu. See ``docs/specs/0004-subagent-grouping.md``.
SUBAGENTS_SIDECAR_PATH = HOME / ".claude" / "agent-state.subagents.tsv"

#: Mutex on :data:`SUBAGENTS_SIDECAR_PATH`, shared with ``hooks/agent-state.sh``.
_SUBAGENTS_SIDECAR_LOCK_DIR = SUBAGENTS_SIDECAR_PATH.with_suffix(
    SUBAGENTS_SIDECAR_PATH.suffix + ".lock.d"
)

#: Bytes mmap'd from the JSONL tail when searching for the most recent
#: ``gitBranch`` — bounded so huge transcripts (base64 attachments) stay cheap.
#: The same window is used by ``last_usage_tokens`` to find the freshest
#: ``"usage":{…}`` block; both signals live near the file end because Claude
#: Code appends events sequentially.
JSONL_TAIL_BYTES = 64 * 1024

#: Tail window for hunting the *latest* user prompt — used as the
#: aiTitle fallback. Bigger than ``JSONL_TAIL_BYTES`` because we want
#: to catch the most recent few turns, and a single rich turn (with
#: pasted code or large tool outputs) can easily fill 64 KB on its own.
JSONL_USER_TAIL_BYTES = 128 * 1024

#: Upper bound for the per-tick scan that hunts for ``ai-title``. AI titles
#: are emitted within the first few hundred lines (right after the first turn),
#: so capping the scan keeps us cheap even when a transcript has megabytes of
#: base64 attachments dragging the line count up.
JSONL_TITLE_SCAN_BYTES = 256 * 1024

#: ANSI SGR sequences. SwiftBar interprets these when a row carries
#: ``ansi=true``, letting us colour a single segment independently of the
#: base ``color=`` parameter.
_ANSI_RESET = "\x1b[0m"
#: Right-side age labels in the dropdown (toned-down palette).
_ANSI_WORKING = "\x1b[1;33m"  # bold yellow — active in flight
_ANSI_WAITING = "\x1b[1;31m"  # bold red    — needs you
_ANSI_FRESH = "\x1b[1;36m"    # bold cyan   — finished, user hasn't seen it yet
_ANSI_ACK = "\x1b[32m"        # green       — acknowledged
_ANSI_STALE = "\x1b[2;37m"    # dim white   — abandoned
#: ● bullets in the compact menu-bar (brighter palette for legibility).
_ANSI_ACTIVE_BAR = "\x1b[1;93m"  # bold bright yellow
_ANSI_FRESH_BAR = "\x1b[1;92m"   # bold bright green
_ANSI_ACK_BAR = "\x1b[1;94m"     # bold bright blue

#: Session ids we're willing to surface. Claude Code in practice ships
#: RFC-4122 UUIDs, but we accept the broader ``[A-Za-z0-9_-]{1,64}`` —
#: large enough to fit any future Claude id format while still rejecting
#: every shape that could weaponise downstream consumers:
#:
#:   * shell arguments (``param1=`` to ``bin/*.sh``),
#:   * AppleScript dialogs in ``bin/app/delete-session.sh``,
#:   * field-anchored TSV lookups,
#:   * SwiftBar ``paramN=`` tokens, which break on embedded newlines.
#:
#: The set is intentionally narrow: no spaces, no quotes, no shell or
#: regex metacharacters, no path separators, no control bytes. A session
#: id whose source we don't fully control (TSV row written by a hook,
#: JSONL filename created by another process under the same uid) is
#: rejected at the boundary so every downstream consumer stays simple.
#: See the SECURITY note at the top of ``bin/app/delete-session.sh`` and the
#: SwiftBar quoting helper for context.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _is_valid_session_id(value: str) -> bool:
    """True iff ``value`` matches the safe-session-id allow-list.

    See :data:`_SESSION_ID_RE` for the threat model.
    """
    return bool(_SESSION_ID_RE.match(value))


#: Editor URL schemes we're willing to hand to ``open``. Anything outside
#: this set is dropped at config-load time, falling back to the
#: ``Config.editor_url_scheme`` default. Add a new entry here only when a
#: real Code-OSS fork (Cursor, VSCodium, …) registers its own scheme; the
#: empty value is reserved for ``open ?session=…`` style URLs which macOS
#: would otherwise treat as a relative web URL.
_EDITOR_URL_SCHEME_ALLOWLIST = frozenset({
    "vscode://",
    "vscodium://",
    "cursor://",
    "windsurf://",
    "positron://",
})

#: Editor URL scheme → the ``.app`` that registers it. Canonical home for
#: this mapping; ``doctor`` aliases it (to warn when the editor isn't
#: installed) and ``render`` uses it to tell ``open-session.sh`` which app
#: to raise so a row-click deeplink lands in the window matching the
#: session's ``cwd`` rather than whichever window is frontmost. The notify
#: hooks keep a parallel copy in ``hooks/_notify-common.sh`` for the same
#: fix on banner clicks (bash can't import this). Schemes outside this map
#: (custom forks) skip the focus step and fire the deeplink as before.
#: Keep the keys in lockstep with the allowlist above.
EDITOR_SCHEME_APP = {
    "vscode://": "/Applications/Visual Studio Code.app",
    "vscodium://": "/Applications/VSCodium.app",
    "cursor://": "/Applications/Cursor.app",
    "windsurf://": "/Applications/Windsurf.app",
    "positron://": "/Applications/Positron.app",
}


#: Strict 24h ``HH:MM-HH:MM`` matcher for ``Config.quiet_hours``. Mirrors
#: the same regex in ``hooks/_notify-common.sh`` — keep them in lockstep
#: so the menu surface and the hook agree on what's quiet.
_QUIET_HOURS_RE = re.compile(
    r"^(2[0-3]|[01][0-9]):([0-5][0-9])-(2[0-3]|[01][0-9]):([0-5][0-9])$"
)

#: Allowed members of :attr:`Config.quiet_hours_silences`. Anything else is
#: dropped at config-load time with a warning — the spec deliberately lists
#: only these three channels so the menu / hook gating stays simple.
_QUIET_SILENCE_CHANNELS = frozenset({"sound", "voice", "banner"})

#: Allowed values for :attr:`Config.keep_awake` plus the sidecar at
#: :data:`KEEP_AWAKE_MODE_PATH`. Any other value falls back to ``"off"``
#: with a warning — keep_awake controls a process lifecycle, so we'd
#: rather refuse than guess.
_KEEP_AWAKE_MODES = frozenset({"off", "auto", "always"})

#: States that ``hooks/agent-state.sh`` may write to the **parent** sidecar.
HOOK_STATES = frozenset({"waiting", "working", "idle"})

#: The subset of :data:`HOOK_STATES` that mean "session is in flight" — i.e.
#: the right-hand label should show duration of the current state rather
#: than time-since-last-interaction. ``RenderGroup.ACTIVE`` deliberately
#: conflates these two (see its docstring); this is the same conflation at
#: the per-state level.
ACTIVE_HOOK_STATES = frozenset({"working", "waiting"})

#: States that the hook may write to the **subagent** sidecar. ``stopped``
#: replaces ``idle`` because subagents announce their end through the
#: ``SubagentStop`` hook event, whose semantics (Task finished, control
#: returned to the parent) differ from the parent ``Stop`` (turn ended,
#: row goes 🟡 → 🟢). ``waiting`` is absent: subagent-side events never
#: carry ``Notification`` / ``PermissionRequest`` — those always reach the
#: parent.
SUBAGENT_STATES = frozenset({"working", "stopped"})

#: The subset of :data:`SUBAGENT_STATES` that still hold the parent ACTIVE.
SUBAGENT_LIVE_STATES = frozenset({"working"})

#: Agent ids in Claude Code 2.1.x are 16-char hex tokens (e.g.
#: ``a2a96465dfa0eee5d``). Same threat model as :data:`_SESSION_ID_RE` —
#: keep the allow-list narrow so values from the TSV are safe to drop into
#: SwiftBar ``paramN=`` slots and shell arguments without further escaping.
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _is_valid_agent_id(value: str) -> bool:
    """True iff ``value`` matches the safe-agent-id allow-list."""
    return bool(_AGENT_ID_RE.match(value))

#: ``entrypoint`` values that count as interactive — these are the sessions a
#: human is actively typing into. Everything else (notably ``sdk-cli`` for
#: scripted / scheduled runs) is hidden from the menu unconditionally: those
#: aren't sessions the user can usefully click on, and they otherwise clutter
#: the list with cron output. The check is permissive — a session whose first
#: events don't carry ``entrypoint`` at all (older or malformed transcripts)
#: is *kept* so we never silently drop something the user might want.
INTERACTIVE_ENTRYPOINTS = frozenset({
    "claude-vscode",
    "cli",
})

#: Default search order for the user config. Override either entry by setting
#: ``CLAUDE_AGENTS_BAR_CONFIG`` to an explicit path. XDG semantics apply: an
#: explicit ``$XDG_CONFIG_HOME`` takes precedence over ``~/.config``.
_CONFIG_FILENAME = "claude-agents-bar/config.json"

#: Repo root — directory holding the ``claude-agents.5s.py`` shim, ``locales/``,
#: ``bin/`` and ``config.example.json``. Derived from this file's location
#: because the package always lives one level below the shim, both in the
#: source tree and inside the Homebrew Cellar.
PLUGIN_DIR = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# User configuration                                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Config:
    """Tunable parameters loaded from JSON on disk.

    Every field has a sensible default — the config file is optional. When
    a field is present in the JSON but the value can't be coerced to the
    expected type, we keep the default for that field rather than failing
    the whole load (one bad knob shouldn't break the menu).

    Field semantics
    ~~~~~~~~~~~~~~~
    ``window_sec``
        Hide sessions whose last activity is older than this. Derived from
        ``window_minutes`` in the JSON.
    ``fresh_sec``
        How long an idle session stays 🟢 *fresh* after Stop without any
        click. After this it auto-promotes to 🔵 *acknowledged*. A click
        before the timer expires also promotes the session immediately and
        the same duration is then used as the time-to-stale below.
        Derived from ``fresh_minutes``.
    ``ack_sec``
        How long an idle session stays 🔵 *acknowledged* before fading to
        ⚪ *stale*. Each new click while acknowledged restarts this timer.
        Derived from ``ack_minutes``.
    ``watchdog_sec``
        A sidecar row marked ``working`` but older than this is treated as
        ``idle`` — handles crashed sessions that never emitted ``Stop``.
    ``title_max``
        Maximum title length on a row, with an ellipsis appended on overflow.
    ``menubar_icon``
        Icon drawn before the counters. Accepts four shapes:

        * a plain glyph (e.g. ``"🤖"``, ``"✱"``) — embedded inline.
        * ``"sf:<name>"`` — SF Symbol via SwiftBar's ``sfimage=``.
        * ``"template:<path>"`` — monochrome template PNG, rendered
          natively (adapts to dark/light/active menu-bar). Default
          points at Claude.app's own tray icon so the menu bar shows
          the Claude mark when Claude.app is installed.
        * ``"image:<path>"`` — full-colour PNG (no theme adaptation).

        Paths may be absolute or relative to the plugin directory.
        For ``template:``/``image:`` paths the plugin auto-resizes the
        source to fit the menu bar height (cached under
        ``$XDG_CACHE_HOME/claude-agents-bar/``).
    ``menubar_icon_fallback``
        Glyph to use when ``menubar_icon`` points at a file that doesn't
        exist (Claude.app not installed, broken path, …). Defaults to
        ``"🤖"`` to preserve the original behaviour from before the
        template-image feature.
    ``editor_url_scheme``
        URL scheme prefix used to build the per-row deeplink that opens
        a session in the user's editor — the full URL is
        ``<editor_url_scheme>anthropic.claude-code/open?session=<uuid>``.
        Defaults to ``"vscode://"`` for stock VS Code. VSCodium forks
        register their own scheme (``"vscodium://"``); other Code-OSS
        forks may need their own value. Must include the trailing
        ``"://"``.
    ``language``
        UI language for menu labels, dialogs and time-ago strings. Empty
        or ``"auto"`` (the default) detects from macOS ``AppleLocale``
        falling back to ``$LANG``. Supported codes: ``en``, ``ru``, ``zh``,
        ``fr``, ``de``, ``it``. Unknown codes fall back to ``en``.
    ``compact``
        When ``True``, the menu-bar title is rendered in a narrower form:
        the icon is suppressed and the wide emoji circles 🟡🟢🔵 are
        replaced with ANSI-coloured ``●`` bullets (``●2 ●1 ●3``). Saves
        roughly 30 px — meant for notched MacBooks where every menu-bar
        slot counts. Default ``False``. See ADR-0010 for the rationale
        behind ANSI bullets vs the alternatives.
    ``context_window_tokens``
        Total size of the model's context window in tokens, used as the
        denominator for the per-session ``{N}% — {used}k/{total}k``
        indicator in the submenu. Default ``1_000_000`` — matches
        Claude Opus 4.7 / Opus 4.6 / Sonnet 4.6 (Opus 4.7 has been
        Anthropic's API default since 2026-04-23). Override down to
        ``200_000`` when running Haiku 4.5 or Sonnet 4.5 in the
        session. We do not auto-detect from the transcript because the
        SDK response carries the model name but not the context window,
        and no publicly stable Anthropic API surfaces it either; see
        ADR-0011 for the alternatives considered.
    ``context_warning_threshold``
        Percentage of context-window usage above which the main row
        gets an inline ``⚠ {pct}%`` marker between the title and the
        age label. Default ``80`` (matches the yellow zone in Claude
        Code's own CLI). Valid range ``1..100``. The marker switches
        from yellow to red once usage crosses 90 % so a glance tells
        you how close auto-compact is. Set to ``100`` to effectively
        disable the warning while keeping the submenu gauge.
    ``quiet_hours``
        ``"HH:MM-HH:MM"`` scheduled silence window (24h, local time)
        or ``None`` to disable. ``start > end`` wraps midnight; ``start
        == end`` is treated as "never quiet" (safer than always).
        Hooks consult :attr:`quiet_hours_silences` to decide which
        channels to suppress while quiet. The same value drives the
        *Tools → Notifications* status line. See
        ``docs/specs/0002-quiet-hours.md``.
    ``quiet_hours_silences``
        Channels suppressed while quiet: subset of ``("sound",
        "voice", "banner")``. Default ``("sound", "voice")`` — quiet
        mutes audio (chime + ``say``) but the banner still appears so
        you don't miss the event. Add ``"banner"`` to go fully silent,
        or list only ``"voice"`` to keep the chime. Anything outside
        the allow-list falls back to the default with a warning.
    ``notify_audio``
        Master switch for notification audio (the chime *and* the spoken
        ``say`` summary), independent of quiet hours. ``True`` (default):
        notifications play sound per :attr:`notify_sound_stop` /
        ``notify_voice``. ``False``: banner only — no chime, no speech.
        Surfaced as the *Tools → Notifications → Banner + voice / Banner
        only* radio pair; once the user picks one the sidecar at
        :data:`NOTIFY_AUDIO_MODE_PATH` takes precedence, so the config
        knob is just the first-launch default. Does not touch the banner —
        that's always shown (quiet hours aside).
    ``keep_awake``
        First-launch default for the keep-awake reconcile loop:
        ``"off"`` (default), ``"auto"`` (caffeinate while any session
        is *working*), or ``"always"`` (caffeinate until disabled).
        Once the user clicks a mode in the menu the sidecar at
        :data:`KEEP_AWAKE_MODE_PATH` takes precedence over config —
        the config knob is just the initial state, not the runtime
        truth. See ``docs/specs/0003-keep-awake.md``.
    ``multi_workspace_mode``
        Master switch for raising the editor window that owns a clicked
        session before firing the deeplink. Default ``True`` so clicks
        land in the right window even with several windows / a multi-root
        workspace open. Set ``False`` to skip the window-raise (and the
        anchor tab + settle it entails) and fire the deeplink directly —
        instant, but it lands in whatever window is frontmost. Gates
        :attr:`editor_focus_settle_sec`.
    ``editor_focus_settle_sec``
        Seconds the focus helper (``hooks/raise-and-open.sh``) waits
        after raising the editor window before firing the session
        deeplink. ``open -a <file>`` returns before the editor has
        rendered the anchor tab; without this beat the deeplink fires
        first and the anchor tab renders on top of the resumed chat, so
        you land on the file instead of the session. Default ``0.1`` —
        comfortably above the race threshold seen in testing (``0.05`` was
        occasionally flaky under load).
        Lower trims latency but risks landing on the file under load;
        ``0`` skips the settle. Range ``0..5``.
    """

    window_sec: int = 3 * 3600
    fresh_sec: int = 60 * 60
    ack_sec: int = 60 * 60
    watchdog_sec: int = 90
    title_max: int = 60
    menubar_icon: str = (
        "template:/Applications/Claude.app/Contents/Resources/TrayIconTemplate@2x.png"
    )
    menubar_icon_fallback: str = "🤖"
    editor_url_scheme: str = "vscode://"
    language: str = ""
    compact: bool = False
    context_window_tokens: int = 1_000_000
    context_warning_threshold: int = 80
    #: Toggle for the per-row model badge (ⓞ/ⓢ/ⓗ/ⓜ next to the title when
    #: the session's model differs from the user's default) and the model
    #: line in each session's submenu. Defaults ``True``; flip to
    #: ``False`` to suppress the glyph and the row entirely. See
    #: ``docs/specs/0004-subagent-grouping.md`` § Model badge & submenu row.
    model_badge: bool = True
    #: Quiet-hours window, ``"HH:MM-HH:MM"`` (24h local) or ``None``.
    #: Default ``"23:00-08:00"`` — a hands-off night window so the menu
    #: doesn't ding/speak while the user is asleep (the banner still
    #: appears; see :attr:`quiet_hours_silences`). Set to ``None`` (or
    #: override via ``config.json``) to disable.
    quiet_hours: str | None = "23:00-08:00"
    #: Channels suppressed during quiet hours. Default mutes audio only
    #: (chime + voice); the banner still appears so the event isn't
    #: missed. Add "banner" to go fully silent.
    quiet_hours_silences: tuple[str, ...] = ("sound", "voice")
    #: Master switch for notification audio (chime + ``say``). ``True``
    #: (default): notifications sound off per ``notify_sound_*`` /
    #: ``notify_voice``. ``False``: banner only — no chime, no speech.
    #: First-launch default only; the sidecar at
    #: :data:`NOTIFY_AUDIO_MODE_PATH` (set from the menu) overrides at
    #: runtime. Mirrored by the bash reader in ``hooks/_notify-common.sh``.
    notify_audio: bool = True
    #: Prefix that marks the assistant's spoken-summary line — the last line
    #: of a reply (markdown ``*``/``_`` wrappers stripped) starting with this
    #: is read aloud by the Stop hook and re-spoken by the per-row *Remind*
    #: submenu item. Default ``"-- "``; an explicit ``null``/``""`` disables
    #: the feature (the *Remind* item then renders permanently disabled).
    #: Mirrors ``notify_summary_marker`` read by ``hooks/notify-stop.sh``.
    notify_summary_marker: str = "-- "
    #: First-launch keep-awake mode (sidecar overrides at runtime).
    keep_awake: str = "off"
    #: Master switch for the multi-workspace window-focus behaviour.
    #: ``True`` (default): a session click (dropdown row or notification
    #: banner) first raises the editor window that owns the session's cwd,
    #: then fires the deeplink — so it lands in the right window even with
    #: several windows / a multi-root workspace open. ``False``: skip all
    #: of that and just fire the deeplink (the pre-fix behaviour — instant,
    #: opens no anchor tab, but lands in whatever window is frontmost).
    #: Turn off if you only ever run one editor window and want the
    #: snappiest open. Gates :attr:`editor_focus_settle_sec`.
    multi_workspace_mode: bool = True
    #: Seconds the focus helper waits after raising the editor window
    #: before firing the session deeplink, so the anchor tab finishes
    #: rendering and the resumed chat lands on top of it instead of under
    #: it. See ``hooks/raise-and-open.sh``. Default ``0.1`` — comfortably
    #: above the render-race threshold seen in testing (``0.05`` was
    #: occasionally flaky under load); lower risks landing on the anchor
    #: file, higher just adds latency. ``0`` disables the settle entirely.
    #: Range ``0..5``.
    editor_focus_settle_sec: float = 0.1

    # --- Loader ------------------------------------------------------------ #

    @classmethod
    def load(cls) -> "Config":
        """Read the JSON config from disk and overlay it onto the defaults."""
        path = _config_path()
        if path is None or not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _warn(f"config load failed ({path}): {exc}")
            return cls()
        if not isinstance(data, dict):
            _warn(f"config root is not an object ({path}); ignoring")
            return cls()
        return cls._from_mapping(data)

    @classmethod
    def _from_mapping(cls, data: dict) -> "Config":
        """Translate the JSON-friendly schema into the internal field set."""
        coerced: dict[str, object] = {}

        def take(json_key: str, field_name: str, kind: type, transform=None) -> None:
            if json_key not in data:
                return
            raw = data[json_key]
            try:
                value = kind(raw) if not isinstance(raw, kind) else raw
            except (TypeError, ValueError):
                _warn(f"config: ignoring invalid {json_key}={raw!r}")
                return
            if transform is not None:
                try:
                    value = transform(value)
                except (TypeError, ValueError) as exc:
                    _warn(f"config: ignoring invalid {json_key}={raw!r} ({exc})")
                    return
            coerced[field_name] = value

        # Positive-int constraint: 0 or negative would make _format_context_left
        # return an empty string and the row would vanish silently. Better to
        # warn loudly and keep the 1M default.
        def _require_positive(n: int) -> int:
            if n <= 0:
                raise ValueError("must be > 0")
            return n

        def _require_editor_scheme(value: str) -> str:
            if value not in _EDITOR_URL_SCHEME_ALLOWLIST:
                raise ValueError(
                    f"must be one of {sorted(_EDITOR_URL_SCHEME_ALLOWLIST)}"
                )
            return value

        take("window_minutes", "window_sec", float, lambda m: int(m * 60))
        take("fresh_minutes", "fresh_sec", float, lambda m: int(m * 60))
        take("ack_minutes", "ack_sec", float, lambda m: int(m * 60))
        take("watchdog_seconds", "watchdog_sec", int)
        take("title_max", "title_max", int)
        take("menubar_icon", "menubar_icon", str)
        take("menubar_icon_fallback", "menubar_icon_fallback", str)
        # Lock the editor URL scheme to known Code-OSS-family handlers. The
        # value flows into ``open <url>``, so an attacker who can write to
        # the config (e.g. a malicious process running under the same uid,
        # or a synced config from another machine) could otherwise pivot
        # every row click into launching an arbitrary registered URL
        # handler (``file://`` apps, custom schemes). Schemes outside the
        # allow-list are logged and dropped, falling back to the default.
        take("editor_url_scheme", "editor_url_scheme", str, _require_editor_scheme)
        take("language", "language", str)
        take("context_window_tokens", "context_window_tokens", int, _require_positive)

        # Settle delay in seconds — keep it sane. Negative is meaningless and
        # an over-large value would freeze the row click for seconds; cap at
        # 5s. 0 is allowed (skip the settle, at the user's own risk).
        def _require_settle(x: float) -> float:
            if not (0 <= x <= 5):
                raise ValueError("must be in 0..5 seconds")
            return x

        take(
            "editor_focus_settle_sec",
            "editor_focus_settle_sec",
            float,
            _require_settle,
        )

        # Percent — keep in (0, 100]. Values outside that range are useless
        # (≤0 fires the warning unconditionally, >100 is unreachable) so we
        # drop them rather than silently clamping.
        def _require_percent(n: int) -> int:
            if not (1 <= n <= 100):
                raise ValueError("must be in 1..100")
            return n

        take(
            "context_warning_threshold",
            "context_warning_threshold",
            int,
            _require_percent,
        )
        # JSON booleans are native Python bool after json.loads; bool("false")
        # == True so we can't use the generic take() helper here.
        if "compact" in data and isinstance(data["compact"], bool):
            coerced["compact"] = data["compact"]
        if "model_badge" in data and isinstance(data["model_badge"], bool):
            coerced["model_badge"] = data["model_badge"]
        if "multi_workspace_mode" in data and isinstance(
            data["multi_workspace_mode"], bool
        ):
            coerced["multi_workspace_mode"] = data["multi_workspace_mode"]
        if "notify_audio" in data and isinstance(data["notify_audio"], bool):
            coerced["notify_audio"] = data["notify_audio"]

        # Nullable string with strict format — take() would either eat the
        # explicit ``null`` (treating it as "fall back to default") or trip
        # on ``str(None)``. Handle the three valid shapes by hand.
        if "quiet_hours" in data:
            raw_qh = data["quiet_hours"]
            if raw_qh is None:
                coerced["quiet_hours"] = None
            elif isinstance(raw_qh, str) and _QUIET_HOURS_RE.match(raw_qh):
                # start == end is treated as "never quiet" at the parse
                # site below — we still accept the literal form here so a
                # user can flip it on/off by edit without re-typing.
                coerced["quiet_hours"] = raw_qh
            else:
                _warn(
                    f"config: ignoring invalid quiet_hours={raw_qh!r} "
                    f"(must be \"HH:MM-HH:MM\" 24h or null)"
                )

        if "quiet_hours_silences" in data:
            raw_qs = data["quiet_hours_silences"]
            if not isinstance(raw_qs, list):
                _warn(
                    f"config: ignoring invalid quiet_hours_silences={raw_qs!r} "
                    f"(must be a list of strings)"
                )
            else:
                kept: list[str] = []
                seen: set[str] = set()
                bad: list[object] = []
                for v in raw_qs:
                    if isinstance(v, str) and v in _QUIET_SILENCE_CHANNELS:
                        if v not in seen:
                            kept.append(v)
                            seen.add(v)
                    else:
                        bad.append(v)
                if bad:
                    _warn(
                        f"config: dropping unknown quiet_hours_silences "
                        f"entries: {bad!r}; allowed: "
                        f"{sorted(_QUIET_SILENCE_CHANNELS)}"
                    )
                coerced["quiet_hours_silences"] = tuple(kept)

        # Nullable string: an explicit ``null``/``""`` disables the spoken
        # summary (and the Remind item); absence keeps the default. Mirrors
        # ``_cfg_string_or_null`` in ``hooks/_notify-common.sh``.
        if "notify_summary_marker" in data:
            raw_marker = data["notify_summary_marker"]
            if raw_marker is None:
                coerced["notify_summary_marker"] = ""
            elif isinstance(raw_marker, str):
                coerced["notify_summary_marker"] = raw_marker
            else:
                _warn(
                    f"config: ignoring invalid notify_summary_marker="
                    f"{raw_marker!r} (must be a string or null)"
                )

        if "keep_awake" in data:
            raw_ka = data["keep_awake"]
            if isinstance(raw_ka, str) and raw_ka in _KEEP_AWAKE_MODES:
                coerced["keep_awake"] = raw_ka
            else:
                _warn(
                    f"config: ignoring invalid keep_awake={raw_ka!r}; "
                    f"allowed: {sorted(_KEEP_AWAKE_MODES)}"
                )

        # Drop unknown keys silently — they're forward-compatibility hooks.
        valid_names = {f.name for f in fields(cls)}
        coerced = {k: v for k, v in coerced.items() if k in valid_names}
        return replace(cls(), **coerced)


def _config_path() -> Path | None:
    """Resolve where the user's ``config.json`` lives, or ``None`` if disabled."""
    override = os.environ.get("CLAUDE_AGENTS_BAR_CONFIG")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else HOME / ".config"
    return base / _CONFIG_FILENAME


def _warn(message: str) -> None:
    """Log a config-time warning to stderr; SwiftBar surfaces it in *Show Logs*."""
    print(f"[claude-agents-bar] {message}", file=sys.stderr)


#: Singleton — read once at import time. Cheap (a few hundred bytes of JSON).
CONFIG = Config.load()


def multi_workspace_enabled() -> bool:
    """Whether the window-raising focus behaviour is currently on.

    Sidecar (:data:`MULTI_WORKSPACE_MODE_PATH`, ``on``/``off``) takes
    precedence over :attr:`Config.multi_workspace_mode` — once the user
    flips the *Tools → Multi-workspace mode* checkbox, that's the runtime
    truth; the config knob is only the first-launch default. Any
    absence / unreadable / unrecognised sidecar value falls back to
    config. Mirrors the bash reader in ``hooks/_notify-common.sh`` — keep
    the two in lockstep.
    """
    try:
        raw = MULTI_WORKSPACE_MODE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        raw = ""
    if raw == "on":
        return True
    if raw == "off":
        return False
    return CONFIG.multi_workspace_mode


def write_multi_workspace_mode(on: bool) -> int:
    """Persist the multi-workspace toggle to the sidecar; 0 on success.

    Written by the *Tools → Multi-workspace mode* checkbox via
    ``bin/app/multi-workspace-set.sh`` → ``--multi-workspace on|off``.
    """
    try:
        MULTI_WORKSPACE_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
        MULTI_WORKSPACE_MODE_PATH.write_text(
            ("on" if on else "off") + "\n", encoding="utf-8"
        )
    except OSError as exc:
        _warn(f"multi_workspace: write failed: {exc}")
        return 1
    return 0


def notify_audio_enabled() -> bool:
    """Whether notification audio (chime + ``say``) is currently on.

    Sidecar (:data:`NOTIFY_AUDIO_MODE_PATH`, ``on``/``off``) takes
    precedence over :attr:`Config.notify_audio` — once the user picks
    *Banner + voice* or *Banner only* in the menu, that's the runtime
    truth; the config knob is only the first-launch default. Any
    absence / unreadable / unrecognised sidecar value falls back to
    config. Mirrors ``_notify_audio_enabled`` in
    ``hooks/_notify-common.sh`` — keep the two in lockstep.
    """
    try:
        raw = NOTIFY_AUDIO_MODE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        raw = ""
    if raw == "on":
        return True
    if raw == "off":
        return False
    return CONFIG.notify_audio


def write_notify_audio_mode(on: bool) -> int:
    """Persist the notification-audio toggle to the sidecar; 0 on success.

    Written by the *Tools → Notifications → Banner + voice / Banner only*
    radio pair via ``bin/app/notify-audio-set.sh`` → ``--notify-audio
    on|off``.
    """
    try:
        NOTIFY_AUDIO_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
        NOTIFY_AUDIO_MODE_PATH.write_text(
            ("on" if on else "off") + "\n", encoding="utf-8"
        )
    except OSError as exc:
        _warn(f"notify_audio: write failed: {exc}")
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Localization                                                                 #
# --------------------------------------------------------------------------- #

#: Where the per-locale JSON tables live, colocated with the plugin shim
#: (one level above this package). Resolving through :data:`PLUGIN_DIR`
#: keeps the lookup symlink-safe — ``install.sh`` symlinks the shim into
#: the SwiftBar plugins folder, and ``Path(__file__).resolve()`` follows
#: the symlink so the directory next to the *source* file is what we read.
_LOCALES_DIR = PLUGIN_DIR / "locales"


def _load_strings() -> dict[str, dict[str, str]]:
    """Read every ``locales/<lang>.json`` into ``{lang: {key: template}}``.

    Each file is a flat ``{ "menu.refresh": "...", ... }`` map. Keys
    starting with ``_`` (e.g. ``_meta``) are treated as metadata and
    dropped — this lets translators add documentation fields without
    polluting the runtime table.

    Failures degrade gracefully:

    * a missing ``locales/`` directory or a malformed file is logged via
      :func:`_warn` and skipped — the affected locale simply isn't
      available and lookups fall through to the English source-of-truth;
    * a missing ``en.json`` is warned about but doesn't crash; lookups
      then fall back to the literal key, leaving a visibly broken (but
      diagnosable) menu rather than a Python traceback.

    Placeholders use Python ``str.format`` syntax (``{n}``, ``{title}``,
    ``{sid}``, ``{duration}``, ``{exc}``) — keep them identical across
    locales, the renderer passes the same kwargs regardless of language.
    """
    tables: dict[str, dict[str, str]] = {}
    if not _LOCALES_DIR.is_dir():
        _warn(f"locales directory missing: {_LOCALES_DIR}")
        return tables
    for path in sorted(_LOCALES_DIR.glob("*.json")):
        lang = path.stem.lower()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _warn(f"locale {path.name} failed to load: {exc}")
            continue
        if not isinstance(raw, dict):
            _warn(f"locale {path.name} is not a JSON object")
            continue
        tables[lang] = {
            k: v for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, str) and not k.startswith("_")
        }
    if "en" not in tables:
        _warn(f"required source-of-truth locale 'en' not found in {_LOCALES_DIR}")
    return tables


#: Loaded once at import time. Same lifecycle as :data:`CONFIG` — the menu
#: re-runs as a fresh process every ~5 s, so re-reading on edits is free.
STRINGS: dict[str, dict[str, str]] = _load_strings()


def _normalize_lang(raw: str) -> str:
    """Normalize ``zh_TW`` / ``zh-TW`` / ``zh_TW.UTF-8`` → ``zh-tw``.

    Strips any codepage suffix, unifies the separator to ``-``, and lowercases
    the whole thing so the result matches ``locales/<code>.json`` filename
    stems (themselves lowercased on load). Preserves the optional region
    subtag so Traditional Chinese for Taiwan (``zh-tw``) stays distinguishable
    from generic Chinese (``zh``).
    """
    return raw.strip().split(".", 1)[0].replace("_", "-").lower()


def _detect_system_lang() -> str:
    """macOS GUI locale (e.g. ``zh-tw`` or ``en``), or ``"en"`` if nothing usable was found.

    GUI apps under launchd don't inherit a shell ``LANG``, so the canonical
    source is ``defaults read -g AppleLocale`` (the same value System Settings
    writes). We still consult ``LANG`` / ``LC_*`` as a fallback for terminal
    runs (tests, manual invocation).
    """
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "read", "-g", "AppleLocale"],
            capture_output=True, text=True, timeout=0.5, check=False,
        )
        if result.returncode == 0:
            code = _normalize_lang(result.stdout)
            if code:
                return code
    except (OSError, subprocess.SubprocessError):
        pass
    for env in ("LANG", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(env, "")
        if val and val.lower() not in ("c", "posix"):
            return _normalize_lang(val)
    return "en"


def _resolve_lang() -> str:
    """Pick the UI locale code, honouring ``CONFIG.language`` overrides.

    Tries the full ``lang-region`` code first so ``zh-tw`` picks
    ``locales/zh-TW.json`` over the generic ``locales/zh.json``; on miss,
    falls back to the primary subtag (``zh``), and finally to English.
    """
    raw = (CONFIG.language or "").strip().lower()
    code = _normalize_lang(raw) if raw and raw != "auto" else _detect_system_lang()
    if code in STRINGS:
        return code
    primary = code.split("-", 1)[0]
    if primary in STRINGS:
        return primary
    return "en"


_LANG_CACHE: str | None = None


def _lang() -> str:
    """Return the resolved UI locale, computing it once on first use."""
    global _LANG_CACHE
    if _LANG_CACHE is None:
        _LANG_CACHE = _resolve_lang()
    return _LANG_CACHE


def _t_for(key: str, lang: str, **kwargs: object) -> str:
    """Look up ``key`` in ``lang``, falling back to English then to the key itself.

    Uses ``.get("en", {})`` rather than indexing — a broken install missing
    ``locales/en.json`` then renders the bare key (e.g. ``menu.refresh``) in
    the UI rather than crashing the SwiftBar tick.
    """
    template = (
        STRINGS.get(lang, {}).get(key)
        or STRINGS.get("en", {}).get(key, key)
    )
    return template.format(**kwargs) if kwargs else template


def _t(key: str, **kwargs: object) -> str:
    """Look up ``key`` in the resolved UI locale (see :func:`_lang`)."""
    return _t_for(key, _lang(), **kwargs)


# --------------------------------------------------------------------------- #
# Domain types                                                                 #
# --------------------------------------------------------------------------- #


class RenderGroup(enum.Enum):
    """Presentation bucket for a session — orthogonal to the raw hook state.

    ``ACTIVE`` deliberately conflates ``waiting`` and ``working``: visually
    they're the same urgency category ("something is happening, look at it"),
    even though semantically one is blocked on the user and the other on the
    model. The distinction is preserved on the per-row label colour
    (:attr:`Session.right_label_ansi`), not on the group icon.

    The three idle buckets express how the user relates to a finished
    session:

    * 🟢 ``FRESH``        — Stop fired recently, user hasn't opened it yet.
    * 🔵 ``ACKNOWLEDGED`` — either user clicked the row, or the fresh
      window expired on its own. The session is still considered worth
      keeping in sight; each new click restarts the timer.
    * ⚪ ``STALE``        — long enough without any interaction that we
      assume it's been abandoned.
    """

    ACTIVE = ("active", 0, "🟡", "#cc7700")
    FRESH = ("fresh", 1, "🟢", "#1f7a1f")
    ACKNOWLEDGED = ("acknowledged", 2, "🔵", "#0a84ff")
    STALE = ("stale", 3, "⚪", "#777777")

    def __init__(self, key: str, order: int, icon: str, color: str) -> None:
        self.key = key
        self.order = order
        self.icon = icon
        self.color = color


@dataclass(frozen=True)
class HookSnapshot:
    """A single row of ``agent-state.tsv`` — the last hook fact about a session.

    ``state_since`` is the Unix time at which the session entered its current
    ``state``; the hook preserves it across consecutive events of the same
    state, so during one ``working`` cycle this stays pinned to the moment
    the user submitted their prompt. Legacy 5-column rows (written by older
    hook versions) come in with ``state_since == last_event_ts`` — that
    over-estimates "freshness" of the state by one event, which corrects
    itself on the next hook fire.
    """

    state: str
    last_event_ts: int
    last_event_kind: str
    cwd: str
    state_since: int


@dataclass(frozen=True)
class SubagentSnapshot:
    """A single row of ``agent-state.subagents.tsv`` — the last hook fact
    about a subagent (``Task`` spawn).

    The plugin uses this to (a) keep the parent ACTIVE when at least one
    of its subagents is ``working`` and (b) render the subagent block in
    the parent's submenu. See ``docs/specs/0004-subagent-grouping.md``.

    Subagents share the parent's ``session_id`` in Claude Code 2.1.x —
    confirmed by the spike — so rows are keyed on ``(parent_sid, agent_id)``.

    ``first_event_ts`` is set once on the row's first hook write and
    never advanced; ``last_event_ts - first_event_ts`` is therefore the
    end-to-end runtime of a stopped subagent. ``None`` on legacy rows
    written by the pre-7-column hook — the renderer skips the
    ``ran Xs`` suffix in that case rather than emitting a wrong value.
    """

    parent_sid: str
    agent_id: str
    agent_type: str
    state: str
    state_since: int
    last_event_ts: int
    first_event_ts: int | None = None

    @property
    def is_live(self) -> bool:
        """True iff this subagent is still running.

        Watchdog demotion to ``stopped`` happens in :mod:`render`, not here —
        the snapshot is the raw row from disk. Live here means the last
        hook event said ``working``.
        """
        return self.state in SUBAGENT_LIVE_STATES


@dataclass(frozen=True)
class TranscriptMeta:
    """Subset of a JSONL transcript needed to render a menu row."""

    ai_title: str = ""
    raw_title: str = ""
    cwd: str = ""
    entrypoint: str = ""
    last_user_message: str = ""

    @property
    def display_title(self) -> str:
        """Title to show in the UI.

        Priority order: AI-generated summary → latest user prompt
        (so a fresh session shows what the user just asked rather
        than a stale opening line) → first user prompt (works on
        truncated transcripts where the tail doesn't carry a parseable
        user event yet).
        """
        return _shorten(
            self.ai_title or self.last_user_message or self.raw_title
        )


@dataclass
class Session:
    """Composite view of a single Claude Code session, ready to render.

    ``last_event_ts`` is the hook-side timestamp (``Stop`` for idle rows,
    otherwise the latest interaction). ``age_sec`` is measured from the
    most recent interaction the user can perceive — Stop **or** a click
    that registered after Stop — so an acknowledged session's age starts
    counting from the click, not from when the thread finished.
    """

    id: str
    hook_state: str
    group: RenderGroup
    last_event_ts: int
    age_sec: int
    title: str
    project: str
    git_branch: str
    cwd: str
    entrypoint: str
    #: ``input + cache_creation + cache_read`` from the last assistant event's
    #: ``usage`` block — i.e. how many tokens the live context window currently
    #: holds. ``None`` when the transcript has no parseable usage block yet
    #: (very young session) so the submenu line can be skipped instead of
    #: showing a meaningless "100% — 0k/200k".
    context_used: int | None = None
    #: How long the session has been in its current ``working`` / ``waiting``
    #: state, in seconds. ``0`` for idle sessions (or when the hook hasn't
    #: stamped a transition yet) — :attr:`right_label` only consults this
    #: for the active states, so the value is meaningless otherwise.
    state_duration_sec: int = 0
    #: One-line summary of the last assistant ``tool_use`` chunk
    #: (e.g. ``"Read: main.py"``, ``"Bash: pytest"``). Empty when the
    #: tail of the transcript has no parseable tool call — used as the
    #: hover tooltip on the main row so a quick glance answers
    #: "what is Claude doing right now?".
    last_tool_use: str = ""
    #: Model string from the latest assistant event in the transcript
    #: (e.g. ``claude-opus-4-7``). ``None`` for older transcripts whose
    #: tail has no parseable ``"model":"..."`` match — :func:`_model_badge`
    #: then falls through to the ⓜ glyph and the submenu model row is
    #: omitted. Drives both the per-row badge and the submenu line.
    model: str | None = None
    #: Subagents (``Task`` spawns) attached to this parent session,
    #: ordered by ``state_since`` ascending so the oldest is first. Empty
    #: when the session has never spawned a subagent (the common case).
    #: A snapshot here means the row exists in
    #: :data:`SUBAGENTS_SIDECAR_PATH` — it might still be working, stopped
    #: recently, or stopped long ago but inside the per-row fresh window.
    #: ``render.build_session`` filters / promotes these before render.
    subagents: tuple[SubagentSnapshot, ...] = ()
    #: ``True`` when the session's ``cwd`` is a git *worktree* checkout
    #: (``.git`` is a file of the form ``gitdir: …`` rather than a
    #: directory). Surfaced as a green branch line in the submenu to signal
    #: that the agent's changes are isolated from the main checkout.
    #: Computed once in :func:`render.build_session`.
    is_worktree: bool = False
    #: ``True`` when two or more *active* sessions share the same non-empty
    #: ``cwd`` — i.e. they're stepping on each other in the same folder.
    #: Set by :func:`render.collect_sessions` after the full list is built,
    #: so it can't be derived from a single session in isolation. Surfaced
    #: as a red ``⚠`` branch line in the submenu.
    cwd_collision: bool = False

    @property
    def live_subagent_count(self) -> int:
        """How many subagents are still ``working`` right now.

        Drives the ``🤖×N`` badge on the row. Snapshots with ``state ==
        stopped`` aren't counted even when they're inside the fresh
        window — the badge answers "what's running", the submenu block
        answers "what just finished".
        """
        return sum(1 for s in self.subagents if s.is_live)

    @property
    def right_label(self) -> str:
        """Plain-text status shown on the right side of the row.

        The bullet colour already encodes "working" / "waiting", so the
        right-hand text carries the *duration* of the current state instead
        of repeating it as a word — `3m` next to a yellow dot reads as "has
        been working for 3 minutes". Idle rows keep their "time since last
        interaction" reading.
        """
        lang = _lang()
        if self.hook_state == "waiting":
            duration = _humanize_age(self.state_duration_sec, lang)
            return _t_for("label.blocked", lang, duration=duration)
        if self.hook_state in ACTIVE_HOOK_STATES:
            return _humanize_age(self.state_duration_sec, lang)
        return _humanize_age(self.age_sec, lang)

    @property
    def right_label_ansi(self) -> str:
        """:attr:`right_label` wrapped in an ANSI colour escape.

        Urgent states get bold yellow/red; idle states pick green / cyan /
        dim grey depending on the bucket. The escapes only render when the
        row also carries ``ansi=true``.
        """
        if self.hook_state == "working":
            color = _ANSI_WORKING
        elif self.hook_state == "waiting":
            color = _ANSI_WAITING
        elif self.group is RenderGroup.FRESH:
            color = _ANSI_FRESH
        elif self.group is RenderGroup.ACKNOWLEDGED:
            color = _ANSI_ACK
        else:
            color = _ANSI_STALE
        return f"{color}{self.right_label}{_ANSI_RESET}"


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #

_CMD_MSG_RE = re.compile(r"<command-message>(.*?)</command-message>", re.DOTALL)
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_GIT_BRANCH_RE = re.compile(rb'"gitBranch":"([^"]*)"')
#: Captures the three additive components of the live context window from a
#: ``"usage":{…}`` block on an assistant event. ``[^}]*?`` keeps the match
#: inside the outer object — the first three fields always appear *before*
#: ``server_tool_use`` (which introduces a nested ``{``), so non-greedy
#: bracket-class repetition is enough and we don't need a real JSON parser.
_USAGE_BLOCK_RE = re.compile(
    rb'"usage":\{[^}]*?"input_tokens":(\d+)[^}]*?'
    rb'"cache_creation_input_tokens":(\d+)[^}]*?'
    rb'"cache_read_input_tokens":(\d+)'
)

#: Captures the ``model`` of the most recent event in a JSONL transcript
#: tail. The same tail window that backs :data:`_USAGE_BLOCK_RE` catches
#: this signal — Claude Code writes ``"model":"..."`` next to every
#: assistant event's ``usage`` block — so the read cost is shared.
#:
#: ``[^"]+`` is loose on purpose: we want to capture non-Claude provider
#: strings too (OpenRouter, custom endpoints) so the submenu row can
#: surface them; :func:`_model_badge` maps those to the ⓜ fallback.
_MODEL_RE = re.compile(rb'"model":"([^"]+)"')


#: Prefix → badge glyph for Claude model families. Order matters only for
#: documentation; prefixes are mutually exclusive in the current API.
_MODEL_FAMILY_BADGES: tuple[tuple[str, str], ...] = (
    ("claude-opus-", "ⓞ"),
    ("claude-sonnet-", "ⓢ"),
    ("claude-haiku-", "ⓗ"),
)

#: Fallback badge for non-Claude providers and unparseable models. Spec
#: 0004 § Model badge & submenu row.
_MODEL_FALLBACK_BADGE = "ⓜ"


def _model_badge(model: str | None, default_model: str | None) -> str:
    """Return the inline badge for a session's model, or ``""`` when the
    badge should be suppressed.

    Rules from `docs/specs/0004-subagent-grouping.md` § model surface:

    * ``model is None`` (older JSONL with no parseable ``"model":"..."``)
      → fall back to ⓜ so the badge is never silently absent.
    * ``model == default_model`` → suppress (the badge is a *difference*
      marker; the user's default needs no marker).
    * Family-prefix match → the family glyph.
    * Otherwise → ⓜ (OpenRouter, custom endpoint, anything we don't
      recognise).

    When ``default_model is None`` (the user has no ``model`` field in
    either settings file) every match falls through to the family
    glyph — “safe degradation”, so a misconfigured user is never
    surprised by an *absent* badge.
    """
    if model is None:
        return _MODEL_FALLBACK_BADGE
    if default_model and model == default_model:
        return ""
    for prefix, badge in _MODEL_FAMILY_BADGES:
        if model.startswith(prefix):
            return badge
    return _MODEL_FALLBACK_BADGE


def _default_model_for(cwd: str) -> str | None:
    """Resolve the user's default Claude model for a session in ``cwd``.

    Reads ``model`` from ``~/.claude/settings.json``, then overlays
    ``<cwd>/.claude/settings.local.json`` when that file exists and
    carries the field. Returns ``None`` when neither file declares one
    — spec 0004 treats that as "unset everywhere", which makes every
    row show a badge (safe degradation).

    Cheap: both files are tiny and we only touch them on tick that
    has at least one renderable session. Bad JSON / unreadable file /
    non-string value silently fall back to ``None``.
    """
    result: str | None = None
    candidates = [HOME / ".claude" / "settings.json"]
    if cwd:
        candidates.append(Path(cwd) / ".claude" / "settings.local.json")
    for path in candidates:
        try:
            if not path.is_file():
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        value = raw.get("model")
        if isinstance(value, str) and value:
            result = value
    return result


def _shorten(text: str) -> str:
    """Collapse whitespace and truncate to :attr:`Config.title_max` with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= CONFIG.title_max:
        return text
    return text[: CONFIG.title_max - 1].rstrip() + "…"


def _shorten_head(text: str) -> str:
    """Collapse whitespace and truncate from the *start* with a leading ellipsis.

    Mirror of :func:`_shorten` but keeps the *tail*. Used for the subagent
    tool-use summary in the submenu, where the meaningful part of a long
    string is at the end — a filename in a deep path
    (``/Users/me/Projects/.../app/src/main/Foo.kt`` → ``…/app/src/main/Foo.kt``)
    or the last few args of a Bash command. Clipping the end on those values
    would hide the actual subject.
    """
    text = " ".join(text.split())
    if len(text) <= CONFIG.title_max:
        return text
    return "…" + text[-(CONFIG.title_max - 1):].lstrip()


def _humanize_age(seconds: int, lang: str = "en") -> str:
    """Render a duration in seconds as a compact, human-friendly string.

    Examples (``lang="en"``):
        ``"42s"``, ``"7m"``, ``"1h"``, ``"2h 13m"``.

    The output is *bare* — no "ago" / "назад" / "前" suffix — because every
    place we use this string already implies "ago" from context (the right
    side of a finished session row, or the "No sessions in the last X"
    empty-menu placeholder). Adding the word everywhere just makes the menu
    chattier without conveying anything.

    Defaults to English so unit tests stay locale-independent; the renderer
    passes :func:`_lang` explicitly.
    """
    if seconds < 60:
        return _t_for("age.seconds", lang, n=seconds)
    if seconds < 3600:
        return _t_for("age.minutes", lang, n=seconds // 60)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if minutes == 0:
        return _t_for("age.hours", lang, n=hours)
    return _t_for("age.hours_minutes", lang, h=hours, m=minutes)


def _format_context_warning(used: int, total: int, threshold: int) -> str:
    """Render the inline ``⚠ {pct}%`` warning for the main row, or ``""``.

    Returned string is wrapped in an ANSI colour escape: yellow while usage
    sits between ``threshold`` and 90 %, red once it crosses 90 % (the
    point at which Claude Code's CLI itself starts shouting). When the
    session is below ``threshold`` we render nothing — the user already
    sees the detailed ``{N}% — {used}k/{total}k`` line in the submenu,
    and crowding every row with a green-zone gauge would defeat the
    purpose of having a warning at all.

    Mirrors the percent-used semantics rather than ``_format_context_left``
    (percent remaining) so the threshold reads naturally as "warn at 80 %
    consumed".
    """
    if total <= 0:
        return ""
    used_clamped = max(0, used)
    pct_used = min(100, round(used_clamped / total * 100))
    if pct_used < threshold:
        return ""
    color = _ANSI_WAITING if pct_used >= 90 else _ANSI_WORKING
    return f"{color}⚠ {pct_used}%{_ANSI_RESET}"


def _format_context_left(used: int, total: int) -> str:
    """Render the per-session context-window indicator: ``"30% — 140k/200k"``.

    ``used`` is the sum of ``input_tokens + cache_creation_input_tokens +
    cache_read_input_tokens`` from the most recent assistant ``usage`` block.
    ``total`` is :attr:`Config.context_window_tokens` — surfaced as a
    parameter rather than a module-level constant so tests can pin it
    explicitly. Percent is clamped to ``[0, 100]`` so a session that has
    exceeded the nominal window (still possible in the brief window before
    Claude Code auto-compacts) reads as ``0%`` rather than a confusing
    negative.

    Number scale is rounded to the nearest thousand for a stable two-three
    digit width — the menu submenu is monospace and we don't want this row
    to jitter every tick as token counts tick up.
    """
    if total <= 0:
        return ""
    used_clamped = max(0, used)
    percent_left = max(0, min(100, round((1 - used_clamped / total) * 100)))
    used_k = round(used_clamped / 1000)
    total_k = round(total / 1000)
    return f"{percent_left}% — {used_k}k/{total_k}k"


def _classify(
    hook_state: str,
    now: int,
    stop_ts: int,
    effective_click_ts: int,
    last_event_kind: str,
) -> RenderGroup:
    """Map raw hook state + timestamps onto a :class:`RenderGroup`.

    The rule has three idle thresholds, derived from two configurable
    durations (``fresh_sec`` and ``ack_sec``):

    * The session lands in 🟢 ``FRESH`` until ``ack_ts``: the moment it
      either becomes acknowledged. That's the first click after Stop, or
      — failing any click — ``stop_ts + fresh_sec``.
    * From ``ack_ts`` to ``ack_ts + ack_sec`` it sits in 🔵
      ``ACKNOWLEDGED``. Each later click bumps ``effective_click_ts``,
      which moves ``ack_ts`` forward and restarts the timer.
    * After that it falls into ⚪ ``STALE`` until the global window evicts
      it from the menu.

    Crucially, FRESH only fires when the last hook event was an actual
    ``Stop`` — i.e. an agent turn that genuinely *ended*. Any other
    flavour of idle (a ``SessionStart`` with no following work, the
    watchdog downgrading a stuck ``working`` to ``idle``, the sidecar
    fallback for sessions with no TSV row yet) collapses the FRESH
    window to zero, so the session lands in ACKNOWLEDGED or STALE
    immediately. Without this guard, just *opening* a session in the
    IDE — which fires ``SessionStart`` — would paint the row green as
    if a turn had just completed. See CHANGELOG entry for this branch.

    ``effective_click_ts`` is ``0`` when no click happened *after* the
    last Stop. Clicks made while the session was still working are
    handled by the caller (filtered out before this is reached) so they
    don't carry over a new idle cycle.
    """
    if hook_state in ACTIVE_HOOK_STATES:
        return RenderGroup.ACTIVE
    fresh_window = CONFIG.fresh_sec if last_event_kind == "Stop" else 0
    ack_ts = effective_click_ts if effective_click_ts else stop_ts + fresh_window
    if now < ack_ts:
        return RenderGroup.FRESH
    if now < ack_ts + CONFIG.ack_sec:
        return RenderGroup.ACKNOWLEDGED
    return RenderGroup.STALE


def _clean_text(text: str) -> str:
    """Surface slash-commands as ``/name args`` and strip XML-ish wrappers.

    Claude Code stores slash-command invocations in transcripts as
    ``<command-message>name</command-message><command-args>...</command-args>``
    rather than as the literal ``/name args`` the user typed. Unwrapping that
    keeps the titles in the menu readable.
    """
    if not text:
        return ""
    if (match := _CMD_MSG_RE.search(text)) is not None:
        cmd = match.group(1).strip()
        args_match = _CMD_ARGS_RE.search(text)
        args = args_match.group(1).strip() if args_match else ""
        return f"/{cmd}" + (f" {args}" if args else "")
    return _TAG_STRIP_RE.sub("", text).strip()


def _content_to_title(content: object) -> str:
    """Flatten a JSONL user-event ``message.content`` into a single line."""
    if isinstance(content, str):
        return _clean_text(content)
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for chunk in content:
        if not isinstance(chunk, dict):
            continue
        chunk_type = chunk.get("type")
        if chunk_type == "text":
            parts.append(_clean_text(chunk.get("text", "")))
        elif chunk_type == "image":
            parts.append("[image]")
    return " ".join(part for part in (s.strip() for s in parts) if part)


def _project_name(cwd: str, fallback_dirname: str) -> str:
    """Best-effort project name: last segment of cwd, or of the slug if cwd is empty.

    The slug fallback handles sessions started before hooks were installed —
    in those cases the JSONL transcript may not carry an explicit cwd, but the
    directory name itself is a slugified path like ``-Users-name-Projects-foo``,
    so the last hyphen-delimited segment is a reasonable proxy.
    """
    if cwd:
        return Path(cwd).name
    segments = fallback_dirname.rstrip("-").split("-")
    return segments[-1] if segments else fallback_dirname


# --------------------------------------------------------------------------- #
# Quiet hours (spec 0002)                                                      #
# --------------------------------------------------------------------------- #


def _parse_quiet_window(spec: str | None) -> tuple[_dt.time, _dt.time] | None:
    """Parse ``"HH:MM-HH:MM"`` into ``(start, end)``, or ``None``.

    ``start == end`` is treated as ``None`` ("never quiet") rather than
    "always quiet" — the only legitimate way to land matching values is a
    typo, so refusing to silence the user 24/7 is the safer call. The same
    rule lives in ``hooks/_notify-common.sh``; keep them in lockstep.
    """
    if not spec:
        return None
    m = _QUIET_HOURS_RE.match(spec)
    if not m:
        return None
    sh, sm, eh, em = (int(g) for g in m.groups())
    start = _dt.time(sh, sm)
    end = _dt.time(eh, em)
    if start == end:
        return None
    return (start, end)


def _quiet_window_active(now_t: _dt.time, start: _dt.time, end: _dt.time) -> bool:
    """True iff ``now_t`` is inside ``[start, end)`` with midnight wrap.

    Half-open interval matches the hook side — a window ending at 09:00
    means 09:00 sharp is no longer quiet.
    """
    if start < end:
        return start <= now_t < end
    return now_t >= start or now_t < end


def _next_occurrence(now_dt: _dt.datetime, target: _dt.time) -> _dt.datetime:
    """Next wall-clock occurrence of ``target`` strictly after ``now_dt``."""
    candidate = now_dt.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0,
    )
    if candidate <= now_dt:
        return candidate + _dt.timedelta(days=1)
    return candidate


def quiet_status(
    now_dt: _dt.datetime,
    spec: str | None,
    paused_until: _dt.datetime | None,
    bypass_until: _dt.datetime | None = None,
) -> dict:
    """Render-ready summary of the quiet-hours state for the Tools submenu.

    Returned dict always carries ``kind`` (one of ``"off"``,
    ``"scheduled_inactive"``, ``"scheduled_active"``, ``"paused"``,
    ``"paused_and_scheduled_active"``, ``"bypassed"``) and, depending on
    the kind, some of: ``start`` / ``end`` formatted as ``"HH:MM"``,
    ``scheduled_remaining`` / ``scheduled_until_start`` /
    ``paused_remaining`` / ``bypass_remaining`` in seconds. Pure —
    callers compose the user-facing string in the renderer so i18n
    stays in one place.

    Precedence when both pause and bypass are set: ``paused`` wins.
    Pause is "do not bother me"; bypass is "do bother me even during
    quiet". Holding both is a contradiction the user resolves by
    cancelling whichever is wrong — the menu keeps showing both
    *remaining* numbers in :attr:`bypass_remaining` /
    :attr:`paused_remaining` regardless of which kind is active.
    """
    endpoints = _parse_quiet_window(spec)
    sched_active = (
        endpoints is not None and _quiet_window_active(now_dt.time(), *endpoints)
    )
    paused = paused_until is not None and paused_until > now_dt
    bypassed = (
        sched_active and bypass_until is not None and bypass_until > now_dt
    )

    info: dict = {}
    if endpoints is not None:
        start, end = endpoints
        info["start"] = start.strftime("%H:%M")
        info["end"] = end.strftime("%H:%M")
        if sched_active:
            next_end = _next_occurrence(now_dt, end)
            info["scheduled_remaining"] = int(
                (next_end - now_dt).total_seconds()
            )
        else:
            next_start = _next_occurrence(now_dt, start)
            info["scheduled_until_start"] = int(
                (next_start - now_dt).total_seconds()
            )
    if paused:
        info["paused_remaining"] = int((paused_until - now_dt).total_seconds())
    if bypassed:
        info["bypass_remaining"] = int((bypass_until - now_dt).total_seconds())

    if paused and sched_active:
        info["kind"] = "paused_and_scheduled_active"
    elif paused:
        info["kind"] = "paused"
    elif bypassed:
        info["kind"] = "bypassed"
    elif sched_active:
        info["kind"] = "scheduled_active"
    elif endpoints is not None:
        info["kind"] = "scheduled_inactive"
    else:
        info["kind"] = "off"
    return info


def is_quiet_now(
    now_dt: _dt.datetime,
    spec: str | None,
    paused_until: _dt.datetime | None,
    bypass_until: _dt.datetime | None = None,
) -> bool:
    """Quick predicate for hook-parity checks (tests, doctor)."""
    status = quiet_status(now_dt, spec, paused_until, bypass_until)
    return status["kind"] in (
        "scheduled_active", "paused", "paused_and_scheduled_active",
    )
