#!/bin/bash
#
# Set the ad-hoc quiet-hours bypass sidecar.
#
# Inverse of quiet-pause.sh: writes a future timestamp at
#   ${HOME}/.claude/agent-state.quiet-bypass-until
# meaning "fire notifications even though we're inside the scheduled
# quiet window, until this time". The deadline is always pinned to the
# end of the *current* quiet-hours window — once the window closes the
# bypass becomes a no-op anyway, and tying it to the window's end keeps
# the UX self-explanatory ("you said bypass for this window; it's over,
# so we're back to normal scheduling tomorrow").
#
# Reads `quiet_hours` from the user's config the same way quiet-pause.sh
# does, so a malformed config makes us no-op rather than write a garbage
# timestamp.
#
# Always exits 0 — a write failure or a SwiftBar refresh failure must
# not propagate up into AppleScript's error dialog.

set -u

SIDECAR="${HOME}/.claude/agent-state.quiet-bypass-until"

if [ -n "${CLAUDE_AGENTS_BAR_CONFIG:-}" ]; then
    _CAB_CONFIG="$CLAUDE_AGENTS_BAR_CONFIG"
else
    _XDG="${XDG_CONFIG_HOME:-$HOME/.config}"
    _CAB_CONFIG="${_XDG}/claude-agents-bar/config.json"
fi

QH=""
if [ -f "$_CAB_CONFIG" ]; then
    QH=$(/usr/bin/jq -r '.quiet_hours // empty' "$_CAB_CONFIG" 2>/dev/null || true)
fi
# Empty config falls back to the dataclass default. Keep the duo in
# lockstep with claude_agents_bar/core.py:Config.quiet_hours so the
# bypass behaves identically with or without a user config file.
[ -z "$QH" ] && QH="23:00-08:00"

END_H=08
END_M=00
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

mkdir -p "$(dirname "$SIDECAR")"
printf '%s\n' "$UNTIL" > "$SIDECAR" 2>/dev/null || true

open "swiftbar://refreshallplugins" 2>/dev/null || true
exit 0
