"""ClaudeAgentsBar — SwiftBar plugin internals split across five modules.

The package is loaded by the tiny ``claude-agents.5s.py`` shim in the
repository root (SwiftBar dictates that filename). All real logic lives
here, organised by concern:

* :mod:`.core` — constants, ``Config``, i18n, domain types, pure helpers.
  Imports nothing from siblings.
* :mod:`.sidecars` — readers, parsers, locks and GC for the four
  ``agent-state.*`` sidecar files, plus JSONL transcript readers backed
  by a shared tail cache.
* :mod:`.render` — session composition (``collect_sessions``,
  ``build_session``) and SwiftBar menu emission.
* :mod:`.doctor` — ``claude-agents-bar doctor`` diagnostics.
* :mod:`.actions` — user-facing CLI actions wired up from
  ``bin/app/*.sh`` menu rows (stats today, print-strings, ack-fresh).

This file re-exports the public surface so that ``import
claude_agents_bar as plugin; plugin.X`` keeps working — the tests rely
on it, and it doubles as a contract for any future external caller.

``main()`` is the single CLI entry point invoked by the shim; subcommands
are dispatched by ``sys.argv[1]``.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace

from . import actions, core, doctor, keep_awake, render, sidecars

# --- Re-exports from .core -------------------------------------------------- #
from .core import (
    CONFIG,
    CLICKS_PATH,
    Config,
    DISMISS_PATH,
    FORGET_PATH,
    HOME,
    HookSnapshot,
    INTERACTIVE_ENTRYPOINTS,
    KEEP_AWAKE_MODE_PATH,
    KEEP_AWAKE_PID_PATH,
    PROJECTS_DIR,
    PLUGIN_DIR,
    QUIET_BYPASS_UNTIL_PATH,
    QUIET_UNTIL_PATH,
    RenderGroup,
    SIDECAR_PATH,
    STRINGS,
    Session,
    TranscriptMeta,
    _ANSI_ACK,
    _ANSI_FRESH,
    _ANSI_RESET,
    _ANSI_STALE,
    _ANSI_WAITING,
    _ANSI_WORKING,
    _CLICKS_LOCK_DIR,
    _EDITOR_URL_SCHEME_ALLOWLIST,
    _FORGET_LOCK_DIR,
    _KEEP_AWAKE_MODES,
    _LANG_CACHE,
    _QUIET_HOURS_RE,
    _QUIET_SILENCE_CHANNELS,
    _SIDECAR_LOCK_DIR,
    _classify,
    _clean_text,
    _content_to_title,
    _detect_system_lang,
    _format_context_left,
    _format_context_warning,
    _humanize_age,
    _is_valid_session_id,
    _lang,
    _next_occurrence,
    _normalize_lang,
    _parse_quiet_window,
    _project_name,
    _quiet_window_active,
    _resolve_lang,
    _shorten,
    _t,
    _t_for,
    _warn,
    is_quiet_now,
    quiet_status,
)

# --- Re-exports from .sidecars --------------------------------------------- #
from .sidecars import (
    _live_session_ids,
    _parse_clicks,
    _parse_sidecar,
    _read_jsonl_tail,
    _summarise_tool_use,
    _user_prompt_text,
    ack_fresh,
    current_git_branch,
    fallback_git_branch_from_jsonl,
    gc_clicks,
    gc_forget,
    gc_sidecar,
    last_tool_use_summary,
    last_usage_tokens,
    last_user_message_preview,
    read_clicks,
    read_dismiss_ts,
    read_forget,
    read_quiet_bypass_until,
    read_quiet_until,
    read_sidecar,
    read_transcript_meta,
)

# --- Re-exports from .render ----------------------------------------------- #
from .render import (
    _is_interactive,
    _menubar_icon_pieces,
    _print_footer,
    _print_menubar,
    _print_session_row,
    _resized_menubar_image,
    _swiftbar_quote,
    build_session,
    collect_sessions,
    iter_active_jsonls,
)

# --- Re-exports from .doctor ----------------------------------------------- #
from .doctor import (
    _EDITOR_SCHEME_APP,
    _REQUIRED_HOOK_EVENTS,
    _doctor_check_editor_app,
    _doctor_check_hook_registration,
    _doctor_check_sidecar_permissions,
    _doctor_check_swiftbar_plugin,
    _doctor_check_terminal_notifier,
    _doctor_check_tsv_freshness,
    _has_agent_state_hook,
    _run_doctor,
)

# --- Re-exports from .actions ---------------------------------------------- #
from .actions import (
    _collect_stats_today,
    _format_stats_dialog,
    _format_token_count,
    _local_midnight_ts,
    _model_sort_key,
    _print_shell_strings,
    _run_ack_fresh,
    _run_stats_today,
    _session_initial_cwd,
)


def main() -> int:
    """Render the menu once. Always exits zero so SwiftBar keeps polling.

    Recognised subcommands:

    * ``--ack-fresh`` runs the bulk acknowledgement (Tools → Acknowledge all).
    * ``--print-strings`` emits localized shell variables for bin/*.sh.
    * ``--doctor`` runs the deeper health checks behind ``claude-agents-bar doctor``.
    * ``--stats-today`` shows today's activity summary in a modal dialog.
    * ``--keep-awake <mode>`` sets the keep-awake mode (off/auto/always).
    * ``--keep-awake-shutdown`` kills any caffeinate we own (used by teardown).
    * ``--multi-workspace <on|off>`` toggles the window-focus behaviour
      (writes the :data:`core.MULTI_WORKSPACE_MODE_PATH` sidecar).

    Anything else is treated as a render.
    """
    if len(sys.argv) > 1 and sys.argv[1] == "--ack-fresh":
        return actions._run_ack_fresh()
    if len(sys.argv) > 1 and sys.argv[1] == "--print-strings":
        actions._print_shell_strings()
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--doctor":
        return doctor._run_doctor()
    if len(sys.argv) > 1 and sys.argv[1] == "--stats-today":
        return actions._run_stats_today()
    if len(sys.argv) > 1 and sys.argv[1] == "--keep-awake":
        mode = sys.argv[2] if len(sys.argv) > 2 else ""
        rc = keep_awake.write_mode(mode)
        if rc == 0:
            # Reconcile immediately so a click on *Off* tears down the
            # running caffeinate without waiting for the next tick.
            try:
                keep_awake.reconcile(render.collect_sessions(int(time.time())))
            except Exception as exc:
                core._warn(f"keep_awake: post-set reconcile failed: {exc}")
        return rc
    if len(sys.argv) > 1 and sys.argv[1] == "--keep-awake-shutdown":
        keep_awake.shutdown()
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--multi-workspace":
        arg = sys.argv[2] if len(sys.argv) > 2 else ""
        if arg not in ("on", "off"):
            core._warn(f"multi_workspace: refusing invalid value {arg!r}")
            return 1
        return core.write_multi_workspace_mode(arg == "on")
    try:
        sessions = render.collect_sessions(int(time.time()))
        render.render(sessions)
        # Keep-awake reconcile rides on the render tick — we already paid
        # to enumerate sessions for the menu, and the decision logic only
        # needs ``hook_state``. Wrapping in try/except so a reconcile bug
        # never takes the menu down.
        try:
            keep_awake.reconcile(sessions)
        except Exception as exc:
            core._warn(f"keep_awake: reconcile failed: {exc}")
    except Exception as exc:
        # Catch-all so SwiftBar never sees a Python traceback in the menu.
        print("⚠️ | color=red")
        print("---")
        print(f"{core._t('error.plugin', exc=exc)} | color=red")
    return 0
