"""Session composition + SwiftBar menu emission.

This is the per-tick hot path. ``collect_sessions`` joins the four
sidecars with each live JSONL into a list of :class:`core.Session`
objects, and ``render`` walks that list to print the SwiftBar-formatted
menu on stdout.

The split between this module and :mod:`claude_agents_bar.sidecars` is
"composition vs. raw data": every helper that reads or writes a sidecar
or a transcript lives there; everything that turns the merged view into
UI lives here.

All ``core.X`` accesses go through the module (not ``from .core import X``)
so unit tests can swap ``CONFIG`` / ``SIDECAR_PATH`` / ``HOME`` via
``patch.object(plugin.core, …)`` and the substitution propagates here.
"""

from __future__ import annotations

import base64
import datetime as _dt
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from . import core, keep_awake, sidecars
from .core import (
    ACTIVE_HOOK_STATES,
    INTERACTIVE_ENTRYPOINTS,
    RenderGroup,
    Session,
    SubagentSnapshot,
    _ANSI_ACK_BAR,
    _ANSI_ACTIVE_BAR,
    _ANSI_FRESH_BAR,
    _ANSI_RESET,
    _ANSI_WAITING,
    _classify,
    _format_context_left,
    _format_context_warning,
    _humanize_age,
    _lang,
    _model_badge,
    _project_name,
    _shorten,
    _shorten_head,
    _t,
    _warn,
)

# --------------------------------------------------------------------------- #
# Composition                                                                  #
# --------------------------------------------------------------------------- #


def iter_active_jsonls(now: int) -> Iterator[Path]:
    """Yield JSONL transcripts touched within the last :attr:`Config.window_sec`."""
    if not core.PROJECTS_DIR.exists():
        return
    for project_dir in core.PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                mtime = int(jsonl.stat().st_mtime)
            except OSError:
                continue
            if now - mtime <= core.CONFIG.window_sec:
                yield jsonl


def _apply_subagent_watchdog(
    raw: tuple[SubagentSnapshot, ...], now: int
) -> tuple[SubagentSnapshot, ...]:
    """Demote ``working`` subagent rows whose ``last_event_ts`` is stale.

    Mirrors the parent-side watchdog in :func:`build_session`: if a
    subagent stopped emitting hook events for more than
    :attr:`Config.watchdog_sec` seconds without a `SubagentStop`, the
    Task has almost certainly crashed and we treat it as stopped so the
    parent rollup doesn't pin the row 🟡 forever.
    """
    out: list[SubagentSnapshot] = []
    for s in raw:
        if s.state == "working" and (now - s.last_event_ts) > core.CONFIG.watchdog_sec:
            out.append(replace(s, state="stopped"))
        else:
            out.append(s)
    return tuple(out)


def build_session(
    jsonl: Path,
    sidecar: dict[str, core.HookSnapshot],
    clicks: dict[str, int],
    subagents_by_sid: dict[str, tuple[SubagentSnapshot, ...]],
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
        last_event_kind = snapshot.last_event_kind
    else:
        last_ts = jsonl_mtime
        hook_state = "idle"
        sidecar_cwd = ""
        state_since = jsonl_mtime
        # Empty kind means "no hook event seen for this session" — the
        # classifier uses this to refuse the FRESH window (no Stop event
        # ever fired, so we have nothing to be "fresh" about).
        last_event_kind = ""

    subagents = _apply_subagent_watchdog(
        subagents_by_sid.get(jsonl.stem, ()), now
    )
    live_subagents = tuple(s for s in subagents if s.is_live)
    has_live_subagent = bool(live_subagents)
    live_subagent_ts = (
        max(s.last_event_ts for s in live_subagents) if live_subagents else 0
    )

    # Liveness signal: Claude Code writes the parent transcript continuously
    # while running, but during a ``Task`` the parent JSONL freezes and only
    # the subagent transcripts tick. Folding the freshest live subagent's
    # ``last_event_ts`` into the floor keeps the parent ACTIVE while any
    # child is doing work — the whole point of the subagent sidecar.
    liveness_ts = max(last_ts, jsonl_mtime, live_subagent_ts)
    if (
        hook_state == "working"
        and not has_live_subagent
        and (now - liveness_ts) > core.CONFIG.watchdog_sec
    ):
        hook_state = "idle"
        last_ts = liveness_ts
        # Watchdog-driven downgrade is not a real Stop — clear the kind so
        # the classifier doesn't paint the row FRESH on a stale "working"
        # row whose timestamp happens to fall inside fresh_sec.
        last_event_kind = ""

    # Parent state rollup: a live subagent forces the parent to ACTIVE even
    # when the parent's own hook said ``Stop`` (a turn that ended before the
    # Task did — rare but it happens with backgrounded subagents). Without
    # this the row would briefly flash 🟢 / 🔵 mid-Task. See
    # ``docs/specs/0004-subagent-grouping.md`` § Parent state rollup.
    if has_live_subagent and hook_state not in ACTIVE_HOOK_STATES:
        hook_state = "working"
        last_event_kind = ""
        # Anchor ``state_since`` to the oldest live subagent so the
        # right-side label reads as "Task running for Xm", not as
        # "working for 0s" each tick.
        state_since = min(s.state_since for s in live_subagents)
        last_ts = max(last_ts, live_subagent_ts)

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
        # While the session is in flight the JSONL mtime *and* every live
        # subagent's last_event_ts are fresher signals than the TSV
        # timestamp — the latter only ticks on parent-side hook events.
        interaction_ts = max(interaction_ts, jsonl_mtime, live_subagent_ts)
    age = now - interaction_ts

    meta = sidecars.read_transcript_meta(jsonl)
    # AI-generated titles only appear after the first turn — for sessions
    # whose first user message hasn't been summarized yet we fall back to
    # the latest *real* user prompt (not first, since by then the
    # conversation has often moved on). Only worth the extra tail-read
    # when ``ai_title`` is missing.
    if not meta.ai_title:
        last_user = sidecars.last_user_message_preview(jsonl)
        if last_user:
            meta = replace(meta, last_user_message=last_user)
    cwd = sidecar_cwd or meta.cwd
    branch = sidecars.current_git_branch(cwd) or sidecars.fallback_git_branch_from_jsonl(jsonl)
    is_worktree = sidecars.is_worktree_checkout(cwd)
    context_used = sidecars.last_usage_tokens(jsonl)
    tool_summary = sidecars.last_tool_use_summary(jsonl)
    remind_summary = sidecars.last_assistant_summary(jsonl, core.CONFIG.notify_summary_marker)
    session_model = sidecars.last_session_model(jsonl)

    state_duration_sec = (
        max(0, now - state_since) if hook_state in ACTIVE_HOOK_STATES else 0
    )

    return Session(
        id=jsonl.stem,
        hook_state=hook_state,
        group=_classify(hook_state, now, last_ts, effective_click_ts, last_event_kind),
        last_event_ts=interaction_ts,
        age_sec=age,
        title=meta.display_title or _t("title.no_title"),
        project=_project_name(cwd, jsonl.parent.name),
        git_branch=branch,
        cwd=cwd,
        entrypoint=meta.entrypoint,
        context_used=context_used,
        state_duration_sec=state_duration_sec,
        last_tool_use=tool_summary,
        remind_summary=remind_summary,
        model=session_model,
        subagents=subagents,
        is_worktree=is_worktree,
    )


def collect_sessions(now: int) -> list[Session]:
    """Build all live sessions, filtered + sorted ready for rendering.

    Side effect: both sidecars (state TSV and clicks TSV) are
    opportunistically garbage-collected — rows whose transcript is gone
    or whose last event has fallen out of :attr:`Config.window_sec` are
    stripped. Cheap when there's nothing to drop; we only take the lock
    + rewrite when stale rows exist.
    """
    sidecar = sidecars.read_sidecar()
    clicks = sidecars.read_clicks()
    forget = sidecars.read_forget()
    subagents_by_sid = sidecars.read_subagents_sidecar()
    if stale := sidecars._stale_sidecar_ids(sidecar, now):
        sidecars.gc_sidecar(stale)
        for sid in stale:
            sidecar.pop(sid, None)
    if subagents_by_sid:
        # Same GC stance as the main sidecar: drop rows whose parent
        # transcript is gone or whose last event has fallen out of the
        # render window. Cheap when there's nothing to drop.
        stale_subs = sidecars._stale_subagent_keys(subagents_by_sid, now)
        if stale_subs:
            sidecars.gc_subagents(stale_subs)
            for parent_sid, agent_id in stale_subs:
                remaining = tuple(
                    s for s in subagents_by_sid.get(parent_sid, ())
                    if s.agent_id != agent_id
                )
                if remaining:
                    subagents_by_sid[parent_sid] = remaining
                else:
                    subagents_by_sid.pop(parent_sid, None)
    # Click and forget rows for sessions whose transcript no longer exists
    # are pure overhead. The transcript — not the state sidecar — is
    # authoritative: plenty of legitimate sessions have a JSONL but no TSV row.
    if clicks or forget:
        live_ids = sidecars._live_session_ids()
        orphan_clicks = set(clicks) - live_ids
        if orphan_clicks:
            sidecars.gc_clicks(orphan_clicks)
            for sid in orphan_clicks:
                clicks.pop(sid, None)
        orphan_forget = set(forget) - live_ids
        if orphan_forget:
            sidecars.gc_forget(orphan_forget)
            for sid in orphan_forget:
                forget.pop(sid, None)
    # Only sessions the hook has actually written a row for are eligible
    # for rendering. A JSONL transcript on its own is not enough — Claude
    # Code touches the transcript on every IDE tab switch (SessionStart
    # source=resume / source=compact), which is why we deliberately don't
    # register the SessionStart hook. Filtering here means a session
    # appears in the menu only after a real working/waiting/idle event
    # has fired (UserPromptSubmit, PreToolUse, etc.).
    sessions = [
        build_session(p, sidecar, clicks, subagents_by_sid, now)
        for p in iter_active_jsonls(now)
        if p.stem in sidecar
    ]
    # Drop headless sessions unconditionally — they're scripted runs the
    # user can't usefully interact with, and they otherwise clutter the menu.
    sessions = [s for s in sessions if _is_interactive(s)]
    if dismiss_ts := sidecars.read_dismiss_ts():
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
    _mark_cwd_collisions(sessions)
    sessions.sort(key=lambda s: (s.group.order, -s.last_event_ts))
    return sessions


def _mark_cwd_collisions(sessions: list[Session]) -> None:
    """Flag every *active* session that shares a folder with another.

    Two or more sessions in ``working`` / ``waiting`` with the same
    normalized non-empty ``cwd`` are stepping on each other — set
    ``cwd_collision`` on all of them so the branch line can warn. Idle
    sessions and empty ``cwd`` are ignored. Mutates ``sessions`` in place.
    """
    by_cwd: dict[str, list[Session]] = {}
    for s in sessions:
        if s.hook_state not in ACTIVE_HOOK_STATES or not s.cwd:
            continue
        by_cwd.setdefault(os.path.normpath(s.cwd), []).append(s)
    for group in by_cwd.values():
        if len(group) > 1:
            for s in group:
                s.cwd_collision = True


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
        duration = core._humanize_age(core.CONFIG.window_sec, core._lang())
        print(f"{_t('menu.no_sessions', duration=duration)} | color=gray")
        print("---")
        _print_footer(sessions)
        return

    last_group: RenderGroup | None = None
    for session in sessions:
        if last_group is not None and last_group is not session.group:
            print("---")
        last_group = session.group
        _print_session_row(session)

    print("---")
    _print_footer(sessions)


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
    if core.CONFIG.compact:
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
    base = Path(xdg).expanduser() if xdg else core.HOME / ".cache"
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
    icon = core.CONFIG.menubar_icon
    if icon.startswith("sf:"):
        return f" | sfimage={icon[3:]}", ""
    for prefix, param in (("template:", "templateImage"), ("image:", "image")):
        if icon.startswith(prefix):
            raw_path = icon[len(prefix):]
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = core.PLUGIN_DIR / raw_path
            if not path.exists():
                # File missing — Claude.app not installed, broken config,
                # whatever. Fall back to the configured emoji so the menu
                # bar still has *some* icon, matching the pre-template
                # behaviour.
                return "", core.CONFIG.menubar_icon_fallback
            sized = _resized_menubar_image(path)
            try:
                b64 = base64.b64encode(sized.read_bytes()).decode("ascii")
            except OSError as exc:
                _warn(f"menubar_icon image read failed ({sized}): {exc}")
                return "", core.CONFIG.menubar_icon_fallback
            return f" | {param}={b64}", ""
    return "", icon


def _branch_decoration(session: Session) -> tuple[str, str, str]:
    """Pick the branch line's text colour, text and status tooltip.

    Status is carried by the *text* colour: SF Symbols in an NSMenu submenu
    render monochrome (``sfcolor`` is ignored — they inherit the menu's
    label colour), so the icon can't carry it. A cwd collision (two active
    sessions in the same folder) wins and paints the branch name red;
    otherwise a worktree checkout paints it green to signal the agent's
    changes are isolated; otherwise the usual dim grey. Returns
    ``(color, text, tooltip)`` — an empty tooltip means "no status note,
    let the caller fall back to the plain cwd".
    """
    if session.cwd_collision:
        return ("#cc0000", session.git_branch, _t("tooltip.cwd_collision"))
    if session.is_worktree:
        return ("#1f7a1f", session.git_branch, _t("tooltip.worktree"))
    return ("#999999", session.git_branch, "")


def _print_session_row(session: Session) -> None:
    """Emit one main row plus the submenu for one session.

    Main row: state icon + title + coloured right-label. Clicking it
    invokes ``bin/app/open-session.sh`` which records the click into the
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
    warning = ""
    if session.context_used is not None:
        warning = _format_context_warning(
            session.context_used,
            core.CONFIG.context_window_tokens,
            core.CONFIG.context_warning_threshold,
        )
    warning_segment = f"{warning} · " if warning else ""
    waiting_segment = "❓ · " if session.hook_state == "waiting" else ""
    # Two active sessions sharing a folder: a red fork glyph (U+2442, the
    # closest unicode match to the submenu's branch SF Symbol) between the
    # title and the duration. ``⚠`` is already taken by the context-usage
    # warning, so this fork is the collision marker and echoes the red
    # branch name in the submenu.
    collision_segment = (
        f"{_ANSI_WAITING}⑂{_ANSI_RESET} · " if session.cwd_collision else ""
    )
    live_count = session.live_subagent_count
    subagent_segment = (
        f"{_t('row.subagent_badge', n=live_count)} · " if live_count else ""
    )
    label = (
        f"{session.group.icon} {session.title} · "
        f"{collision_segment}{subagent_segment}{waiting_segment}{warning_segment}{session.right_label_ansi}"
    )
    href = f"{core.CONFIG.editor_url_scheme}anthropic.claude-code/open?session={quote(session.id)}"
    bin_dir = core.PLUGIN_DIR / "bin" / "app"
    open_script = bin_dir / "open-session.sh"
    # The session deeplink lands in whichever editor window is frontmost —
    # the extension doesn't route by workspace. When multi-workspace mode
    # is on we hand open-session.sh the session's cwd, the .app that owns
    # the scheme, and the settle delay so it can raise the matching window
    # first; when off we pass only id + url, so open-session.sh falls back
    # to firing the deeplink directly (the snappy single-window path). The
    # live toggle (sidecar) wins over the config default.
    main_params = [
        f"shell={_swiftbar_quote(str(open_script))}",
        f"param1={_swiftbar_quote(session.id)}",
        f"param2={_swiftbar_quote(href)}",
    ]
    if core.multi_workspace_enabled():
        editor_app = core.EDITOR_SCHEME_APP.get(core.CONFIG.editor_url_scheme, "")
        main_params += [
            f"param3={_swiftbar_quote(session.cwd)}",
            f"param4={_swiftbar_quote(editor_app)}",
            f"param5={_swiftbar_quote(str(core.CONFIG.editor_focus_settle_sec))}",
        ]
    main_params += [
        "terminal=false",
        "refresh=true",
        f"color={session.group.color}",
        "font=Menlo",
        "ansi=true",
    ]
    print(f"{label} | {' '.join(main_params)}")

    # "Remind" — re-speak this session's last spoken summary via say(1).
    # First item in every row's submenu. Enabled only when there's something
    # to say (the marker is on and the last reply ended with a summary line);
    # otherwise rendered greyed-out and inert so the slot stays predictable.
    if session.remind_summary:
        remind_script = bin_dir / "remind-session.sh"
        print(
            f"--{_t('menu.remind_session')} | "
            f"shell={_swiftbar_quote(str(remind_script))} "
            f"param1={_swiftbar_quote(session.remind_summary[:240])} "
            "terminal=false refresh=false "
            "sfimage=speaker.wave.2.fill sfcolor=systemBlue"
        )
    else:
        print(
            f"--{_t('menu.remind_session')} | "
            "color=#999999 sfimage=speaker.wave.2.fill"
        )

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
    reveal_script = bin_dir / "reveal-session.sh"
    print(
        f"--{_t('menu.reveal_in_finder')} | "
        f"shell={_swiftbar_quote(str(reveal_script))} "
        f"param1={_swiftbar_quote(session.id)} "
        "terminal=false refresh=false "
        "sfimage=doc.text.magnifyingglass sfcolor=systemGray"
    )
    if session.git_branch:
        # Project + branch are split across two submenu lines so each
        # gets its own SF Symbol (SwiftBar allows one ``sfimage=`` per
        # item). The cwd tooltip rides on the project line — it's the
        # natural carrier for the full path; the branch line stays
        # minimal. When the project name is missing we collapse back to
        # a single branch line so the cwd tooltip still surfaces.
        branch_color, branch_text, branch_tooltip = _branch_decoration(session)
        if session.project:
            project_line = (
                f"--{session.project} | "
                "font=Menlo color=#999999 sfimage=folder"
            )
            # The cwd tooltip rides on the project line by default, but a
            # collision / worktree status tooltip takes precedence and lives
            # on the branch line — so only attach the plain cwd here when the
            # branch line isn't already carrying a status tooltip.
            if session.cwd and not branch_tooltip:
                project_line += f" tooltip={_swiftbar_quote(session.cwd)}"
            print(project_line)
            branch_line = (
                f"--{branch_text} | "
                f"font=Menlo color={branch_color} sfimage=arrow.triangle.branch"
            )
            if branch_tooltip:
                branch_line += f" tooltip={_swiftbar_quote(branch_tooltip)}"
        else:
            branch_line = (
                f"--{branch_text} | "
                f"font=Menlo color={branch_color} sfimage=arrow.triangle.branch"
            )
            tooltip = branch_tooltip or session.cwd
            if tooltip:
                branch_line += f" tooltip={_swiftbar_quote(tooltip)}"
        print(branch_line)
    elif session.cwd:
        # No git branch (cwd isn't a repo, or .git was removed) — surface
        # the full path directly so the menu still tells the user which
        # project the session belongs to.
        print(
            f"--{session.cwd} | font=Menlo color=#999999 sfimage=folder.fill"
        )
    if core.CONFIG.model_badge and session.model:
        # Full model string between branch and context — same read-only
        # `font=Menlo color=#999999` style as its neighbours. The row
        # itself doesn't carry model info, so this submenu line is the
        # only surface that tells the user which model is live; gated by
        # ``model_badge`` config for users who want a quieter menu.
        print(
            f"--{session.model} | font=Menlo color=#999999 sfimage=cpu"
        )
    if session.context_used is not None:
        label = _format_context_left(session.context_used, core.CONFIG.context_window_tokens)
        if label:
            # The context line piggy-backs the "currently doing" preview
            # as its hover tooltip — same pattern as the branch row
            # carrying the full cwd. Lives here rather than on the
            # parent session row because NSMenu auto-expands the submenu
            # on hover and that win-races the tooltip; leaf rows under
            # the open submenu show their tooltips reliably.
            context_line = (
                f"--{label} | font=Menlo color=#999999 sfimage=gauge.medium"
            )
            if session.last_tool_use:
                context_line += (
                    f" tooltip={_swiftbar_quote(session.last_tool_use)}"
                )
            print(context_line)
    if session.subagents:
        _print_subagent_block(session)


def _print_subagent_block(session: Session) -> None:
    """Render the Subagents info block in a parent's submenu.

    A submenu separator (``-----``) precedes the header so the block
    reads as its own visual section, distinct from the project / branch /
    model / context rows above it. Header carries the live count and
    shares the SF-symbol icon column with its siblings. Two rows per
    subagent follow — the user explicitly asked for a statically-
    expanded block rather than a nested submenu, since the list is
    short and drilling into a sub-popup just to read four words is
    friction.

    First subagent row carries: status icon (🟡 / 🟢), the parent's
    ``Task`` description (read from the meta sidecar) or the agent type
    when the description is missing, and the in-state duration or
    time-since-stop. The second row carries the freshest ``tool_use``
    from the subagent's own transcript — split off the main row because
    long Bash commands pack the single line past readable. Rows aren't
    clickable; deep-links can't reach a subagent transcript.

    Status / model / tool glyphs travel inline rather than via
    ``sfimage=`` because SwiftBar's ``color=`` overrides ``sfcolor=``,
    which would turn every status circle grey instead of yellow/green.
    Inline emoji carry their own colour and dodge that conflict.
    """
    print("-----")
    project_dir = _project_dir_for(session)
    now = int(time.time())
    visible = sorted(
        (
            s for s in session.subagents
            if s.is_live or (now - s.last_event_ts) <= core.CONFIG.fresh_sec
        ),
        key=lambda s: (0 if s.is_live else 1, -s.last_event_ts),
    )
    live_count = session.live_subagent_count
    header = _t("menu.subagents_header", n=live_count, total=len(visible))
    print(
        f"--🤖 {header} | "
        "font=Menlo color=#888888"
    )
    for snap in visible:
        main_label, sub_rows = _subagent_row_parts(
            snap, session, project_dir, now,
        )
        color = "#cc7700" if snap.is_live else "#999999"
        print(f"--{main_label} | color={color}")
        for sub_row in sub_rows:
            print(f"----{sub_row}")


def _project_dir_for(session: Session) -> Path | None:
    """Resolve the Claude Code project directory holding the parent JSONL.

    Claude Code stores transcripts under
    ``~/.claude/projects/<slugified-cwd>/<session_id>.jsonl``; the slug is
    not derivable from :class:`Session` alone, so we look for the
    matching file across project directories. ``None`` means the parent
    transcript wasn't found on disk (race during deletion) — callers
    degrade by skipping the per-subagent ``tool_use`` summary.
    """
    if not core.PROJECTS_DIR.exists():
        return None
    for candidate in core.PROJECTS_DIR.iterdir():
        if not candidate.is_dir():
            continue
        if (candidate / f"{session.id}.jsonl").exists():
            return candidate
    return None


def _subagent_row_parts(
    snap: SubagentSnapshot,
    session: Session,
    project_dir: Path | None,
    now: int,
) -> tuple[str, list[str]]:
    """Return ``(main_label, [sub_label, ...])`` for a subagent.

    The status glyph (🟡/🟢) travels inline — SwiftBar's ``color=``
    overrides ``sfcolor=``, so ``sfimage=circle.fill sfcolor=systemYellow``
    would render grey. Inline emoji carry their own colour. The model
    sub-row uses ``sfimage=cpu`` (no ``color=``, so sfimage works).
    The tool sub-row uses inline ``↳``.

    Main label: ``{status-emoji} {agent_type} · {description} · {duration}``.
    🟡 while the subagent is working, 🟢 once ``SubagentStop``
    fires — same colour vocabulary as the parent RenderGroup.
    Description comes from the ``Task`` tool's ``description``
    field via ``agent-<id>.meta.json``; omitted when missing.

    Sub-rows carry their own SwiftBar params (full "label | params"
    strings) so each can have a different ``sfimage=``.

    Sub-rows, in order:

    1. ``{model-name} | sfimage=cpu`` — full model string with the
       CPU SF Symbol. Only shown when the subagent's
       JSONL has a parseable ``"model":"..."``.
    2. ``↳ {tool} · 🛠×N · ran Xs`` — inline ``↳`` (U+21B3)
       carries the "child of" semantics; head-trimmed
       ``tool_use`` summary so deep paths keep their meaningful
       tail; cumulative tool count; end-to-end runtime for
       finished subagents written by the 7-column hook.

    Empty list means "no sub-rows", caller skips printing them.
    """
    description = ""
    model_str: str | None = None
    summary = ""
    tool_count = 0
    if project_dir is not None:
        subagents_dir = project_dir / session.id / "subagents"
        meta_path = subagents_dir / f"agent-{snap.agent_id}.meta.json"
        sub_jsonl = subagents_dir / f"agent-{snap.agent_id}.jsonl"
        meta = sidecars.read_subagent_meta(meta_path) if meta_path.exists() else None
        if meta is not None:
            desc_raw = meta.get("description")
            if isinstance(desc_raw, str):
                description = desc_raw.strip()
        if sub_jsonl.exists():
            model_str = sidecars.last_session_model(sub_jsonl)
            summary = sidecars.last_tool_use_summary(sub_jsonl)
            tool_count = sidecars.count_tool_uses(sub_jsonl)

    # Status emoji travels inline — see the docstring on why an
    # ``sfimage=circle.fill`` row would have its ``sfcolor`` overridden
    # by ``color=``.
    status_icon = (
        RenderGroup.ACTIVE.icon if snap.is_live else RenderGroup.STALE.icon
    )

    if snap.is_live:
        duration = _humanize_age(max(0, now - snap.state_since), _lang())
    else:
        duration = _humanize_age(max(0, now - snap.last_event_ts), _lang())

    # Build label: status · type · description (trimmed to 40) · duration
    type_str = snap.agent_type if snap.agent_type else "Task"
    label_parts = [status_icon + " " + type_str]
    if description:
        desc_trimmed = (description[:40] + "…") if len(description) > 40 else description
        label_parts.append(desc_trimmed)
    label_parts.append(duration)
    main_label = " · ".join(label_parts)

    # Each sub-row is a full "label | params" string so model and tool
    # rows can carry different SwiftBar params (sfimage=cpu for model).
    sub_rows: list[str] = []

    if model_str:
        sub_rows.append(
            f"{model_str} | font=Menlo color=#999999 sfimage=cpu"
        )

    tool_segments: list[str] = []
    if summary:
        tool_segments.append(_shorten_head(summary))
    if tool_count:
        tool_segments.append(f"🛠×{tool_count}")
    if not snap.is_live and snap.first_event_ts is not None:
        runtime_sec = max(0, snap.last_event_ts - snap.first_event_ts)
        if runtime_sec > 0:
            tool_segments.append(
                _t(
                    "menu.subagent_ran",
                    duration=_humanize_age(runtime_sec, _lang()),
                )
            )
    if tool_segments:
        tooltip = f" tooltip={_swiftbar_quote(summary)}" if summary else ""
        sub_rows.append(
            "↳ " + " · ".join(tool_segments) + f" | font=Menlo color=#999999{tooltip}"
        )

    return main_label, sub_rows


def _swiftbar_quote(value: str) -> str:
    """Quote a SwiftBar ``paramN=`` value so paths with spaces survive parsing.

    SwiftBar's lexer parses each param value before handing it to the shell.
    Embedded double-quotes would close our outer quoting prematurely; replace
    them with single quotes (no shell-meaningful path component contains a
    double-quote in practice, and this avoids the surprises of backslash
    escaping under SwiftBar's tokenizer).
    """
    return '"' + value.replace('"', "'") + '"'


def _print_footer(sessions: list[Session] | None = None) -> None:
    """System actions at the bottom of the menu — manual refresh + Tools submenu.

    ``sessions`` (optional) is the same list rendered above; passed in so
    the *Keep awake* status line can quote *holding while N working*
    without re-walking the disk. ``None`` is treated as "no live work".
    """
    print(f"{_t('menu.refresh')} | refresh=true sfimage=arrow.clockwise")
    bin_dir = core.PLUGIN_DIR / "bin" / "app"
    ack_fresh_script = bin_dir / "ack-fresh.sh"
    forget_script = bin_dir / "forget-sessions.sh"
    open_config_script = bin_dir / "open-config.sh"
    example_config = core.PLUGIN_DIR / "config.example.json"
    config_path = core._config_path()
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
    stats_script = bin_dir / "stats-today.sh"
    print(
        f"--{_t('menu.stats_today')} | "
        f"shell={_swiftbar_quote(str(stats_script))} "
        "terminal=false refresh=false "
        "sfimage=chart.bar.fill sfcolor=systemPurple"
    )

    print("-----")
    _print_notifications_block(bin_dir)

    print("-----")
    _print_keep_awake_block(bin_dir, sessions or [])

    print("-----")
    # Multi-workspace mode toggle — sits just above Configuration. A native
    # SwiftBar checkmark reflects the live state; clicking flips it by
    # writing the opposite value to the sidecar (so it doesn't rewrite the
    # user's config.json). Effective state already folds sidecar over config.
    mw_on = core.multi_workspace_enabled()
    mw_set_script = bin_dir / "multi-workspace-set.sh"
    # Invoke via `/bin/bash <script> <value>` rather than running the
    # script directly, so it doesn't depend on the executable bit (same
    # reason as raise-and-open.sh — survives distribution where the +x bit
    # may be lost). param1 is the script, param2 the value to write.
    print(
        f"--{_t('menu.multi_workspace')} | "
        "shell=/bin/bash "
        f"param1={_swiftbar_quote(str(mw_set_script))} "
        f"param2={'off' if mw_on else 'on'} "
        f"checked={'true' if mw_on else 'false'} "
        "terminal=false refresh=true "
        "sfimage=macwindow.on.rectangle"
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

    # Suggest improvement sits at the very bottom of Tools.
    print(
        f"--{_t('menu.suggest')} | "
        "href=https://github.com/alexey-krylov/ClaudeAgentsBar/issues/new "
        "sfimage=lightbulb.fill"
    )


# --------------------------------------------------------------------------- #
# Tools → Notifications block (spec 0002)                                      #
# --------------------------------------------------------------------------- #


def _format_quiet_status(status: dict) -> str:
    """Translate the kind/numbers dict from :func:`core.quiet_status` to a
    localised one-liner.

    Centralised here so the duration humanisation can reuse
    :func:`_humanize_age` (already locale-aware) without core having to
    depend on the rendering helpers.
    """
    kind = status["kind"]
    lang = _lang()
    if kind == "off":
        return _t("quiet.status_off")
    if kind == "scheduled_active":
        return _t(
            "quiet.status_scheduled_active",
            start=status["start"], end=status["end"],
            remaining=_humanize_age(status["scheduled_remaining"], lang),
        )
    if kind == "scheduled_inactive":
        return _t(
            "quiet.status_scheduled_inactive",
            start=status["start"], end=status["end"],
            until_start=_humanize_age(status["scheduled_until_start"], lang),
        )
    if kind == "bypassed":
        return _t(
            "quiet.status_bypassed",
            remaining=_humanize_age(status["bypass_remaining"], lang),
        )
    # Both "paused" and "paused_and_scheduled_active" collapse to the same
    # user-facing line: the user sees that they're paused, and the *Resume*
    # click only clears the ad-hoc sidecar (leaving any scheduled window
    # untouched). Listing the scheduled overlay would crowd the row without
    # changing what the buttons do.
    return _t(
        "quiet.status_paused",
        remaining=_humanize_age(status["paused_remaining"], lang),
    )


def _print_notifications_block(bin_dir: Path) -> None:
    """Tools → Notifications: status line + Pause / Resume / Bypass actions."""
    until_dt = sidecars.read_quiet_until()
    bypass_until_dt = sidecars.read_quiet_bypass_until()
    status = core.quiet_status(
        _dt.datetime.now(),
        core.CONFIG.quiet_hours,
        until_dt,
        bypass_until_dt,
    )
    print(
        f"--{_t('menu.notifications')} | "
        "font=Menlo color=#888888 sfimage=bell.fill"
    )
    print(
        f"--  {_format_quiet_status(status)} | "
        "font=Menlo color=#888888"
    )

    pause_script = bin_dir / "quiet-pause.sh"
    resume_script = bin_dir / "quiet-resume.sh"
    bypass_script = bin_dir / "quiet-bypass.sh"
    bypass_cancel_script = bin_dir / "quiet-bypass-cancel.sh"

    kind = status["kind"]
    if kind in ("paused", "paused_and_scheduled_active"):
        # Paused — only "Resume" makes sense. Bypass would be a
        # contradiction (user has just said "no notifications").
        print(
            f"--  {_t('quiet.resume')} | "
            f"shell={_swiftbar_quote(str(resume_script))} "
            "terminal=false refresh=true "
            "sfimage=bell.fill sfcolor=systemGreen"
        )
    elif kind == "bypassed":
        # Bypass active — offer the inverse so the user can revert
        # without waiting for the window to end.
        print(
            f"--  {_t('quiet.bypass_cancel')} | "
            f"shell={_swiftbar_quote(str(bypass_cancel_script))} "
            "terminal=false refresh=true "
            "sfimage=bell.slash.fill sfcolor=systemIndigo"
        )
    elif kind == "scheduled_active":
        # In the scheduled quiet window — Bypass becomes meaningful
        # ("I need to be reachable until this window ends"). Pause is
        # redundant here (notifications are already suppressed), so we
        # swap the two pause rows out for a single Bypass row.
        print(
            f"--  {_t('quiet.bypass')} | "
            f"shell={_swiftbar_quote(str(bypass_script))} "
            "terminal=false refresh=true "
            "sfimage=bell.fill sfcolor=systemGreen"
        )
    else:
        print(
            f"--  {_t('quiet.pause_hour')} | "
            f"shell={_swiftbar_quote(str(pause_script))} "
            f"param1=1h "
            "terminal=false refresh=true "
            "sfimage=moon.fill sfcolor=systemIndigo"
        )
        print(
            f"--  {_t('quiet.pause_tomorrow')} | "
            f"shell={_swiftbar_quote(str(pause_script))} "
            f"param1=tomorrow "
            "terminal=false refresh=true "
            "sfimage=moon.zzz.fill sfcolor=systemIndigo"
        )

    # Notification mode selector — banner+audio vs banner-only. A radio
    # pair (like keep-awake) rather than a checkbox so both choices read
    # explicitly; ``checked=true`` marks the live one. Each row passes its
    # absolute on/off value, so a click is idempotent.
    #
    # Invoked as ``/bin/bash <script> <value>`` rather than ``shell=<script>``
    # so it doesn't depend on the action script's executable bit surviving
    # distribution (Homebrew bottle / zip) — the same robustness the hook
    # helper buys via ``_raise_open_cmd``. (bin/app/multi-workspace-set.sh
    # shipped 0644 in 1.1.1 and its checkbox went dead; this sidesteps that.)
    audio_on = core.notify_audio_enabled()
    audio_set_script = bin_dir / "notify-audio-set.sh"
    for value, key, icon in (
        ("on",  "notify.mode_voice",  "speaker.wave.2.fill"),
        ("off", "notify.mode_banner", "speaker.slash.fill"),
    ):
        checked = "checked=true " if (value == "on") == audio_on else ""
        print(
            f"--  {_t(key)} | "
            "shell=/bin/bash "
            f"param1={_swiftbar_quote(str(audio_set_script))} "
            f"param2={value} "
            f"{checked}"
            "terminal=false refresh=true "
            f"sfimage={icon}"
        )


# --------------------------------------------------------------------------- #
# Tools → Keep awake block (spec 0003)                                         #
# --------------------------------------------------------------------------- #


def _print_keep_awake_block(bin_dir: Path, sessions: list[Session]) -> None:
    """Tools → Keep awake: status line + mode selector."""
    mode = keep_awake.current_mode()
    working = sum(1 for s in sessions if s.hook_state == "working")

    if mode == "off":
        status = _t("keep_awake.status_off")
    elif mode == "always":
        status = _t("keep_awake.status_always")
    elif working > 0:
        status = _t("keep_awake.status_auto_holding", n=working)
    else:
        status = _t("keep_awake.status_auto_idle")

    print(
        f"--{_t('menu.keep_awake')} | "
        "font=Menlo color=#888888 sfimage=cup.and.saucer.fill"
    )
    print(f"--  {status} | font=Menlo color=#888888")

    set_script = bin_dir / "keep-awake-set.sh"
    for value, key, icon in (
        ("off",    "keep_awake.mode_off",    "moon.fill"),
        ("auto",   "keep_awake.mode_auto",   "bolt.badge.automatic"),
        ("always", "keep_awake.mode_always", "bolt.fill"),
    ):
        # SwiftBar's ``checked=true`` paints a leading checkmark, giving
        # the user a glanceable confirmation of which mode is live
        # without us having to roll bullets into the label string.
        checked = "checked=true " if value == mode else ""
        print(
            f"--  {_t(key)} | "
            f"shell={_swiftbar_quote(str(set_script))} "
            f"param1={value} "
            f"{checked}"
            "terminal=false refresh=true "
            f"sfimage={icon}"
        )
