#!/bin/bash
#
# Write the ad-hoc quiet-hours pause sidecar (spec 0002).
#
# Argv:
#   $1  duration sentinel — "1h", "tomorrow", or an explicit ISO-8601
#       local timestamp ("2026-05-27T07:00:00"). Defaults to "1h" when
#       absent so a no-arg click still does something useful.
#
# Resolution rules:
#   "1h"       → now + 1 hour
#   "tomorrow" → next occurrence of the configured `quiet_hours` end
#                (or 09:00 local if the config knob is unset / malformed)
#   anything   → trusted verbatim as ISO-8601, written as given
#                (so an automation can pre-compute a custom deadline)
#
# Always exits 0 — a write failure or a SwiftBar refresh failure must
# not propagate up into AppleScript's error dialog.

set -u

DURATION="${1:-1h}"
SIDECAR="${HOME}/.claude/agent-state.quiet-until"

if [ -n "${CLAUDE_AGENTS_BAR_CONFIG:-}" ]; then
    _CAB_CONFIG="$CLAUDE_AGENTS_BAR_CONFIG"
else
    _XDG="${XDG_CONFIG_HOME:-$HOME/.config}"
    _CAB_CONFIG="${_XDG}/claude-agents-bar/config.json"
fi

case "$DURATION" in
    1h)
        UNTIL=$(date -v+1H "+%Y-%m-%dT%H:%M:%S")
        ;;
    tomorrow)
        # End hour from `quiet_hours` if defined, otherwise 09:00 local.
        QH=""
        if [ -f "$_CAB_CONFIG" ]; then
            QH=$(/usr/bin/jq -r '.quiet_hours // empty' "$_CAB_CONFIG" 2>/dev/null || true)
        fi
        END_H=09
        END_M=00
        # Strict 24h match — keep in lockstep with the Python validator
        # (claude_agents_bar/core.py:_QUIET_HOURS_RE) so an invalid value
        # that snuck past Python's strict load can't drive `date -v` into
        # weird states (date -v29H rejects the input and the rest of the
        # script would write garbage).
        if [[ "$QH" =~ -(2[0-3]|[01][0-9]):([0-5][0-9])$ ]]; then
            END_H="${BASH_REMATCH[1]}"
            END_M="${BASH_REMATCH[2]}"
        fi
        END_H_INT=$((10#$END_H))
        END_M_INT=$((10#$END_M))
        UNTIL=$(date -v"${END_H_INT}H" -v"${END_M_INT}M" -v0S "+%Y-%m-%dT%H:%M:%S")
        UNTIL_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%S" "$UNTIL" +%s 2>/dev/null || echo 0)
        if [ "$UNTIL_EPOCH" -le "$(date +%s)" ]; then
            UNTIL=$(date -v+1d -v"${END_H_INT}H" -v"${END_M_INT}M" -v0S "+%Y-%m-%dT%H:%M:%S")
        fi
        ;;
    *)
        UNTIL="$DURATION"
        ;;
esac

mkdir -p "$(dirname "$SIDECAR")"
printf '%s\n' "$UNTIL" > "$SIDECAR" 2>/dev/null || true

# SwiftBar will pick this up on its next tick anyway, but force a refresh
# so the menu redraws instantly on click.
open "swiftbar://refreshallplugins" 2>/dev/null || true
exit 0
