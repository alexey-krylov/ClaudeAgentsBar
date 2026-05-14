#!/usr/bin/env python3
# <xbar.title>Claude Agents Bar</xbar.title>
# <xbar.title.ru>Claude-агенты</xbar.title.ru>
# <xbar.title.zh>Claude 代理栏</xbar.title.zh>
# <xbar.title.fr>Claude Agents Bar</xbar.title.fr>
# <xbar.title.de>Claude Agents Bar</xbar.title.de>
# <xbar.title.it>Claude Agents Bar</xbar.title.it>
# <xbar.title.vi>Claude Agents Bar</xbar.title.vi>
# <xbar.version>1.0</xbar.version>
# <xbar.author>Alexey Krylov</xbar.author>
# <xbar.author.github>alexey-krylov/ClaudeAgentsBar</xbar.author.github>
# <xbar.desc>Live status of Claude Code sessions across all projects.</xbar.desc>
# <xbar.desc.ru>Статус сессий Claude Code в реальном времени по всем проектам.</xbar.desc.ru>
# <xbar.desc.zh>在所有项目中实时显示 Claude Code 会话状态。</xbar.desc.zh>
# <xbar.desc.fr>État en direct des sessions Claude Code sur tous les projets.</xbar.desc.fr>
# <xbar.desc.de>Live-Status der Claude-Code-Sitzungen aller Projekte.</xbar.desc.de>
# <xbar.desc.it>Stato live delle sessioni Claude Code in tutti i progetti.</xbar.desc.it>
# <xbar.desc.vi>Trạng thái phiên Claude Code trên mọi dự án theo thời gian thực.</xbar.desc.vi>
# <xbar.dependencies>python3</xbar.dependencies>
# <xbar.abouturl>https://github.com/alexey-krylov/ClaudeAgentsBar</xbar.abouturl>
# <swiftbar.hideAbout>false</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>false</swiftbar.hideDisablePlugin>

"""SwiftBar plugin that surfaces live Claude Code session state in the macOS menu bar.

Architecture
------------
The plugin is stateless. On every tick (default: 5 s) SwiftBar invokes the
script, which rebuilds the entire menu from the transcripts plus three
sidecar files:

* ``~/.claude/projects/<slug>/<sid>.jsonl`` — transcripts Claude Code writes
  for every session. We read just enough of each to extract the AI-generated
  title and the session's initial cwd.
* ``~/.claude/agent-state.tsv`` — state sidecar maintained by
  ``hooks/agent-state.sh``, registered as a Claude Code hook. One row per
  session, holding the latest state (``waiting`` / ``working`` / ``idle``),
  event timestamp, and cwd.
* ``~/.claude/agent-state.clicks`` — ``{session_id: click_ts}`` sidecar
  written by ``bin/open-session.sh``. Drives the 🟢 *fresh* → 🔵 *acknowledged*
  promotion and restarts the stale countdown on each click.
* ``~/.claude/agent-state.dismiss`` — single-timestamp cutoff written by
  ``bin/forget-sessions.sh``; sessions whose last activity is at or before
  it are filtered out until a fresh hook event surfaces them again.

Each tick we merge those into :class:`Session` records, classify them into a
:class:`RenderGroup` (ACTIVE / FRESH / ACKNOWLEDGED / STALE) for presentation,
sort by group then recency, and emit SwiftBar-formatted lines on stdout.
Failures degrade to a ⚠️ indicator rather than crashing — SwiftBar would
otherwise show a stack trace in the menu.

User-tunable knobs (window size, fresh / ack thresholds, menu-bar icon, …)
live in ``$XDG_CONFIG_HOME/claude-agents-bar/config.json`` and are loaded
once into the :class:`Config` singleton. See ``config.example.json`` and
the README.

The plugin also exposes a single non-render subcommand: ``--ack-fresh``
bulk-promotes every currently-FRESH session to ACKNOWLEDGED. It's wired up
from ``bin/ack-fresh.sh`` (Tools → Acknowledge all).

This file is intentionally self-contained: standard library only, single
process, no daemon. Side effects are writing to ``stdout``, opportunistic
garbage collection of the two TSV sidecars, and (under ``--ack-fresh``)
appending to the clicks sidecar.
"""

from __future__ import annotations

import base64
import enum
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

# --------------------------------------------------------------------------- #
# Paths and structural constants                                               #
# --------------------------------------------------------------------------- #

HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"
SIDECAR_PATH = HOME / ".claude" / "agent-state.tsv"

#: Cutoff timestamp set by ``bin/forget-sessions.sh`` (the *Tools → Forget
#: all sessions* action). Sessions whose latest activity is at or before this
#: moment are filtered out of the rendered menu; live sessions reappear on
#: their next hook event.
DISMISS_PATH = HOME / ".claude" / "agent-state.dismiss"

#: ``{session_id: click_ts}`` sidecar maintained by ``bin/open-session.sh``.
#: One row per session, last click wins. Used to decide whether an idle
#: session has been "acknowledged" by the user — see :class:`RenderGroup`.
CLICKS_PATH = HOME / ".claude" / "agent-state.clicks"

#: Mutex on :data:`CLICKS_PATH`, shared between plugin (gc) and the click
#: recorder. Same ``mkdir``-based scheme as the main sidecar lock.
_CLICKS_LOCK_DIR = CLICKS_PATH.with_suffix(CLICKS_PATH.suffix + ".lock.d")

#: ``{session_id: forget_ts}`` sidecar maintained by ``bin/forget-session.sh``
#: (the per-row *Forget* action). A session whose ``last_event_ts`` is at or
#: before its ``forget_ts`` is filtered out of the menu — same cutoff semantics
#: as :data:`DISMISS_PATH` but per-session instead of global. A fresh hook
#: event or click pushes ``last_event_ts`` past the cutoff and the row
#: re-surfaces, which is the intended escape hatch if the user wants the row
#: back. Use the per-row *Delete session…* action for permanent removal.
FORGET_PATH = HOME / ".claude" / "agent-state.forget"

#: Mutex on :data:`FORGET_PATH`, shared between plugin (gc) and
#: ``bin/forget-session.sh``. Same ``mkdir``-based scheme as the other sidecar
#: locks.
_FORGET_LOCK_DIR = FORGET_PATH.with_suffix(FORGET_PATH.suffix + ".lock.d")

#: Bytes mmap'd from the JSONL tail when searching for the most recent
#: ``gitBranch`` — bounded so huge transcripts (base64 attachments) stay cheap.
#: The same window is used by :func:`last_usage_tokens` to find the freshest
#: ``"usage":{…}`` block; both signals live near the file end because Claude
#: Code appends events sequentially.
JSONL_TAIL_BYTES = 64 * 1024

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

#: States that ``hooks/agent-state.sh`` may write.
HOOK_STATES = frozenset({"waiting", "working", "idle"})

#: The subset of :data:`HOOK_STATES` that mean "session is in flight" — i.e.
#: the right-hand label should show duration of the current state rather
#: than time-since-last-interaction. ``RenderGroup.ACTIVE`` deliberately
#: conflates these two (see its docstring); this is the same conflation at
#: the per-state level.
ACTIVE_HOOK_STATES = frozenset({"working", "waiting"})

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

#: Directory used as a mutex on the sidecar by both the plugin (cleanup) and
#: ``hooks/agent-state.sh`` (writes). ``mkdir`` is atomic on every POSIX
#: filesystem and doesn't need ``util-linux``, unlike ``flock``.
_SIDECAR_LOCK_DIR = SIDECAR_PATH.with_suffix(SIDECAR_PATH.suffix + ".lock.d")


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

        take("window_minutes", "window_sec", float, lambda m: int(m * 60))
        take("fresh_minutes", "fresh_sec", float, lambda m: int(m * 60))
        take("ack_minutes", "ack_sec", float, lambda m: int(m * 60))
        take("watchdog_seconds", "watchdog_sec", int)
        take("title_max", "title_max", int)
        take("menubar_icon", "menubar_icon", str)
        take("menubar_icon_fallback", "menubar_icon_fallback", str)
        take("editor_url_scheme", "editor_url_scheme", str)
        take("language", "language", str)
        # Positive-int constraint: 0 or negative would make _format_context_left
        # return an empty string and the row would vanish silently. Better to
        # warn loudly and keep the 1M default.
        def _require_positive(n: int) -> int:
            if n <= 0:
                raise ValueError("must be > 0")
            return n
        take("context_window_tokens", "context_window_tokens", int, _require_positive)
        # JSON booleans are native Python bool after json.loads; bool("false")
        # == True so we can't use the generic take() helper here.
        if "compact" in data and isinstance(data["compact"], bool):
            coerced["compact"] = data["compact"]

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


# --------------------------------------------------------------------------- #
# Localization                                                                 #
# --------------------------------------------------------------------------- #

#: Where the per-locale JSON tables live, colocated with this script.
#: ``Path(__file__).resolve()`` follows the install-time symlink so the
#: directory next to the *source* file is what we read — installing with a
#: symlink (which ``install.sh`` does) keeps the JSON files editable in
#: place without copying them to the SwiftBar plugins folder.
_LOCALES_DIR = Path(__file__).resolve().parent / "locales"


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
class TranscriptMeta:
    """Subset of a JSONL transcript needed to render a menu row."""

    ai_title: str = ""
    raw_title: str = ""
    cwd: str = ""
    entrypoint: str = ""

    @property
    def display_title(self) -> str:
        """Title to show in the UI: prefer the AI summary, fall back to raw."""
        return _shorten(self.ai_title or self.raw_title)


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

    @property
    def right_label(self) -> str:
        """Plain-text status shown on the right side of the row.

        The bullet colour already encodes "working" / "waiting", so the
        right-hand text carries the *duration* of the current state instead
        of repeating it as a word — `3m` next to a yellow dot reads as "has
        been working for 3 minutes". Idle rows keep their "time since last
        interaction" reading.
        """
        if self.hook_state in ACTIVE_HOOK_STATES:
            return _humanize_age(self.state_duration_sec, _lang())
        return _humanize_age(self.age_sec, _lang())

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


def _shorten(text: str) -> str:
    """Collapse whitespace and truncate to :attr:`Config.title_max` with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= CONFIG.title_max:
        return text
    return text[: CONFIG.title_max - 1].rstrip() + "…"


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

    ``effective_click_ts`` is ``0`` when no click happened *after* the
    last Stop. Clicks made while the session was still working are
    handled by the caller (filtered out before this is reached) so they
    don't carry over a new idle cycle.
    """
    if hook_state in ACTIVE_HOOK_STATES:
        return RenderGroup.ACTIVE
    ack_ts = effective_click_ts if effective_click_ts else stop_ts + CONFIG.fresh_sec
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
# Source readers                                                               #
# --------------------------------------------------------------------------- #


def read_dismiss_ts() -> int:
    """Return the *Forget all sessions* cutoff, or ``0`` when unset.

    A missing, empty, or unparseable file means "no cutoff" — we'd rather
    show every live session than hide them all because of a corrupt byte.
    """
    try:
        return int(DISMISS_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def read_sidecar() -> dict[str, HookSnapshot]:
    """Load ``agent-state.tsv`` into a ``{session_id: HookSnapshot}`` map.

    The TSV is written by hooks under :data:`_SIDECAR_LOCK_DIR`, but malformed
    rows can still appear (a half-written write that crashed, a leftover from
    a previous schema, etc.) so we treat every row as untrusted and silently
    skip anything that doesn't parse.
    """
    if not SIDECAR_PATH.exists():
        return {}
    try:
        raw = SIDECAR_PATH.read_text(encoding="utf-8", errors="replace")
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
    if PROJECTS_DIR.exists():
        for project_dir in PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl in project_dir.glob("*.jsonl"):
                live_ids.add(jsonl.stem)
    return live_ids


def _stale_sidecar_ids(snapshots: dict[str, HookSnapshot], now: int) -> set[str]:
    """Return session ids whose row should be removed from the sidecar.

    A row is stale when:

    * its transcript no longer exists on disk (the session was deleted out
      of band — typically by ``bin/delete-session.sh`` or by the user
      directly), or
    * its last event is older than :attr:`Config.window_sec` (the row will
      never be rendered again from this point on, so it's pure overhead).
    """
    live_ids = _live_session_ids()
    stale: set[str] = set()
    for sid, snap in snapshots.items():
        if sid not in live_ids:
            stale.add(sid)
        elif now - snap.last_event_ts > CONFIG.window_sec:
            stale.add(sid)
    return stale


def gc_sidecar(stale: set[str]) -> None:
    """Drop the given session ids from the sidecar, atomically.

    Takes the same mutex that ``hooks/agent-state.sh`` uses, so concurrent
    hook writes can't race with our rewrite. Cheap when there's nothing to
    drop: returns immediately without touching the filesystem.
    """
    if not stale or not SIDECAR_PATH.exists():
        return
    with _sidecar_lock():
        # Re-read under the lock so we operate on the freshest snapshot.
        try:
            raw = SIDECAR_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        kept = [
            line
            for line in raw.splitlines()
            if not (line.split("\t", 1)[:1] and line.split("\t", 1)[0] in stale)
        ]
        if len(kept) == len(raw.splitlines()):
            return  # Nothing to do — the rows we wanted to drop are already gone.
        tmp = SIDECAR_PATH.with_suffix(SIDECAR_PATH.suffix + f".{os.getpid()}.tmp")
        try:
            tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            tmp.replace(SIDECAR_PATH)
        except OSError as exc:
            _warn(f"sidecar gc failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass


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
    return _mkdir_lock(_SIDECAR_LOCK_DIR, timeout_sec)


def _clicks_lock(timeout_sec: float = 2.0):
    """Mutex shared with ``bin/open-session.sh`` for ``agent-state.clicks``."""
    return _mkdir_lock(_CLICKS_LOCK_DIR, timeout_sec)


def _forget_lock(timeout_sec: float = 2.0):
    """Mutex shared with ``bin/forget-session.sh`` for ``agent-state.forget``."""
    return _mkdir_lock(_FORGET_LOCK_DIR, timeout_sec)


def read_clicks() -> dict[str, int]:
    """Load the click sidecar into a ``{session_id: click_ts}`` map.

    Only the latest click per session is kept on disk (the recorder
    rewrites the row), so this is a straight dict load. Any unparseable
    row is dropped silently — same fail-open stance as :func:`read_sidecar`.
    """
    if not CLICKS_PATH.exists():
        return {}
    try:
        raw = CLICKS_PATH.read_text(encoding="utf-8", errors="replace")
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
    if not FORGET_PATH.exists():
        return {}
    try:
        raw = FORGET_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return _parse_clicks(raw)


def gc_forget(stale: set[str]) -> None:
    """Drop the given session ids from the forget sidecar, atomically.

    Mirrors :func:`gc_clicks`. We only prune rows whose transcript is gone —
    a forgotten row whose JSONL still exists must stay, otherwise the
    sessions would silently re-surface.
    """
    if not stale or not FORGET_PATH.exists():
        return
    with _forget_lock():
        try:
            raw = FORGET_PATH.read_text(encoding="utf-8", errors="replace")
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
        tmp = FORGET_PATH.with_suffix(FORGET_PATH.suffix + f".{os.getpid()}.tmp")
        try:
            tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            tmp.replace(FORGET_PATH)
        except OSError as exc:
            _warn(f"forget gc failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass


def gc_clicks(stale: set[str]) -> None:
    """Drop the given session ids from the click sidecar, atomically.

    Mirrors :func:`gc_sidecar` but on a simpler two-column file. Cheap
    when there's nothing to drop.
    """
    if not stale or not CLICKS_PATH.exists():
        return
    with _clicks_lock():
        try:
            raw = CLICKS_PATH.read_text(encoding="utf-8", errors="replace")
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
        tmp = CLICKS_PATH.with_suffix(CLICKS_PATH.suffix + f".{os.getpid()}.tmp")
        try:
            tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            tmp.replace(CLICKS_PATH)
        except OSError as exc:
            _warn(f"clicks gc failed: {exc}")
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
                CLICKS_PATH.read_text(encoding="utf-8", errors="replace")
                if CLICKS_PATH.exists()
                else ""
            )
        except OSError:
            return 0
        merged = _parse_clicks(raw)
        for sid in fresh_sids:
            merged[sid] = now
        lines = [f"{sid}\t{ts}" for sid, ts in merged.items()]
        tmp = CLICKS_PATH.with_suffix(CLICKS_PATH.suffix + f".{os.getpid()}.tmp")
        try:
            CLICKS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            tmp.replace(CLICKS_PATH)
        except OSError as exc:
            _warn(f"ack-fresh write failed: {exc}")
            try:
                tmp.unlink()
            except OSError:
                pass
            return 0

    return len(fresh_sids)


def read_transcript_meta(jsonl_path: Path) -> TranscriptMeta:
    """Extract title and cwd from a session transcript.

    We prefer the AI-generated ``ai-title`` event (the same value the VSCode
    sidebar displays). It's emitted right after the first turn, so capping
    the scan at :data:`JSONL_TITLE_SCAN_BYTES` finds it in essentially all
    real-world transcripts while keeping the per-tick cost bounded regardless
    of how much base64 attachment data later events drag in.

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
    return TranscriptMeta(
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


def fallback_git_branch_from_jsonl(jsonl_path: Path) -> str:
    """Latest ``gitBranch`` seen in the JSONL tail, or empty string.

    Used when the session's cwd is no longer a git repo (moved, deleted,
    unmounted) so we still surface some branch context to the user.
    """
    try:
        size = jsonl_path.stat().st_size
        if size == 0:
            return ""
        with jsonl_path.open("rb") as f:
            f.seek(max(0, size - JSONL_TAIL_BYTES))
            data = f.read()
    except OSError:
        return ""
    matches = _GIT_BRANCH_RE.findall(data)
    if not matches:
        return ""
    return matches[-1].decode("utf-8", errors="replace")


def last_usage_tokens(jsonl_path: Path) -> int | None:
    """Return the live context size from the freshest ``usage`` block, or ``None``.

    Reads only the trailing :data:`JSONL_TAIL_BYTES` because Claude Code
    appends events sequentially — the newest assistant turn (with the most
    up-to-date usage block) is always at the end. Bounding the read keeps
    this O(1) regardless of how large the transcript has grown.

    The returned value is ``input_tokens + cache_creation_input_tokens +
    cache_read_input_tokens`` from the last matched block — the same sum
    Claude Code uses to gauge "how full is the window right now". Cache
    reads dominate this number on most turns; that is expected, and is
    why the result *can't* meaningfully be aggregated across all events
    (cache contents repeat from one turn to the next).

    ``None`` is returned for empty files, unreadable files, or transcripts
    whose tail doesn't yet contain a parseable usage block (a session that
    only has the user's first prompt, no assistant reply). Callers should
    omit the indicator row in that case rather than rendering ``0k``.
    """
    try:
        size = jsonl_path.stat().st_size
        if size == 0:
            return None
        with jsonl_path.open("rb") as f:
            f.seek(max(0, size - JSONL_TAIL_BYTES))
            data = f.read()
    except OSError:
        return None
    matches = _USAGE_BLOCK_RE.findall(data)
    if not matches:
        return None
    inp, cache_creation, cache_read = (int(x) for x in matches[-1])
    return inp + cache_creation + cache_read


# --------------------------------------------------------------------------- #
# Composition                                                                  #
# --------------------------------------------------------------------------- #


def iter_active_jsonls(now: int) -> Iterator[Path]:
    """Yield JSONL transcripts touched within the last :attr:`Config.window_sec`."""
    if not PROJECTS_DIR.exists():
        return
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                mtime = int(jsonl.stat().st_mtime)
            except OSError:
                continue
            if now - mtime <= CONFIG.window_sec:
                yield jsonl


def build_session(
    jsonl: Path,
    sidecar: dict[str, HookSnapshot],
    clicks: dict[str, int],
    now: int,
) -> Session:
    """Assemble a :class:`Session` from sidecar state, clicks, transcript and live git."""
    try:
        jsonl_mtime = int(jsonl.stat().st_mtime)
    except OSError:
        jsonl_mtime = 0

    snapshot = sidecar.get(jsonl.stem)
    if snapshot is not None:
        last_ts = snapshot.last_event_ts
        hook_state = snapshot.state
        sidecar_cwd = snapshot.cwd
        state_since = snapshot.state_since
    else:
        last_ts = jsonl_mtime
        hook_state = "idle"
        sidecar_cwd = ""
        state_since = jsonl_mtime

    # Liveness signal: Claude Code writes the transcript continuously while
    # running (assistant streams, tool results, …). If the process is killed,
    # the file freezes the instant it dies — even when the TSV still says
    # ``working`` because no ``Stop`` hook ever fired. Treating the JSONL
    # mtime as the floor for ``last_ts`` lets the watchdog catch crashes
    # within ``watchdog_sec`` regardless of which side went stale first.
    liveness_ts = max(last_ts, jsonl_mtime)
    if hook_state == "working" and (now - liveness_ts) > CONFIG.watchdog_sec:
        hook_state = "idle"
        last_ts = liveness_ts

    # "Effective click" = a click that landed after the most recent Stop.
    # Clicks made while the session was still running don't carry over: the
    # next Stop resets the unread state, so we ignore them here.
    click_ts = clicks.get(jsonl.stem, 0)
    effective_click_ts = click_ts if click_ts > last_ts else 0

    # Age is measured against the user-facing "last interaction" — either
    # the Stop event or the most recent click, whichever is later. That way
    # an acknowledged session's "Xm ago" reads as time-since-click, which
    # is what the user actually cares about.
    interaction_ts = max(last_ts, effective_click_ts)
    if hook_state in ACTIVE_HOOK_STATES:
        # While the session is in flight the JSONL mtime is the freshest
        # signal — it ticks with every streamed token.
        interaction_ts = max(interaction_ts, jsonl_mtime)
    age = now - interaction_ts

    meta = read_transcript_meta(jsonl)
    cwd = sidecar_cwd or meta.cwd
    branch = current_git_branch(cwd) or fallback_git_branch_from_jsonl(jsonl)
    context_used = last_usage_tokens(jsonl)

    state_duration_sec = (
        max(0, now - state_since) if hook_state in ACTIVE_HOOK_STATES else 0
    )

    return Session(
        id=jsonl.stem,
        hook_state=hook_state,
        group=_classify(hook_state, now, last_ts, effective_click_ts),
        last_event_ts=interaction_ts,
        age_sec=age,
        title=meta.display_title or _t("title.no_title"),
        project=_project_name(cwd, jsonl.parent.name),
        git_branch=branch,
        cwd=cwd,
        entrypoint=meta.entrypoint,
        context_used=context_used,
        state_duration_sec=state_duration_sec,
    )


def collect_sessions(now: int) -> list[Session]:
    """Build all live sessions, filtered + sorted ready for rendering.

    Side effect: both sidecars (state TSV and clicks TSV) are
    opportunistically garbage-collected — rows whose transcript is gone
    or whose last event has fallen out of :attr:`Config.window_sec` are
    stripped. Cheap when there's nothing to drop; we only take the lock
    + rewrite when stale rows exist.
    """
    sidecar = read_sidecar()
    clicks = read_clicks()
    forget = read_forget()
    if stale := _stale_sidecar_ids(sidecar, now):
        gc_sidecar(stale)
        for sid in stale:
            sidecar.pop(sid, None)
    # Click and forget rows for sessions whose transcript no longer exists
    # are pure overhead. The transcript — not the state sidecar — is
    # authoritative: plenty of legitimate sessions have a JSONL but no TSV row.
    if clicks or forget:
        live_ids = _live_session_ids()
        orphan_clicks = set(clicks) - live_ids
        if orphan_clicks:
            gc_clicks(orphan_clicks)
            for sid in orphan_clicks:
                clicks.pop(sid, None)
        orphan_forget = set(forget) - live_ids
        if orphan_forget:
            gc_forget(orphan_forget)
            for sid in orphan_forget:
                forget.pop(sid, None)
    sessions = [
        build_session(p, sidecar, clicks, now) for p in iter_active_jsonls(now)
    ]
    # Drop headless sessions unconditionally — they're scripted runs the
    # user can't usefully interact with, and they otherwise clutter the menu.
    sessions = [s for s in sessions if _is_interactive(s)]
    if dismiss_ts := read_dismiss_ts():
        # Hide anything that hasn't moved since the user clicked
        # *Forget all sessions*. ``last_event_ts`` is the last interaction
        # (Stop, click, or streamed token while in flight), so this picks
        # up everything we'd otherwise show.
        sessions = [s for s in sessions if s.last_event_ts > dismiss_ts]
    if forget:
        # Per-row *Forget* uses the same cutoff semantics as the global
        # dismiss — a session re-surfaces if it gets a fresh event past
        # its own ``forget_ts``.
        sessions = [
            s for s in sessions
            if s.id not in forget or s.last_event_ts > forget[s.id]
        ]
    sessions.sort(key=lambda s: (s.group.order, -s.last_event_ts))
    return sessions


def _is_interactive(session: Session) -> bool:
    """True when a session looks like one a human is typing into.

    An unset ``entrypoint`` is treated as interactive — older or malformed
    transcripts may not carry the field, and we'd rather show a session that
    might be real than silently swallow it.
    """
    return not session.entrypoint or session.entrypoint in INTERACTIVE_ENTRYPOINTS


# --------------------------------------------------------------------------- #
# SwiftBar rendering                                                           #
# --------------------------------------------------------------------------- #


def render(sessions: list[Session]) -> None:
    """Emit the full SwiftBar menu — title line, dropdown, footer."""
    counts = {
        RenderGroup.ACTIVE: 0,
        RenderGroup.FRESH: 0,
        RenderGroup.ACKNOWLEDGED: 0,
    }
    for s in sessions:
        if s.group in counts:
            counts[s.group] += 1
    _print_menubar(counts)
    print("---")

    if not sessions:
        duration = _humanize_age(CONFIG.window_sec, _lang())
        print(f"{_t('menu.no_sessions', duration=duration)} | color=gray")
        print("---")
        _print_footer()
        return

    last_group: RenderGroup | None = None
    for session in sessions:
        if last_group is not None and last_group is not session.group:
            print("---")
        last_group = session.group
        _print_session_row(session)

    print("---")
    _print_footer()


#: Order of counters in the menu-bar title — most-urgent first. STALE is
#: deliberately omitted: it would always be the largest number and would
#: drown out the urgent buckets.
_MENUBAR_COUNTER_ORDER: tuple[RenderGroup, ...] = (
    RenderGroup.ACTIVE,
    RenderGroup.FRESH,
    RenderGroup.ACKNOWLEDGED,
)


_COMPACT_ANSI: dict[RenderGroup, str] = {
    RenderGroup.ACTIVE: _ANSI_ACTIVE_BAR,
    RenderGroup.FRESH: _ANSI_FRESH_BAR,
    RenderGroup.ACKNOWLEDGED: _ANSI_ACK_BAR,
}


def _print_menubar(counts: dict[RenderGroup, int]) -> None:
    """Emit the menu-bar title line: icon plus coloured counters.

    Counters are omitted when zero so the bar doesn't carry empty labels.
    When nothing is active we dim the entire title so it visually recedes
    on the bar instead of demanding attention.

    In compact mode (``CONFIG.compact``) the Claude icon is suppressed and
    the wide emoji circles (🟡🟢🔵) are replaced with ANSI-coloured ``●``
    bullets — e.g. ``●2 ●1 ●3`` — roughly halving the menu-bar footprint.
    """
    if CONFIG.compact:
        parts: list[str] = []
        for group in _MENUBAR_COUNTER_ORDER:
            n = counts.get(group, 0)
            if n:
                parts.append(f"{_COMPACT_ANSI[group]}●{_ANSI_RESET}{n}")
        if parts:
            print(f"{' '.join(parts)} | ansi=true")
        else:
            print("● | color=#888888")
        return

    counter_parts: list[str] = []
    for group in _MENUBAR_COUNTER_ORDER:
        n = counts.get(group, 0)
        if n:
            counter_parts.append(f"{group.icon}{n}")
    icon_param, icon_glyph = _menubar_icon_pieces()
    if counter_parts:
        title = f"{icon_glyph} {' '.join(counter_parts)}".strip()
        print(f"{title}{icon_param}".rstrip())
    else:
        print(f"{icon_glyph}{icon_param} | color=#888888".rstrip())


#: Logical height in points for menubar template/image icons. macOS's menu
#: bar is ~22pt tall; SwiftBar passes our base64 straight to
#: ``NSImage(data:)`` which treats the smallest representation as the
#: logical size. :func:`_resized_menubar_image` builds a multi-rep TIFF
#: at 1× / 2× / 3× this value so retina displays get crisp rendering.
_MENUBAR_ICON_PT = 22

#: Pixel-density multipliers baked into the cached TIFF. 1× covers
#: non-retina, 2× the common retina display, 3× iPhone-style ProMotion.
#: ``tiffutil`` infers densities from each rep's dimensions relative to
#: the smallest, so listing them in ascending order keeps the labels right.
_MENUBAR_ICON_SCALES = (1, 2, 3)


def _menubar_cache_dir() -> Path:
    """Resolve ``$XDG_CACHE_HOME/claude-agents-bar`` (or ``~/.cache/...``)."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else HOME / ".cache"
    return base / "claude-agents-bar"


def _resized_menubar_image(src: Path) -> Path:
    """Return a path to a menubar-sized multi-rep TIFF of ``src``, cached on disk.

    Single-rep PNGs at 22×22 look pixelated on retina because NSImage has
    no way to know the file is a 1× asset — it just draws one physical
    pixel per source pixel. The native fix is the same one Apple's own
    apps use: stitch 1× / 2× / 3× PNGs into a multi-representation TIFF
    via ``tiffutil -cathidpicheck``. NSImage detects the reps and picks
    the right density for the current display.

    The intermediate PNGs are scrubbed after assembly so the cache
    holds only the final ``icon-<hash>-<pt>.tiff``. The cache is
    invalidated when the source's mtime moves forward. On any failure
    (sips missing, source unreadable, tiffutil error) we return ``src``
    unchanged so the menu still renders something.
    """
    try:
        src_mtime = src.stat().st_mtime
    except OSError:
        return src

    import hashlib
    # Hash the absolute path to a stable short filename — readable, and
    # no collisions across two assets that happen to share a basename.
    key = hashlib.sha1(str(src.resolve()).encode("utf-8")).hexdigest()[:12]
    cache_dir = _menubar_cache_dir()
    cache_path = cache_dir / f"icon-{key}-{_MENUBAR_ICON_PT}.tiff"

    try:
        if cache_path.stat().st_mtime >= src_mtime:
            return cache_path
    except OSError:
        pass

    rep_paths: list[Path] = []
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for scale in _MENUBAR_ICON_SCALES:
            rep = cache_dir / f"icon-{key}-{_MENUBAR_ICON_PT}@{scale}x.png"
            subprocess.run(
                ["/usr/bin/sips", "-Z", str(_MENUBAR_ICON_PT * scale),
                 str(src), "--out", str(rep)],
                capture_output=True, check=True,
            )
            rep_paths.append(rep)
        subprocess.run(
            ["/usr/bin/tiffutil", "-cathidpicheck",
             *(str(p) for p in rep_paths), "-out", str(cache_path)],
            capture_output=True, check=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        _warn(f"icon resize/multi-rep failed ({src}): {exc}")
        return src
    finally:
        # Clean up the intermediate PNGs whether the TIFF assembly
        # succeeded or not — we don't want stragglers in the cache.
        for rep in rep_paths:
            try:
                rep.unlink()
            except OSError:
                pass
    return cache_path


def _menubar_icon_pieces() -> tuple[str, str]:
    """Return ``(swiftbar_params, inline_glyph)`` for :attr:`Config.menubar_icon`.

    Four prefix conventions:

    * ``sf:<name>`` — an SF Symbol, rendered via SwiftBar's ``sfimage=``.
    * ``template:<path>`` — a monochrome PNG, auto-resized to fit the
      menu bar height and emitted as ``templateImage=<base64>``. macOS
      tints template images to match the menu bar — same mechanism every
      native app (Slack, Mail, Claude.app itself) uses.
    * ``image:<path>`` — a full-colour PNG via ``image=<base64>``. Also
      auto-resized but rendered as-is, no theme tinting.
    * Anything else is treated as a literal inline glyph (emoji, Unicode
      character, …).

    Paths may be absolute or relative to the plugin directory. When the
    file is missing we degrade to :attr:`Config.menubar_icon_fallback`
    (a plain glyph) so the menu still renders — that's the path when
    e.g. the default Claude.app icon isn't available on the machine.
    """
    icon = CONFIG.menubar_icon
    if icon.startswith("sf:"):
        return f" | sfimage={icon[3:]}", ""
    for prefix, param in (("template:", "templateImage"), ("image:", "image")):
        if icon.startswith(prefix):
            raw_path = icon[len(prefix):]
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = Path(__file__).resolve().parent / raw_path
            if not path.exists():
                # File missing — Claude.app not installed, broken config,
                # whatever. Fall back to the configured emoji so the menu
                # bar still has *some* icon, matching the pre-template
                # behaviour.
                return "", CONFIG.menubar_icon_fallback
            sized = _resized_menubar_image(path)
            try:
                b64 = base64.b64encode(sized.read_bytes()).decode("ascii")
            except OSError as exc:
                _warn(f"menubar_icon image read failed ({sized}): {exc}")
                return "", CONFIG.menubar_icon_fallback
            return f" | {param}={b64}", ""
    return "", icon


def _print_session_row(session: Session) -> None:
    """Emit one main row plus the submenu for one session.

    Main row: state icon + title + coloured right-label. Clicking it
    invokes ``bin/open-session.sh`` which records the click into the
    clicks sidecar (so the row turns 🔵 on the next tick) and then opens
    the session in the user's editor via the
    ``<editor_url_scheme>anthropic.claude-code/open`` URI handler.
    ``editor_url_scheme`` defaults to ``"vscode://"`` and can be
    overridden in the config (e.g. ``"vscodium://"`` for VSCodium).

    Submenu (``--`` prefix lines): up to three row-level actions —
    *mark as read* (🟢 FRESH rows only, records a click without opening
    the editor), *forget* (hide the row until it gets a fresh event),
    *delete* (physically remove the transcript) — followed by read-only
    metadata (current git branch with the full cwd as a hover tooltip,
    context usage). When the cwd isn't a git repository we fall back to
    a plain folder line carrying the cwd itself so the path stays
    visible.
    """
    label = f"{session.group.icon} {session.title} · {session.right_label_ansi}"
    href = f"{CONFIG.editor_url_scheme}anthropic.claude-code/open?session={quote(session.id)}"
    bin_dir = Path(__file__).resolve().parent / "bin"
    open_script = bin_dir / "open-session.sh"
    main_params = [
        f"shell={_swiftbar_quote(str(open_script))}",
        f"param1={_swiftbar_quote(session.id)}",
        f"param2={_swiftbar_quote(href)}",
        "terminal=false",
        "refresh=true",
        f"color={session.group.color}",
        "font=Menlo",
        "ansi=true",
    ]
    print(f"{label} | {' '.join(main_params)}")

    if session.group is RenderGroup.FRESH:
        ack_session_script = bin_dir / "ack-session.sh"
        print(
            f"--{_t('menu.mark_read')} | "
            f"shell={_swiftbar_quote(str(ack_session_script))} "
            f"param1={_swiftbar_quote(session.id)} "
            "terminal=false refresh=true "
            "sfimage=checkmark.circle.fill sfcolor=systemBlue"
        )

    forget_script = bin_dir / "forget-session.sh"
    print(
        f"--{_t('menu.forget_session')} | "
        f"shell={_swiftbar_quote(str(forget_script))} "
        f"param1={_swiftbar_quote(session.id)} "
        "terminal=false refresh=true "
        "sfimage=eraser.fill sfcolor=systemOrange"
    )
    delete_script = bin_dir / "delete-session.sh"
    print(
        f"--{_t('menu.delete_session')} | "
        f"shell={_swiftbar_quote(str(delete_script))} "
        f"param1={_swiftbar_quote(session.id)} "
        "terminal=false refresh=true "
        "sfimage=trash.fill sfcolor=systemRed"
    )
    if session.git_branch:
        # Branch line doubles as the cwd surface: the path is verbose
        # enough that promoting it to the visible label would crowd the
        # menu, so we keep the branch in view and let macOS render the
        # full cwd via NSMenuItem's tooltip on hover.
        branch_line = (
            f"--{session.git_branch} | "
            "font=Menlo color=#999999 sfimage=arrow.triangle.branch"
        )
        if session.cwd:
            branch_line += f" tooltip={_swiftbar_quote(session.cwd)}"
        print(branch_line)
    elif session.cwd:
        # No git branch (cwd isn't a repo, or .git was removed) — surface
        # the full path directly so the menu still tells the user which
        # project the session belongs to.
        print(
            f"--{session.cwd} | font=Menlo color=#999999 sfimage=folder.fill"
        )
    if session.context_used is not None:
        label = _format_context_left(session.context_used, CONFIG.context_window_tokens)
        if label:
            print(f"--{label} | font=Menlo color=#999999 sfimage=gauge.medium")


def _swiftbar_quote(value: str) -> str:
    """Quote a SwiftBar ``paramN=`` value so paths with spaces survive parsing.

    SwiftBar's lexer parses each param value before handing it to the shell.
    Embedded double-quotes would close our outer quoting prematurely; replace
    them with single quotes (no shell-meaningful path component contains a
    double-quote in practice, and this avoids the surprises of backslash
    escaping under SwiftBar's tokenizer).
    """
    return '"' + value.replace('"', "'") + '"'


def _print_footer() -> None:
    """System actions at the bottom of the menu — manual refresh + Tools submenu."""
    print(f"{_t('menu.refresh')} | refresh=true sfimage=arrow.clockwise")
    plugin_dir = Path(__file__).resolve().parent
    bin_dir = plugin_dir / "bin"
    ack_fresh_script = bin_dir / "ack-fresh.sh"
    forget_script = bin_dir / "forget-sessions.sh"
    open_config_script = bin_dir / "open-config.sh"
    example_config = plugin_dir / "config.example.json"
    config_path = _config_path()
    print(f"{_t('menu.tools')} | sfimage=wrench.adjustable.fill")
    print(
        f"--{_t('menu.ack_all')} | "
        f"shell={_swiftbar_quote(str(ack_fresh_script))} "
        "terminal=false refresh=true "
        "sfimage=checkmark.circle.fill sfcolor=systemBlue"
    )
    print(
        f"--{_t('menu.forget_all')} | "
        f"shell={_swiftbar_quote(str(forget_script))} "
        "terminal=false refresh=true "
        "sfimage=eraser.fill sfcolor=systemOrange"
    )
    print("-----")
    print(
        f"--{_t('menu.suggest')} | "
        "href=https://github.com/alexey-krylov/ClaudeAgentsBar/issues/new "
        "sfimage=lightbulb.fill"
    )
    # Resolve the config path Python-side so the open-config.sh wrapper
    # stays a thin shell script and doesn't duplicate the env-var → XDG
    # → ~/.config lookup chain. The example path travels alongside so the
    # script can seed an empty install on the first click.
    if config_path is not None:
        print(
            f"--{_t('menu.config')} | "
            f"shell={_swiftbar_quote(str(open_config_script))} "
            f"param1={_swiftbar_quote(str(config_path))} "
            f"param2={_swiftbar_quote(str(example_config))} "
            "terminal=false "
            "sfimage=gearshape.fill"
        )


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #


def _print_shell_strings() -> None:
    """Emit shell-quoted ``MSG_*`` variables for the resolved locale.

    Used by ``bin/*.sh`` so the AppleScript dialogs stay localised without
    duplicating the string tables on the shell side::

        eval "$(/usr/bin/python3 "$PLUGIN" --print-strings)"
        # → $MSG_DIALOG_DELETE_TITLE, $MSG_DIALOG_DELETE_BODY, …

    Only ``dialog.*`` keys are exported — they're the only ones that need to
    survive a bash boundary. ``shlex.quote`` handles every embedded quote
    and newline, so bash gets a real multi-line value safe to splice into a
    HEREDOC.

    Placeholders inside the templates (``{title}``, ``{sid}``) are passed
    through verbatim; the calling script substitutes them at use time.
    """
    import shlex

    table = STRINGS.get(_lang(), STRINGS["en"])
    for key in sorted(table):
        if not key.startswith("dialog."):
            continue
        # Fall back to English for individual missing entries (defensive —
        # full tables today, but cheap insurance against future drift).
        value = table.get(key) or STRINGS["en"].get(key, "")
        var = "MSG_" + key.replace(".", "_").upper()
        print(f"{var}={shlex.quote(value)}")


def main() -> int:
    """Render the menu once. Always exits zero so SwiftBar keeps polling.

    Recognised subcommands:

    * ``--ack-fresh`` runs the bulk acknowledgement (Tools → Acknowledge all).
    * ``--print-strings`` emits localized shell variables for bin/*.sh.

    Anything else is treated as a render.
    """
    if len(sys.argv) > 1 and sys.argv[1] == "--ack-fresh":
        try:
            ack_fresh(int(time.time()))
        except Exception as exc:
            _warn(f"ack-fresh failed: {exc}")
            return 1
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--print-strings":
        _print_shell_strings()
        return 0
    try:
        render(collect_sessions(int(time.time())))
    except Exception as exc:
        # Catch-all so SwiftBar never sees a Python traceback in the menu.
        print("⚠️ | color=red")
        print("---")
        print(f"{_t('error.plugin', exc=exc)} | color=red")
    return 0


if __name__ == "__main__":
    sys.exit(main())
