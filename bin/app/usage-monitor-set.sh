#!/bin/bash
#
# Toggle the usage monitor (Tools → Usage monitor).
#
# Argv:
#   $1  "on" or "off". The menu item passes the *opposite* of the current
#       effective state, so a click flips it. Other values are rejected by
#       the Python side (exit non-zero); SwiftBar swallows the error and
#       the next tick re-renders the unchanged checkbox.
#
# "on" spawns the background claude session that sources live usage; "off"
# kills it (and hides the usage line + silences the alerts). Resolves the
# plugin entry point through the SwiftBar plugin symlink the same way
# multi-workspace-set.sh / keep-awake-set.sh do.

set -u

VALUE="${1:-}"
if [ -z "$VALUE" ]; then
    echo "[usage-monitor-set] missing on/off argv" >&2
    exit 1
fi

# This script lives in <repo>/bin/app/; the plugin shim is at <repo>/claude-agents.5s.py.
# Follow the symlink chain so we find the shim wherever the user installed us.
__target="${BASH_SOURCE[0]}"
while [ -L "$__target" ]; do
    __link=$(/usr/bin/readlink -- "$__target")
    case "$__link" in
        /*) __target="$__link" ;;
        *)  __target="$(cd "$(dirname "$__target")" && pwd -P)/$__link" ;;
    esac
done
__APP_DIR="$(cd "$(dirname "$__target")" && pwd -P)"
__REPO_DIR="$(cd "${__APP_DIR}/../.." && pwd -P)"
__PLUGIN="${__REPO_DIR}/claude-agents.5s.py"

if [ ! -f "$__PLUGIN" ]; then
    echo "[usage-monitor-set] plugin entry point not found: $__PLUGIN" >&2
    exit 1
fi

/usr/bin/python3 "$__PLUGIN" --usage-monitor "$VALUE"
RC=$?
open "swiftbar://refreshallplugins" 2>/dev/null || true
exit "$RC"
