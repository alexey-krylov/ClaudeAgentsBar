#!/bin/bash
#
# ClaudeAgentsBar idle-session reminder (spec 0008).
#
# UNLIKE notify-stop.sh / notify-wait.sh this is NOT a Claude Code hook — it
# is fired by the plugin itself (claude_agents_bar/idle_reminders.py) on the
# SwiftBar tick when a 🟢 fresh (finished, not yet clicked) session has gone
# unread past its next escalation interval. There is no Claude Code event for
# "N minutes after the session finished", and the project runs no daemon, so
# the periodic plugin tick is the only place this can come from.
#
# Because the plugin is the caller, the session id and cwd arrive as
# positional arguments (NOT a JSON payload on stdin), and the on/off + timing
# decision (notify_idle_interval_min, the doubling schedule, the fresh-window
# bound) lives on the Python side — this script just speaks. It can also be
# run by hand for a smoke test: `notify-idle.sh <session-id> <cwd>`.
#
# Composition mirrors notify-wait.sh (phrase → name → summary, "name — summary"
# banner) so a reminder is recognisably about a *waiting* session; only the
# sound (notify_sound_idle), phrase list (notify_idle_phrases) and banner
# title differ.
#
# Requires: terminal-notifier  (brew install terminal-notifier)
# Optional: jq  (for config/transcript parsing); afplay/say are macOS builtins.
#
# Config keys read from the ClaudeAgentsBar config.json (all optional):
#   notify_sound_idle      string  "Submarine" — built-in /System/Library/Sounds
#                                             name, absolute path, ~-path, or
#                                             null to skip the chime entirely
#   notify_idle_phrases    array   [...]    — phrases spoken aloud and shown in
#                                             the banner; one chosen at random
#   notify_voice           string  null     — say(1) voice (shared with the
#                                             other notify hooks)
#   notify_summary_marker  string  "-- "    — closing-line marker; the latest
#                                             turn's name+summary name the
#                                             session aloud and in the banner
#   quiet_hours / quiet_hours_silences      — same silence window as the other
#                                             hooks
#   editor_url_scheme      string  "vscode://" — used to build the deeplink so
#                                             the banner click jumps into the
#                                             unread session

set -u

# ── Source the shared helpers ────────────────────────────────────────────────
# Not symlinked into ~/.claude/hooks (it's invoked from the plugin's own hooks
# dir), but the symlink-following resolver is harmless and keeps this identical
# to the registered hooks.
__target="${BASH_SOURCE[0]}"
while [ -L "$__target" ]; do
    __link=$(/usr/bin/readlink -- "$__target")
    case "$__link" in
        /*) __target="$__link" ;;
        *)  __target="$(cd "$(dirname "$__target")" && pwd -P)/$__link" ;;
    esac
done
__HOOK_DIR="$(cd "$(dirname "$__target")" && pwd -P)"
if [ ! -f "${__HOOK_DIR}/_notify-common.sh" ]; then
    echo "[notify-idle] missing ${__HOOK_DIR}/_notify-common.sh; re-run setup" >&2
    exit 0
fi
# shellcheck source=/dev/null
. "${__HOOK_DIR}/_notify-common.sh"

# ── Read config ──────────────────────────────────────────────────────────────
SCHEME=$(_cfg_string   "editor_url_scheme"     "vscode://")
MULTI_WS=$(_multi_workspace_enabled)
SETTLE=$(_cfg_number   "editor_focus_settle_sec" "0.1")

# Custom audio (spec 0001). Default chime for idle reminders is Submarine — a
# soft, distinct ping, set apart from Hero (done) and Funk (awaiting) so the
# reminder reads as its own kind of nudge.
SOUND_RAW=$(_cfg_string_or_null "notify_sound_idle" "Submarine")
SOUND_PATH=$(_resolve_sound "$SOUND_RAW")
VOICE=$(_cfg_string             "notify_voice"      "")

# Spoken summary marker (spec 0005). The session has finished, so its latest
# turn carries the `*-- Name - Summary*` closing line — name + summary tell the
# user which unread session this is and what it was doing.
MARKER=$(_cfg_string_or_null    "notify_summary_marker" "-- ")

# Quiet-hours gate (spec 0002).
_compute_quiet_state

# Notification-audio master switch (Tools → Notifications). "Banner only"
# mutes both audio channels regardless of quiet hours; the banner still fires.
if [ "$(_notify_audio_enabled)" = "false" ]; then
    SUPPRESS_SOUND=true
    SUPPRESS_VOICE=true
fi

# ── Positional input (from the plugin, not a hook payload) ───────────────────
SID="${1:-}"
CWD="${2:-}"

SESSION_URL=""
[ -n "$SID" ] && SESSION_URL="${SCHEME}anthropic.claude-code/open?session=${SID}"

# Locate the transcript by session id under ~/.claude/projects/<slug>/<sid>.jsonl
# (the plugin doesn't pass the path; same glob fallback as notify-wait.sh).
TRANSCRIPT=""
case "$SID" in
    "" | *[!A-Za-z0-9_-]* ) : ;;   # ignore ids outside the safe glob alphabet
    *) for __f in "$HOME"/.claude/projects/*/"$SID".jsonl; do
           [ -f "$__f" ] && { TRANSCRIPT="$__f"; break; }
       done ;;
esac

# ── Pick a random phrase ─────────────────────────────────────────────────────
PHRASE=$(_pick_phrase "notify_idle_phrases" \
    "Don't forget me" "Still unread" "Pending review" "Your turn")

# ── Name + summary of the latest completed marker turn ───────────────────────
# The session has finished, so its latest turn closed with the marker line.
NAME=""
SUMMARY=""
if [ -n "$MARKER" ] && [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    { IFS= read -r NAME; IFS= read -r SUMMARY; } < <(_marker_fields_latest "$TRANSCRIPT" "$MARKER")
fi

# Speech reads the reminder phrase, then the name, then the summary ("Still
# unread. Чиню баг. нашёл причину"). Banner shows name (+ summary) when known.
SAY_TEXT="$PHRASE"
[ -n "$NAME" ]    && SAY_TEXT="$SAY_TEXT${_SAY_SEP}$NAME"
[ -n "$SUMMARY" ] && SAY_TEXT="$SAY_TEXT${_SAY_SEP}$SUMMARY"
BANNER_MSG="$PHRASE"
if [ -n "$NAME" ] && [ -n "$SUMMARY" ]; then
    BANNER_MSG="$NAME — $SUMMARY"
elif [ -n "$NAME" ]; then
    BANNER_MSG="$NAME"
elif [ -n "$SUMMARY" ]; then
    BANNER_MSG="$SUMMARY"
fi

# ── Chime + speech + banner (shared emit) ────────────────────────────────────
# Click jumps straight to the unread session so the reminder is actionable.
_emit_notification "Claude session unread" "$BANNER_MSG" "$SAY_TEXT" \
    "$SESSION_URL" "$SID" "$CWD"
