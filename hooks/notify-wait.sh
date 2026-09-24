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
# Called two ways. As a Claude Code hook: JSON payload on stdin, no
# arguments. As the plugin's blocked-session reminder (spec 0017), which
# re-announces a prompt nobody answered: `notify-wait.sh <session-id> <cwd>`,
# no stdin. The second form exists so a repeat travels the same channel as
# the first announcement — identical phrases, chime and banner — which is
# also why there are no separate notify_blocked_* phrase/sound knobs. The
# argument form doubles as the manual smoke test.
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

# ── Input: a hook payload on stdin, or (session id, cwd) as arguments ────────
# Claude Code invokes this as a hook: JSON on stdin, no arguments. The
# plugin's blocked-session reminder (spec 0017) re-invokes this *same* script
# with the session id and cwd as positional arguments, so a repeat is the
# identical notification — same phrase list, same chime, same banner — rather
# than a near-duplicate with knobs of its own. Arguments are the discriminator
# because Claude Code never passes any.
if [ -n "${1:-}" ]; then
    SID="$1"
    CWD="${2:-}"
    TRANSCRIPT=""
else
    INPUT=$(cat)
    SID=$(/usr/bin/jq -r '.session_id // empty' <<<"$INPUT" 2>/dev/null)
    CWD=$(/usr/bin/jq -r '.cwd // empty'        <<<"$INPUT" 2>/dev/null)
    # PermissionRequest payloads usually carry transcript_path; the glob below
    # covers an absent or stale one (same fallback as remind-session.sh), and
    # is the only path when we were called with arguments.
    TRANSCRIPT=$(/usr/bin/jq -r '.transcript_path // empty' <<<"$INPUT" 2>/dev/null)
fi

SESSION_URL=""
[ -n "$SID" ] && SESSION_URL="${SCHEME}anthropic.claude-code/open?session=${SID}"

if { [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; } && [ -n "$SID" ]; then
    case "$SID" in
        "" | *[!A-Za-z0-9_-]* ) : ;;   # ignore ids outside the safe glob alphabet
        *) for __f in "$HOME"/.claude/projects/*/"$SID".jsonl; do
               [ -f "$__f" ] && { TRANSCRIPT="$__f"; break; }
           done ;;
    esac
fi

# ── Pick a random phrase ─────────────────────────────────────────────────────
PHRASE=$(_pick_phrase "notify_wait_phrases" \
    "Need instructions" "Awaiting input" "Decision needed" "I'm blocked")

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

# Speech reads the awaiting phrase, then the marker name, then the summary
# ("I'm blocked. Чиню баг. нашёл причину") — the phrase carries no emoji. The
# banner (spec 0009) splits them: line 1 is the phrase with a ❓ type marker,
# line 3 is session title — summary, where the title is the menu row's (so a
# manual rename shows here too); the marker name only stands in when the
# plugin can't answer. No phrase leak into line 3.
SAY_TEXT="$PHRASE"
[ -n "$NAME" ]    && SAY_TEXT="$SAY_TEXT${_SAY_SEP}$NAME"
[ -n "$SUMMARY" ] && SAY_TEXT="$SAY_TEXT${_SAY_SEP}$SUMMARY"
BANNER_NAME=$(_session_menu_title "${TRANSCRIPT:-}")
BANNER_NAME="${BANNER_NAME:-$NAME}"
BANNER_MSG=""
if [ -n "$BANNER_NAME" ] && [ -n "$SUMMARY" ]; then
    BANNER_MSG="$BANNER_NAME — $SUMMARY"
elif [ -n "$BANNER_NAME" ]; then
    BANNER_MSG="$BANNER_NAME"
elif [ -n "$SUMMARY" ]; then
    BANNER_MSG="$SUMMARY"
fi

# ── Chime + speech + banner (shared emit) ────────────────────────────────────
# Title (line 1) is the phrase with a red ❓ so the banner reads as an awaiting
# prompt without a status label. Click jumps straight to the session — the user
# almost always wants to act on the prompt, not just acknowledge it.
_emit_notification "❓ $PHRASE" "$BANNER_MSG" "$SAY_TEXT" \
    "$SESSION_URL" "$SID" "$CWD"
