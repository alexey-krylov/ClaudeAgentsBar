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
    TranscriptMeta,
    _GIT_BRANCH_RE,
    _USAGE_BLOCK_RE,
    _clean_text,
    _content_to_title,
    _is_valid_session_id,
    _warn,
)

# --------------------------------------------------------------------------- #
# Sidecar readers and parsers                                                  #
# --------------------------------------------------------------------------- #


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

    We prefer the AI-generated ``ai-title`` event (the same value the VSCode
    sidebar displays). It's emitted right after the first turn, so capping
    the scan at :data:`core.JSONL_TITLE_SCAN_BYTES` finds it in essentially
    all real-world transcripts while keeping the per-tick cost bounded
    regardless of how much base64 attachment data later events drag in.

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
