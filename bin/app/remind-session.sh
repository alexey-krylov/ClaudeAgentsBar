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
# locate the session's JSONL and pull its summaries (lines after
# notify_summary_marker, via the shared _summary_endpoints helper) and read
# them aloud:
#
#   * Latest summary — always spoken (the session's current state).
#   * Opening summary — prepended only when remind_recap_after_min is set and
#     at least that many minutes have passed since the last output, so a click
#     on a cold session reminds you what it was about before where it is now.
#     Unset → latest only; same-as-latest (single summary) → spoken once.
#   * No summary at all (marker on, none emitted) — we speak <no-marker-hint>
#     instead (a localised "configure Claude" phrase the plugin passes in), so
#     the click is never silently inert.
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

# ── Extract the session's first + last summary ───────────────────────────────
MARKER=$(_cfg_string_or_null "notify_summary_marker" "-- ")
FIRST=""
LAST=""
{ IFS= read -r FIRST; IFS= read -r LAST; } < <(_summary_endpoints "$TRANSCRIPT" "$MARKER")

# ── Decide whether to recap from the start ───────────────────────────────────
# remind_recap_after_min (optional): when set and at least that many minutes
# have passed since the last output (transcript mtime), prepend the session's
# opening summary so a click on a cold session refreshes context. Unset /
# invalid → speak only the latest summary (you're still in the flow); 0 →
# always recap.
RECAP_AFTER=$(_cfg_int "remind_recap_after_min" "")
WANT_FIRST=false
if [ -n "$RECAP_AFTER" ] && [ "$RECAP_AFTER" -ge 0 ] 2>/dev/null && [ -n "$TRANSCRIPT" ]; then
    TS=$(/usr/bin/stat -f %m "$TRANSCRIPT" 2>/dev/null || echo "")
    if [ -n "$TS" ]; then
        AGE_MIN=$(( ( $(date +%s) - TS ) / 60 ))
        [ "$AGE_MIN" -ge "$RECAP_AFTER" ] && WANT_FIRST=true
    fi
fi

# ── Build the phrase list ────────────────────────────────────────────────────
# Default: latest summary only. Recap: opening summary first, then latest
# (the opening is skipped when the session has a single summary). No summary at
# all → the localised hint. Nothing to say → quiet exit.
PHRASES=()
if [ "$WANT_FIRST" = "true" ] && [ -n "$FIRST" ] && [ "$FIRST" != "$LAST" ]; then
    PHRASES+=("$FIRST")
fi
[ -n "$LAST" ] && PHRASES+=("$LAST")
if [ "${#PHRASES[@]}" -eq 0 ]; then
    if [ -n "$HINT" ]; then
        PHRASES+=("$HINT")
    else
        exit 0
    fi
fi

VOICE=$(_cfg_string "notify_voice" "")
_say() {  # speak one phrase with the configured voice (system default if off/unset)
    if [ -n "$VOICE" ] && [ "$VOICE" != "off" ]; then
        say -v "$VOICE" "$1"
    else
        say "$1"
    fi
}

# Fire-and-forget, never block the click. A short pause separates consecutive
# phrases ("was → now"); no leading chime, so nothing before the first.
(
    __i=0
    for __p in "${PHRASES[@]}"; do
        [ "$__i" -gt 0 ] && sleep 0.4
        _say "$__p"
        __i=$((__i + 1))
    done
) >/dev/null 2>&1 &
disown 2>/dev/null || true

exit 0
