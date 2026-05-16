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

#: Mutex on :data:`FORGET_PATH`, shared between plugin (gc) and
#: ``bin/app/forget-session.sh``. Same ``mkdir``-based scheme as the other sidecar
#: locks.
_FORGET_LOCK_DIR = FORGET_PATH.with_suffix(FORGET_PATH.suffix + ".lock.d")

#: Directory used as a mutex on the sidecar by both the plugin (cleanup) and
#: ``hooks/agent-state.sh`` (writes). ``mkdir`` is atomic on every POSIX
#: filesystem and doesn't need ``util-linux``, unlike ``flock``.
_SIDECAR_LOCK_DIR = SIDECAR_PATH.with_suffix(SIDECAR_PATH.suffix + ".lock.d")

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
