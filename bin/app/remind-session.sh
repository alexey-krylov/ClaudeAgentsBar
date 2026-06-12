#!/bin/bash
#
# Row action: re-speak a session's last spoken summary via say(1).
#
# Wired up from claude_agents_bar/render.py — each session row's "Remind"
# submenu item runs:
#
#     remind-session.sh <summary-text>
#
# The plugin already extracted the summary (the text after
# ``notify_summary_marker`` on the assistant's last reply line — see
# sidecars.last_assistant_summary) and only renders this item enabled when
# there's something to say, so we just speak the text it hands us. We reuse
# the notify hooks' voice config + say(1) invocation so a reminder sounds
# exactly like the original Stop notification.
#
# Unlike the Stop hook this is an explicit user click, so it ignores the
# "banner only" audio mode and a ``notify_voice: "off"`` setting (which only
# mute the *automatic* speech): clicking Remind always speaks. ``off`` falls
# through to the system default voice.

set -u

TEXT="${1:-}"
if [ -z "$TEXT" ]; then
    # Nothing to say (the item should have been disabled) — exit quietly.
    exit 0
fi

# ── Source the shared helpers ────────────────────────────────────────────────
# Resolve through any symlink so the repo's hooks/_notify-common.sh is what
# gets sourced (same resolver the hooks use). bin/app/ and hooks/ are
# siblings under the project root, preserved in both the git clone and the
# Homebrew bottle.
__target="${BASH_SOURCE[0]}"
while [ -L "$__target" ]; do
    __link=$(/usr/bin/readlink -- "$__target")
    case "$__link" in
        /*) __target="$__link" ;;
        *)  __target="$(cd "$(dirname "$__target")" && pwd -P)/$__link" ;;
    esac
done
__APP_DIR="$(cd "$(dirname "$__target")" && pwd -P)"
__COMMON="${__APP_DIR}/../../hooks/_notify-common.sh"

VOICE=""
if [ -f "$__COMMON" ]; then
    # shellcheck source=/dev/null
    . "$__COMMON"
    VOICE=$(_cfg_string "notify_voice" "")
fi

# ── Speak (fire-and-forget, never block the click) ───────────────────────────
# No leading chime here, so no settle pause: speak immediately.
if [ -n "$VOICE" ] && [ "$VOICE" != "off" ]; then
    (say -v "$VOICE" "$TEXT") >/dev/null 2>&1 &
else
    (say "$TEXT") >/dev/null 2>&1 &
fi
disown 2>/dev/null || true

exit 0
