#!/bin/bash
#
# Toggle multi-workspace window focus (Tools → Multi-workspace mode).
#
# Argv:
#   $1  "on" or "off". The menu item passes the *opposite* of the current
#       effective state, so a click flips it. Other values are rejected by
#       the Python side (exit non-zero); SwiftBar swallows the error and
#       the next tick re-renders the unchanged checkbox, which is the right
#       user-facing fallback.
#
# Resolves the plugin entry point through the SwiftBar plugin symlink the
# same way keep-awake-set.sh / the hook scripts do, so it works whether the
# repo lives in a Homebrew Cellar or a manually-cloned tree.

set -u

VALUE="${1:-}"
if [ -z "$VALUE" ]; then
    echo "[multi-workspace-set] missing on/off argv" >&2
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
    echo "[multi-workspace-set] plugin entry point not found: $__PLUGIN" >&2
    exit 1
fi

/usr/bin/python3 "$__PLUGIN" --multi-workspace "$VALUE"
RC=$?
open "swiftbar://refreshallplugins" 2>/dev/null || true
exit "$RC"
