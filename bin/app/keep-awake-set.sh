#!/bin/bash
#
# Set the keep-awake mode (spec 0003).
#
# Argv:
#   $1  one of "off", "auto", "always". Other values are rejected by the
#       Python side and exit non-zero — but SwiftBar swallows the error,
#       and the plugin re-renders on its next tick showing the unchanged
#       mode, which is the right user-facing fallback.
#
# Resolves the plugin entry point through the SwiftBar plugin symlink the
# same way the hook scripts do, so the call works regardless of whether
# the repo lives in a Homebrew Cellar or a manually-cloned tree.

set -u

MODE="${1:-}"
if [ -z "$MODE" ]; then
    echo "[keep-awake-set] missing mode argv" >&2
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
    echo "[keep-awake-set] plugin entry point not found: $__PLUGIN" >&2
    exit 1
fi

/usr/bin/python3 "$__PLUGIN" --keep-awake "$MODE"
RC=$?
open "swiftbar://refreshallplugins" 2>/dev/null || true
exit "$RC"
