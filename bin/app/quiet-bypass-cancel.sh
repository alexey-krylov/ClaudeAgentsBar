#!/bin/bash
#
# Clear the ad-hoc quiet-hours bypass sidecar.
#
# Mirrors quiet-resume.sh's role for the bypass channel: removes the
# bypass sidecar so the scheduled quiet window goes back to suppressing
# notifications. Signals SwiftBar to refresh so the menu's *Notifications*
# block re-renders immediately rather than on the next 5 s tick.

set -u

SIDECAR="${HOME}/.claude/agent-state.quiet-bypass-until"
rm -f "$SIDECAR" 2>/dev/null || true
open "swiftbar://refreshallplugins" 2>/dev/null || true
exit 0
