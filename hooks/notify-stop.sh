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
#   quiet_hours            string  null     — "HH:MM-HH:MM" window during
#                                             which notifications are silenced
#                                             per `quiet_hours_silences`
#   quiet_hours_silences   array   all      — channels suppressed while quiet:
#                                             subset of ["sound","voice","banner"]
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

# Custom audio (spec 0001).
SOUND_RAW=$(_cfg_string_or_null "notify_sound_stop" "Hero")
SOUND_PATH=$(_resolve_sound "$SOUND_RAW")
VOICE=$(_cfg_string             "notify_voice"      "")

# Quiet-hours gate (spec 0002). Sets QUIET_NOW + SUPPRESS_SOUND/VOICE/BANNER.
_compute_quiet_state

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
# Bash 3.2 (system /bin/bash on macOS) lacks mapfile, so we use a while loop.
_emit_phrases() {
    [ -f "$_CAB_CONFIG" ] || return
    /usr/bin/jq -r '.notify_phrases // empty | .[]?' "$_CAB_CONFIG" 2>/dev/null
}
PHRASES=()
while IFS= read -r _phrase; do
    [ -n "$_phrase" ] && PHRASES+=("$_phrase")
done < <(_emit_phrases)

if [ "${#PHRASES[@]}" -eq 0 ]; then
    PHRASES=("Check it" "Done" "Ready for review" "Your turn")
fi

PHRASE="${PHRASES[$RANDOM % ${#PHRASES[@]}]}"

# ── Sound + speech (fire-and-forget, never block the hook) ───────────────────
if [ "$SUPPRESS_SOUND" = "false" ] && [ -n "$SOUND_PATH" ]; then
    afplay "$SOUND_PATH" >/dev/null 2>&1 &
fi
if [ "$SUPPRESS_VOICE" = "false" ] && [ "$VOICE" != "off" ]; then
    if [ -n "$VOICE" ]; then
        (sleep 1 && say -v "$VOICE" "$PHRASE") >/dev/null 2>&1 &
    else
        (sleep 1 && say "$PHRASE") >/dev/null 2>&1 &
    fi
fi
disown 2>/dev/null || true

# ── Banner notification ───────────────────────────────────────────────────────
# terminal-notifier blocks macOS impersonation of Apple-signed bundles and
# ignores -appIcon on recent macOS versions. We use -contentImage for the
# side icon. The banner is clickable — clicking jumps to the right session
# in the editor.
#
# When we know the session's cwd and a window-raising .app for the
# configured scheme, the click runs raise-and-open.sh (via -execute) so
# it lands in the window matching the workspace rather than whatever is
# frontmost. Otherwise we fall back to a plain -open of the deeplink.
if [ "$SUPPRESS_BANNER" = "false" ]; then
    ICON="${HOME}/.claude/hooks/assets/claude-icon.png"
    NOTIFIER_ARGS=(-title "$TITLE" -subtitle "Claude Code" -message "$PHRASE")
    [ -f "$ICON" ]         && NOTIFIER_ARGS+=(-contentImage "$ICON")
    if [ -n "$SESSION_URL" ]; then
        EDITOR_APP=$(_editor_app_for_scheme "$SCHEME")
        if [ -n "$CWD" ] && [ -n "$EDITOR_APP" ] && [ -d "$CWD" ] && [ -d "$EDITOR_APP" ]; then
            NOTIFIER_ARGS+=(-execute "$(_raise_open_cmd "$SESSION_URL" "$CWD" "$EDITOR_APP" "$SID")")
        else
            NOTIFIER_ARGS+=(-open "$SESSION_URL")
        fi
    fi

    if command -v terminal-notifier >/dev/null 2>&1; then
        terminal-notifier "${NOTIFIER_ARGS[@]}" >/dev/null 2>&1 &
        disown 2>/dev/null || true
    fi
fi
