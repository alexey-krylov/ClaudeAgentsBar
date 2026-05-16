#!/bin/bash
#
# Submenu action: bulk-acknowledge every currently-🟢 FRESH session.
#
# Wired up from claude-agents.5s.py under *Tools → Acknowledge all*. We
# delegate to the plugin itself in ``--ack-fresh`` mode so the selection
# rules stay in one place: the plugin reads the same sidecars, picks
# every idle session inside the fresh window without an effective click,
# and records a synthetic click for each one. The next plugin tick then
# colours those rows 🔵 ACKNOWLEDGED.
#
# Sessions that are already ACKNOWLEDGED or STALE are left untouched.

set -u

HERE="$(cd "$(dirname "$0")" && pwd -P)"
PLUGIN="${HERE}/../../claude-agents.5s.py"

/usr/bin/python3 "$PLUGIN" --ack-fresh

# Nudge SwiftBar so the colour change shows up immediately rather than
# at the next 5 s tick — same pattern as the other Tools actions.
/usr/bin/open "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

exit 0
