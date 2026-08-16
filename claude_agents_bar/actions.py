"""User-facing actions invoked from the menu UI.

Three things live here:

* ``_run_stats_today`` — *Tools → Stats today*, an AppleScript dialog
  showing today's aggregate (sessions / turns / tokens / top projects).
* ``_print_shell_strings`` — emits localized ``MSG_*`` variables for
  ``bin/app/*.sh`` so the AppleScript dialogs stay translated without
  duplicating the string tables on the shell side.
* The dispatcher wires (``--ack-fresh``) — the heavy lifting still lives
  in :mod:`claude_agents_bar.sidecars`, this module just owns the CLI
  surface that ``bin/app/ack-fresh.sh`` calls.

Diagnostics for *claude-agents-bar doctor* live in
:mod:`claude_agents_bar.doctor` instead; that's a console diagnostic,
not a UI action.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import core, sidecars
from .core import (
    JSONL_TAIL_BYTES,
    _USAGE_BLOCK_RE,
    _project_name,
    _t,
    _warn,
)


# --------------------------------------------------------------------------- #
# Stats today                                                                  #
# --------------------------------------------------------------------------- #


def _local_midnight_ts(now: int) -> int:
    """Unix timestamp of today's local 00:00, derived from ``now``.

    Used as the lower bound when filtering JSONL transcripts by mtime
    for the "Stats today" summary.
    """
    local = time.localtime(now)
    midnight = time.struct_time((
        local.tm_year, local.tm_mon, local.tm_mday,
        0, 0, 0,
        local.tm_wday, local.tm_yday, local.tm_isdst,
    ))
    return int(time.mktime(midnight))


def _format_token_count(n: int) -> str:
    """Render a token count compactly: 1234 → '1.2K', 1_234_567 → '1.2M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _collect_stats_today(now: int) -> dict:
    """Aggregate today's activity across every JSONL under ``~/.claude/projects``.

    Per-transcript fields surfaced:

    * ``sessions`` — count of JSONLs whose mtime is on or after local
      midnight. Approximates "sessions active today" — a session
      started yesterday but still being used today counts here, which
      is the intent.
    * ``turns`` — total number of real user prompts (``type:"user"``
      with a parseable text chunk that isn't a tool result or system
      wrapper) seen today across all JSONLs.
    * ``total_tokens`` / ``prompt_tokens`` / ``cache_read_tokens`` —
      sums from each transcript's most recent ``usage`` block, gives
      a rough "how much context did Claude crunch through today"
      number. Cache reads dominate this; the cache-hit ratio is
      derived from them.
    * ``top_projects`` — list of ``(project_name, turn_count)`` tuples
      sorted descending, capped at three.
    * ``model_counts`` — dict mapping the *full* model string
      (``"claude-opus-4-7"``, ``"claude-haiku-4-5-20251001"``,
      ``"openrouter/gpt-4"``…) to the number of today's sessions whose
      most recent assistant event ran on that model. Sessions with no
      parseable model in their tail (older JSONL, or a transcript that
      only has the user's first prompt) don't contribute — same
      fail-soft policy as everything else here.
    * ``subagents`` — count of subagent runs whose JSONL was modified
      on or after midnight, across every ``<session>/subagents/``
      directory. One subagent JSONL = one ``Task`` invocation.
    * ``subagent_model_counts`` — same shape as ``model_counts``, but
      for subagents. Useful to see how the ``Task`` tool routes work
      across model families (haiku for cheap lookups, opus for harder
      reasoning, etc.).

    Failures stay local: an unreadable JSONL is skipped rather than
    aborting the aggregate. Matches the same fail-soft policy as the
    render path.
    """
    midnight = _local_midnight_ts(now)
    sessions = 0
    turns = 0
    total_tokens = 0
    prompt_tokens = 0
    cache_read_tokens = 0
    per_project_turns: dict[str, int] = {}
    model_counts: dict[str, int] = {}
    subagents = 0
    subagent_model_counts: dict[str, int] = {}

    if not core.PROJECTS_DIR.exists():
        return {
            "sessions": 0,
            "turns": 0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "cache_read_tokens": 0,
            "top_projects": [],
            "model_counts": model_counts,
            "subagents": 0,
            "subagent_model_counts": subagent_model_counts,
        }

    for project_dir in core.PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                mtime = int(jsonl.stat().st_mtime)
            except OSError:
                continue
            if mtime < midnight:
                continue
            sessions += 1
            project_turn_count = 0
            try:
                with jsonl.open("rb") as f:
                    for raw in f:
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
                        if sidecars._user_prompt_text(message.get("content")):
                            project_turn_count += 1
            except OSError:
                continue
            turns += project_turn_count
            # Single tail-read covers all three token counters. The
            # ``usage`` block we care about is always the last one in
            # the file (Claude Code appends sequentially), so a bounded
            # tail is enough and we don't pay for a second read just
            # to split the total into prompt vs cache.
            try:
                size = jsonl.stat().st_size
                with jsonl.open("rb") as f:
                    f.seek(max(0, size - JSONL_TAIL_BYTES))
                    data = f.read()
                matches = _USAGE_BLOCK_RE.findall(data)
                if matches:
                    inp, cache_creation, cache_read = (int(x) for x in matches[-1])
                    total_tokens += inp + cache_creation + cache_read
                    prompt_tokens += inp
                    cache_read_tokens += cache_read
                model_matches = core._MODEL_RE.findall(data)
                if model_matches:
                    model_id = model_matches[-1].decode("utf-8", errors="replace")
                    model_counts[model_id] = model_counts.get(model_id, 0) + 1
            except OSError:
                pass
            project_name = _project_name(_session_initial_cwd(jsonl), project_dir.name)
            per_project_turns[project_name] = (
                per_project_turns.get(project_name, 0) + project_turn_count
            )

        # Subagents — each ``Task`` invocation lands in a sibling
        # ``<session-id>/subagents/agent-<id>.jsonl``. Counting JSONLs
        # whose mtime crossed midnight gives one row per subagent run.
        for sub_jsonl in project_dir.glob("*/subagents/agent-*.jsonl"):
            try:
                sub_mtime = int(sub_jsonl.stat().st_mtime)
            except OSError:
                continue
            if sub_mtime < midnight:
                continue
            subagents += 1
            try:
                sub_size = sub_jsonl.stat().st_size
                with sub_jsonl.open("rb") as f:
                    f.seek(max(0, sub_size - JSONL_TAIL_BYTES))
                    sub_data = f.read()
                sub_model_matches = core._MODEL_RE.findall(sub_data)
                if sub_model_matches:
                    model_id = sub_model_matches[-1].decode(
                        "utf-8", errors="replace",
                    )
                    subagent_model_counts[model_id] = (
                        subagent_model_counts.get(model_id, 0) + 1
                    )
            except OSError:
                pass

    top_projects = sorted(
        per_project_turns.items(), key=lambda kv: kv[1], reverse=True,
    )[:3]
    return {
        "sessions": sessions,
        "turns": turns,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "cache_read_tokens": cache_read_tokens,
        "top_projects": top_projects,
        "model_counts": model_counts,
        "subagents": subagents,
        "subagent_model_counts": subagent_model_counts,
    }


#: Stable-ish ranking for Claude families in the Stats today dialog.
#: Models within the same family share this rank, then break ties on
#: the model id string so two opus releases stay adjacent and ordered.
#: Non-Claude / unparseable strings sort after all Claude families.
#: Keep in step with :data:`core._MODEL_FAMILY_BADGES` — a family that has
#: a badge but no rank here still renders its glyph, it just sorts into the
#: non-Claude tail alongside OpenRouter strings (which is how Fable read
#: before 1.4.2).
_MODEL_FAMILY_RANK: dict[str, int] = {
    "claude-opus-": 0,
    "claude-sonnet-": 1,
    "claude-haiku-": 2,
    "claude-fable-": 3,
}


def _model_sort_key(model_id: str) -> tuple[int, str]:
    """Sort key for the Models block: family rank, then full model id."""
    for prefix, rank in _MODEL_FAMILY_RANK.items():
        if model_id.startswith(prefix):
            return (rank, model_id)
    return (len(_MODEL_FAMILY_RANK), model_id)


def _session_initial_cwd(jsonl: Path) -> str:
    """Return the ``cwd`` from the first JSONL event, or ``""``.

    Cheaper than ``read_transcript_meta`` when all we need is the cwd
    to name the project. Reads at most the first 4 KB — the
    ``SessionStart`` event always lands in the opening bytes.
    """
    try:
        with jsonl.open("rb") as f:
            for raw in f.read(4096).splitlines():
                if b'"cwd"' not in raw:
                    continue
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(event.get("cwd"), str):
                    return event["cwd"]
    except OSError:
        pass
    return ""


def _format_stats_dialog(stats: dict) -> str:
    """Format the aggregate as the multi-line body for the AppleScript dialog."""
    lines: list[str] = []
    lines.append(_t("stats.sessions", n=stats["sessions"]))
    lines.append(_t("stats.turns", n=stats["turns"]))
    total = stats["total_tokens"]
    prompt = stats["prompt_tokens"]
    cache_read = stats["cache_read_tokens"]
    if total > 0:
        cache_hit_pct = round(cache_read / total * 100) if total else 0
        lines.append(_t(
            "stats.tokens",
            total=_format_token_count(total),
            prompt=_format_token_count(prompt),
            cache_hit=cache_hit_pct,
        ))
    else:
        lines.append(_t("stats.tokens_empty"))
    model_counts = stats.get("model_counts") or {}
    if model_counts:
        lines.append("")
        lines.append(_t("stats.models"))
        for model_id, count in sorted(
            model_counts.items(), key=lambda kv: _model_sort_key(kv[0]),
        ):
            badge = core._model_badge(model_id, None)
            lines.append(f"  {badge} {model_id}: {count}")
    subagents = stats.get("subagents", 0)
    if subagents:
        lines.append("")
        lines.append(_t("stats.subagents", n=subagents))
        for model_id, count in sorted(
            (stats.get("subagent_model_counts") or {}).items(),
            key=lambda kv: _model_sort_key(kv[0]),
        ):
            badge = core._model_badge(model_id, None)
            lines.append(f"  {badge} {model_id}: {count}")
    top = stats["top_projects"]
    if top:
        lines.append("")
        lines.append(_t("stats.top_projects"))
        for name, count in top:
            lines.append(f"  {name} ({_t('stats.turns_short', n=count)})")
    return "\n".join(lines)


def _run_stats_today() -> int:
    """Show today's activity summary in a modal AppleScript dialog."""
    stats = _collect_stats_today(int(time.time()))
    body = _format_stats_dialog(stats)
    title = _t("stats.title")
    # AppleScript wrapper is read from stdin and dialog values arrive as
    # argv elements — same pattern as bin/app/delete-session.sh — so the
    # body (which may contain user-controlled project names from disk)
    # can't escape into AppleScript source.
    script = (
        'on run argv\n'
        '  set theTitle to item 1 of argv\n'
        '  set theBody to item 2 of argv\n'
        '  try\n'
        '    display dialog theBody with title theTitle '
        'buttons {"OK"} default button "OK"\n'
        '  end try\n'
        'end run'
    )
    try:
        subprocess.run(
            ["/usr/bin/osascript", "-e", script, "--", title, body],
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"stats-today dialog failed: {exc}")
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Shell strings export                                                         #
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

    table = core.STRINGS.get(core._lang(), core.STRINGS["en"])
    for key in sorted(table):
        if not key.startswith("dialog."):
            continue
        # Fall back to English for individual missing entries (defensive —
        # full tables today, but cheap insurance against future drift).
        value = table.get(key) or core.STRINGS["en"].get(key, "")
        var = "MSG_" + key.replace(".", "_").upper()
        print(f"{var}={shlex.quote(value)}")


# --------------------------------------------------------------------------- #
# CLI wrappers around sidecar mutations                                        #
# --------------------------------------------------------------------------- #


def _run_ack_fresh() -> int:
    """Thin CLI wrapper around :func:`sidecars.ack_fresh`.

    Lives here rather than in ``sidecars`` so the sidecar module stays
    a pure data layer — every CLI entry point that ``bin/app/*.sh``
    calls is owned by ``actions``.
    """
    try:
        sidecars.ack_fresh(int(time.time()))
    except Exception as exc:
        _warn(f"ack-fresh failed: {exc}")
        return 1
    return 0
