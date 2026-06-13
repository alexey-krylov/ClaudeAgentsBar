#!/bin/bash
#
# ClaudeAgentsBar completion-notification hook.
#
# Fires on every Stop event. Plays a chime, speaks a random phrase, and pops
# a terminal-notifier banner so the user knows a session finished without
# having to glance at the menu bar.
#
# Requires: terminal-notifier  (brew install terminal-notifier)
# Optional: jq  (for config/transcript parsing); afplay/say are macOS builtins.
#
# Config keys read from the ClaudeAgentsBar config.json (all optional):
#   notify_on_stop         bool    true     — false to silence all notifications
#   notify_threshold_sec   int     30       — skip sessions whose last user
#                                             turn was less than this many
#                                             seconds ago (avoids noise from
#                                             quick one-liner exchanges)
#   notify_phrases         array   [...]    — phrases spoken aloud and shown in
#                                             the banner; one is picked at random
#   notify_sound_stop      string  "Hero"   — built-in /System/Library/Sounds
#                                             name, absolute path, ~-path, or
#                                             null to skip the chime entirely
#   notify_voice           string  null     — say(1) voice name; null/absent
#                                             uses the system default; "off"
#                                             skips speech entirely
#   notify_summary_marker  string  "-- "    — when the LAST line of the
#                                             assistant's reply starts with
#                                             this prefix (markdown italic/bold
#                                             wrappers stripped first), say(1)
#                                             speaks the random phrase followed
#                                             by that text ("Done. <summary>"),
#                                             and the banner shows the text
#                                             alone. No such line, or null/""
#                                             marker, falls back to just the
#                                             phrase in both.
#   quiet_hours            string  null     — "HH:MM-HH:MM" window during
#                                             which notifications are silenced
#                                             per `quiet_hours_silences`
#   quiet_hours_silences   array   [snd,vc] — channels suppressed while quiet:
#                                             subset of ["sound","voice","banner"];
#                                             default mutes audio, keeps banner
#   editor_url_scheme      string  "vscode://" — used to build the deeplink;
#                                             mirrors the same key the plugin
#                                             uses for row clicks

set -u

# ── Source the shared helpers ────────────────────────────────────────────────
# The hook is symlinked into ~/.claude/hooks/; resolve through the symlink
# so the sibling _notify-common.sh in the repo is what gets sourced.
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
    # Either an incomplete install (legacy zip that predates spec 0001)
    # or someone deleted the include. Silent exit rather than failing
    # noisily — Claude Code captures hook stderr and surfaces it in the
    # session transcript, which would be confusing for the user.
    echo "[notify-stop] missing ${__HOOK_DIR}/_notify-common.sh; re-run setup" >&2
    exit 0
fi
# shellcheck source=/dev/null
. "${__HOOK_DIR}/_notify-common.sh"

# ── Read config ──────────────────────────────────────────────────────────────
NOTIFY_ON=$(_cfg_bool  "notify_on_stop"        "true")
[ "$NOTIFY_ON" = "false" ] && exit 0

THRESHOLD=$(_cfg_int   "notify_threshold_sec"  "30")
SCHEME=$(_cfg_string   "editor_url_scheme"     "vscode://")
MULTI_WS=$(_multi_workspace_enabled)
SETTLE=$(_cfg_number   "editor_focus_settle_sec" "0.1")

# Custom audio (spec 0001).
SOUND_RAW=$(_cfg_string_or_null "notify_sound_stop" "Hero")
SOUND_PATH=$(_resolve_sound "$SOUND_RAW")
VOICE=$(_cfg_string             "notify_voice"      "")

# Spoken summary (spec 0005). Prefix of the LAST line of the assistant's
# reply that say(1) reads aloud (the text after it). Default "-- " — the
# feature is on out of the box, but inert until the assistant actually ends
# a reply with such a line. _cfg_string_or_null lets an explicit null / ""
# disable it (→ phrase), while an absent key keeps the default.
MARKER=$(_cfg_string_or_null    "notify_summary_marker" "-- ")

# Quiet-hours gate (spec 0002). Sets QUIET_NOW + SUPPRESS_SOUND/VOICE/BANNER.
_compute_quiet_state

# Notification-audio master switch (Tools → Notifications). "Banner only"
# mutes both audio channels regardless of quiet hours; the banner still fires.
if [ "$(_notify_audio_enabled)" = "false" ]; then
    SUPPRESS_SOUND=true
    SUPPRESS_VOICE=true
fi

# ── Parse hook payload ───────────────────────────────────────────────────────
INPUT=$(cat)
TRANSCRIPT=$(/usr/bin/jq -r '.transcript_path // empty' <<<"$INPUT" 2>/dev/null)
SID=$(/usr/bin/jq -r '.session_id // empty'    <<<"$INPUT" 2>/dev/null)
CWD=$(/usr/bin/jq -r '.cwd // empty'           <<<"$INPUT" 2>/dev/null)

SESSION_URL=""
[ -n "$SID" ] && SESSION_URL="${SCHEME}anthropic.claude-code/open?session=${SID}"

# ── Threshold: skip short turns ──────────────────────────────────────────────
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    LAST_USER_TS=$(tail -r "$TRANSCRIPT" 2>/dev/null \
        | /usr/bin/jq -r \
            'select(.type=="user")
             | select((.message.content | type) == "array")
             | select(.message.content[0].type? == "text")
             | .timestamp // empty' 2>/dev/null \
        | head -n 1)
    if [ -n "$LAST_USER_TS" ]; then
        TS_TRIM="${LAST_USER_TS%%.*}"
        LAST_EPOCH=$(date -j -u -f "%Y-%m-%dT%H:%M:%S" "$TS_TRIM" +%s 2>/dev/null)
        if [ -n "$LAST_EPOCH" ]; then
            ELAPSED=$(( $(date +%s) - LAST_EPOCH ))
            [ "$ELAPSED" -lt "$THRESHOLD" ] && exit 0
        fi
    fi
fi

# ── Extract task title from transcript ───────────────────────────────────────
TASK=""
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    TASK=$(tail -r "$TRANSCRIPT" 2>/dev/null \
        | /usr/bin/jq -r 'select(.type=="ai-title") | .aiTitle // empty' 2>/dev/null \
        | head -n 1)
    if [ -z "$TASK" ]; then
        TASK=$(tail -r "$TRANSCRIPT" 2>/dev/null \
            | /usr/bin/jq -r \
                'select(.type=="user")
                 | select((.message.content | type) == "array")
                 | select(.message.content[0].type? == "text")
                 | .message.content[0].text' 2>/dev/null \
            | head -n 1)
    fi
fi
TITLE=$(printf '%s' "$TASK" | tr '\n' ' ' | head -c 240)
TITLE="${TITLE:-Done}"

# ── Pick a random phrase ─────────────────────────────────────────────────────
PHRASE=$(_pick_phrase "notify_phrases" \
    "Check it" "Done" "Ready for review" "Your turn")

# ── Extract the spoken summary (spec 0005) ───────────────────────────────────
# The LAST non-blank line of the assistant's reply, after the marker, if any —
# see _extract_summary in _notify-common.sh (shared with the Remind action).
# Any miss leaves SUMMARY empty — a silent, non-fatal fallback to the phrase.
SUMMARY=$(_extract_summary "${TRANSCRIPT:-}" "$MARKER")

# Speech keeps the random phrase and appends the summary when present, so it
# reads as a natural sentence ("Done. Migrated the auth module"). The banner
# shows just the summary when present, else the phrase.
SAY_TEXT="$PHRASE"
[ -n "$SUMMARY" ] && SAY_TEXT="$PHRASE. $SUMMARY"
BANNER_MSG="$PHRASE"
[ -n "$SUMMARY" ] && BANNER_MSG="$SUMMARY"

# ── Chime + speech + banner (shared emit) ────────────────────────────────────
# Banner title is the task title from the transcript; the click jumps to the
# session in the editor. $BANNER_MSG is the extracted summary when present,
# else the random phrase.
_emit_notification "$TITLE" "$BANNER_MSG" "$SAY_TEXT" \
    "$SESSION_URL" "$SID" "$CWD"
