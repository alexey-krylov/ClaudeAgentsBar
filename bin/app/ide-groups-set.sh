#!/bin/bash
#
# Pick the IDE-groups display mode (Tools → Grouping).
#
# Argv:
#   $1  "submenu", "inline", or "off". Written to
#       ~/.claude/agent-state.ide-groups.mode, which overrides the
#       ide_groups_mode config knob. Unknown values are refused by the
#       Python side (exit non-zero); SwiftBar swallows the error and the
#       next tick re-renders the unchanged selection.
#
# Resolves the plugin entry point through the SwiftBar plugin symlink the
# same way multi-workspace-set.sh / keep-awake-set.sh do, so it works
# whether the repo lives in a Homebrew Cellar or a manually-cloned tree.

set -u

VALUE="${1:-}"
if [ -z "$VALUE" ]; then
    echo "[ide-groups-set] missing mode argv" >&2
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
    echo "[ide-groups-set] plugin entry point not found: $__PLUGIN" >&2
    exit 1
fi

/usr/bin/python3 "$__PLUGIN" --ide-groups "$VALUE"
RC=$?
open "swiftbar://refreshallplugins" 2>/dev/null || true
exit "$RC"
