#!/bin/bash
#
# Row action: re-speak a session's last spoken summary via say(1).
#
# Wired up from claude_agents_bar/render.py — each session row's "Remind"
# submenu item runs:
#
#     remind-session.sh <session-id> <no-marker-hint>
#
# The transcript is parsed here, on click — never on the 5 s render tick. We
# locate the session's JSONL, pull the summary (the text after
# notify_summary_marker on the assistant's last reply line, via the shared
# _extract_summary helper — the same line the Stop notification speaks), and
# read it aloud. If the marker is configured but the latest reply carried no
# such line, we speak <no-marker-hint> instead (a localised "configure Claude"
# phrase the plugin passes in), so the click is never silently inert.
#
# We reuse the notification voice (notify_voice) so a reminder sounds like the
# original Stop notification. Being an explicit click, it speaks even under
# "banner only" mode or notify_voice:"off" (which mute only the *automatic*
# speech); "off" falls through to the system default voice.

set -u

SID="${1:-}"
HINT="${2:-}"
if [ -z "$SID" ]; then
    exit 1
fi

# Defence-in-depth: refuse anything outside the safe session-id alphabet
# before using it to build a glob (mirrors forget-session.sh).
case "$SID" in
    "" | *[!A-Za-z0-9_-]* ) exit 1 ;;
esac
if [ "${#SID}" -gt 64 ]; then
    exit 1
fi

# ── Source the shared helpers ────────────────────────────────────────────────
# Resolve through any symlink so the repo's hooks/_notify-common.sh is what
# gets sourced (same resolver the hooks use). bin/app/ and hooks/ are siblings
# under the project root, preserved in both the git clone and the Homebrew
# bottle.
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
if [ ! -f "$__COMMON" ]; then
    exit 0
fi
# shellcheck source=/dev/null
. "$__COMMON"

# ── Locate the session transcript ────────────────────────────────────────────
# Claude Code stores transcripts at ~/.claude/projects/<slug>/<sid>.jsonl; the
# slug isn't derivable from the id, so glob for the matching file.
TRANSCRIPT=""
for __f in "$HOME"/.claude/projects/*/"$SID".jsonl; do
    [ -f "$__f" ] && { TRANSCRIPT="$__f"; break; }
done

# ── Extract + speak ──────────────────────────────────────────────────────────
MARKER=$(_cfg_string_or_null "notify_summary_marker" "-- ")
SUMMARY=$(_extract_summary "$TRANSCRIPT" "$MARKER")

# Summary present → speak it. Marker on but no summary in the last reply →
# speak the localised hint. Nothing to say at all → exit quietly.
TEXT="$SUMMARY"
[ -z "$TEXT" ] && TEXT="$HINT"
[ -z "$TEXT" ] && exit 0

VOICE=$(_cfg_string "notify_voice" "")

# Fire-and-forget, never block the click. No leading chime, so no settle pause.
if [ -n "$VOICE" ] && [ "$VOICE" != "off" ]; then
    (say -v "$VOICE" "$TEXT") >/dev/null 2>&1 &
else
    (say "$TEXT") >/dev/null 2>&1 &
fi
disown 2>/dev/null || true

exit 0
