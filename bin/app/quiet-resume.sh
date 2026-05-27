#!/bin/bash
#
# Clear the ad-hoc quiet-hours pause sidecar (spec 0002).
#
# Side effect: signals SwiftBar to refresh so the menu's *Notifications*
# block re-renders immediately rather than on the next 5 s tick.

set -u

SIDECAR="${HOME}/.claude/agent-state.quiet-until"
rm -f "$SIDECAR" 2>/dev/null || true
open "swiftbar://refreshallplugins" 2>/dev/null || true
exit 0
