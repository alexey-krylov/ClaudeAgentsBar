#!/bin/bash
#
# ClaudeAgentsBar permission-prompt notification hook.
#
# Fires on every PermissionRequest event (and optionally on Notification —
# see hooks/settings-hooks.json). Plays a short chime, speaks a random
# phrase, and pops a terminal-notifier banner so the user knows Claude is
# blocked on a tool-approval dialog without having to glance at the menu
# bar.
#
# Requires: terminal-notifier  (brew install terminal-notifier)
# Optional: jq  (for config/payload parsing); afplay/say are macOS builtins.
#
# Config keys read from the ClaudeAgentsBar config.json (all optional):
#   notify_on_wait         bool    true     — false to silence permission
#                                             notifications
#   notify_wait_phrases    array   [...]    — phrases spoken aloud and shown
#                                             in the banner; one chosen at
#                                             random per event
#   notify_sound_wait      string  "Funk"   — built-in /System/Library/Sounds
#                                             name, absolute path, ~-path, or
#                                             null to skip the chime entirely
#   notify_voice           string  null     — say(1) voice name; null/absent
#                                             uses the system default; "off"
#                                             skips speech entirely (shared
#                                             with notify-stop.sh)
#   quiet_hours            string  null     — "HH:MM-HH:MM" window during
#                                             which notifications are silenced
#                                             per `quiet_hours_silences`
#   quiet_hours_silences   array   [snd,vc] — channels suppressed while quiet:
#                                             subset of ["sound","voice","banner"];
#                                             default mutes audio, keeps banner
#   editor_url_scheme      string  "vscode://" — used to build the deeplink
#                                             so the banner click jumps
#                                             straight into the waiting
#                                             session; mirrors the same key
#                                             the plugin uses for row clicks

set -u

# ── Source the shared helpers ────────────────────────────────────────────────
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
    echo "[notify-wait] missing ${__HOOK_DIR}/_notify-common.sh; re-run setup" >&2
    exit 0
fi
# shellcheck source=/dev/null
. "${__HOOK_DIR}/_notify-common.sh"

# ── Read config ──────────────────────────────────────────────────────────────
NOTIFY_ON=$(_cfg_bool  "notify_on_wait"        "true")
[ "$NOTIFY_ON" = "false" ] && exit 0

SCHEME=$(_cfg_string   "editor_url_scheme"     "vscode://")
MULTI_WS=$(_multi_workspace_enabled)
SETTLE=$(_cfg_number   "editor_focus_settle_sec" "0.1")

# Custom audio (spec 0001). Default chime for permission prompts is Funk —
# shorter and softer than Hero, matching the existing semantic distinction
# "needs your attention" vs "task complete".
SOUND_RAW=$(_cfg_string_or_null "notify_sound_wait" "Funk")
SOUND_PATH=$(_resolve_sound "$SOUND_RAW")
VOICE=$(_cfg_string             "notify_voice"      "")

# Spoken summary marker (spec 0005). Shared with notify-stop.sh / the Remind
# action: the assistant's `*-- Name - Summary*` closing line. Here we read both
# fields to name the blocked session aloud (phrase → name → summary). Empty /
# null disables, falling back to the phrase alone.
MARKER=$(_cfg_string_or_null    "notify_summary_marker" "-- ")

# Quiet-hours gate (spec 0002).
_compute_quiet_state

# Notification-audio master switch (Tools → Notifications). "Banner only"
# mutes both audio channels regardless of quiet hours; the banner still fires.
if [ "$(_notify_audio_enabled)" = "false" ]; then
    SUPPRESS_SOUND=true
    SUPPRESS_VOICE=true
fi

# ── Parse hook payload ───────────────────────────────────────────────────────
INPUT=$(cat)
SID=$(/usr/bin/jq -r '.session_id // empty' <<<"$INPUT" 2>/dev/null)
CWD=$(/usr/bin/jq -r '.cwd // empty'        <<<"$INPUT" 2>/dev/null)

SESSION_URL=""
[ -n "$SID" ] && SESSION_URL="${SCHEME}anthropic.claude-code/open?session=${SID}"

# Locate the transcript for the name/summary lookup. PermissionRequest payloads
# usually carry transcript_path; if absent or stale, glob by session id under
# ~/.claude/projects/<slug>/<sid>.jsonl (same fallback as remind-session.sh).
TRANSCRIPT=$(/usr/bin/jq -r '.transcript_path // empty' <<<"$INPUT" 2>/dev/null)
if { [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; } && [ -n "$SID" ]; then
    case "$SID" in
        "" | *[!A-Za-z0-9_-]* ) : ;;   # ignore ids outside the safe glob alphabet
        *) for __f in "$HOME"/.claude/projects/*/"$SID".jsonl; do
               [ -f "$__f" ] && { TRANSCRIPT="$__f"; break; }
           done ;;
    esac
fi

# ── Pick a random phrase ─────────────────────────────────────────────────────
# Bash 3.2 (system /bin/bash on macOS) lacks mapfile, so we use a while loop.
_emit_phrases() {
    [ -f "$_CAB_CONFIG" ] || return
    /usr/bin/jq -r '.notify_wait_phrases // empty | .[]?' "$_CAB_CONFIG" 2>/dev/null
}
PHRASES=()
while IFS= read -r _phrase; do
    [ -n "$_phrase" ] && PHRASES+=("$_phrase")
done < <(_emit_phrases)

if [ "${#PHRASES[@]}" -eq 0 ]; then
    PHRASES=("Need instructions" "Awaiting input" "Decision needed" "I'm blocked")
fi

PHRASE="${PHRASES[$RANDOM % ${#PHRASES[@]}]}"

# ── Name + summary of the latest completed marker turn ───────────────────────
# Two-field marker line `*-- Name - Summary*`. At a permission prompt the
# current turn hasn't closed with its marker yet, so these resolve to the
# previous completed turn — enough to say which session is blocked and what it
# was doing. Marker off / no marker turn → both empty → phrase only.
NAME=""
SUMMARY=""
if [ -n "$MARKER" ] && [ -n "${TRANSCRIPT:-}" ] && [ -f "$TRANSCRIPT" ]; then
    { IFS= read -r NAME; IFS= read -r SUMMARY; } < <(_marker_fields_latest "$TRANSCRIPT" "$MARKER")
fi

# Speech reads the awaiting phrase, then the name, then the summary ("I'm
# blocked. Чиню баг. нашёл причину"). Banner shows name (+ summary) when known.
SAY_TEXT="$PHRASE"
[ -n "$NAME" ]    && SAY_TEXT="$SAY_TEXT. $NAME"
[ -n "$SUMMARY" ] && SAY_TEXT="$SAY_TEXT. $SUMMARY"
BANNER_MSG="$PHRASE"
if [ -n "$NAME" ] && [ -n "$SUMMARY" ]; then
    BANNER_MSG="$NAME — $SUMMARY"
elif [ -n "$NAME" ]; then
    BANNER_MSG="$NAME"
elif [ -n "$SUMMARY" ]; then
    BANNER_MSG="$SUMMARY"
fi

# ── Sound + speech (fire-and-forget, never block the hook) ───────────────────
if [ "$SUPPRESS_SOUND" = "false" ] && [ -n "$SOUND_PATH" ]; then
    afplay "$SOUND_PATH" >/dev/null 2>&1 &
fi
if [ "$SUPPRESS_VOICE" = "false" ] && [ "$VOICE" != "off" ]; then
    if [ -n "$VOICE" ]; then
        (sleep 1 && say -v "$VOICE" "$SAY_TEXT") >/dev/null 2>&1 &
    else
        (sleep 1 && say "$SAY_TEXT") >/dev/null 2>&1 &
    fi
fi
disown 2>/dev/null || true

# ── Banner notification ──────────────────────────────────────────────────────
# Click jumps straight to the session — the user almost always wants to act
# on the prompt, not just acknowledge it. With multi_workspace_mode on and
# the session's cwd + a window-raising .app known, the click runs
# raise-and-open.sh (via -execute) so it lands in the window matching the
# workspace instead of whatever is frontmost; otherwise (focus off, or args
# missing) it falls back to a plain -open.
if [ "$SUPPRESS_BANNER" = "false" ]; then
    ICON="${HOME}/.claude/hooks/assets/claude-icon.png"
    NOTIFIER_ARGS=(-title "Claude awaiting input" -subtitle "Claude Code" -message "$BANNER_MSG")
    [ -f "$ICON" ]         && NOTIFIER_ARGS+=(-contentImage "$ICON")
    if [ -n "$SESSION_URL" ]; then
        EDITOR_APP=$(_editor_app_for_scheme "$SCHEME")
        if [ "$MULTI_WS" = "true" ] && [ -n "$CWD" ] && [ -n "$EDITOR_APP" ] \
                && [ -d "$CWD" ] && [ -d "$EDITOR_APP" ]; then
            NOTIFIER_ARGS+=(-execute "$(_raise_open_cmd "$SESSION_URL" "$CWD" "$EDITOR_APP" "$SID" "$SETTLE")")
        else
            NOTIFIER_ARGS+=(-open "$SESSION_URL")
        fi
    fi

    if command -v terminal-notifier >/dev/null 2>&1; then
        terminal-notifier "${NOTIFIER_ARGS[@]}" >/dev/null 2>&1 &
        disown 2>/dev/null || true
    fi
fi
