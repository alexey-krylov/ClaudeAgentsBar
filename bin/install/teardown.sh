#!/bin/bash
#
# ClaudeAgentsBar teardown — reverses what setup.sh did:
#
#   1. Stop any caffeinate-i process we own (spec 0003).
#   2. Remove the SwiftBar plugin symlink.
#   3. Remove the Claude Code hook symlinks.
#   4. Strip our hook entries from ~/.claude/settings.json (with a
#      timestamped backup of the file taken first).
#   5. Refresh SwiftBar so the menu-bar icon disappears immediately.
#
# We leave ~/.claude/agent-state.tsv (and its peers) behind on purpose —
# you may want to inspect them; delete manually if not.

set -euo pipefail

# This script lives in <repo>/bin/install/. The repo root is two levels up.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_DIR="$(cd "$HERE/../.." && pwd -P)"

SWIFTBAR_PLUGINS_DIR="${SWIFTBAR_PLUGINS_DIR:-$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null \
    || echo "${HOME}/SwiftBar")}"
PLUGIN_DST="${SWIFTBAR_PLUGINS_DIR}/claude-agents.5s.py"
HOOK_DST="${HOME}/.claude/hooks/agent-state.sh"
NOTIFY_HOOK_DST="${HOME}/.claude/hooks/notify-stop.sh"
NOTIFY_WAIT_HOOK_DST="${HOME}/.claude/hooks/notify-wait.sh"
SETTINGS="${HOME}/.claude/settings.json"
SETTINGS_BACKUP="${SETTINGS}.bak.$(date +%Y%m%d-%H%M%S)"

say()  { printf '  %s\n' "$*"; }
step() { printf '\n→ %s\n' "$*"; }


step "1. Stop any caffeinate we own"
# spec 0003 — if keep_awake=auto/always is enabled, the plugin has a
# detached caffeinate process holding awake. Kill it before we strip
# the symlink, otherwise the menu's only off-switch goes away and the
# user has to hunt the PID by hand.
if [ -f "${REPO_DIR}/claude-agents.5s.py" ]; then
    /usr/bin/python3 "${REPO_DIR}/claude-agents.5s.py" --keep-awake-shutdown \
        2>/dev/null || true
fi
say "ok"


step "2. Remove plugin symlink"
if [ -L "$PLUGIN_DST" ]; then
    rm "$PLUGIN_DST" && say "removed $PLUGIN_DST"
else
    say "not present"
fi


step "3. Remove hook symlinks"
if [ -L "$HOOK_DST" ]; then
    rm "$HOOK_DST" && say "removed $HOOK_DST"
else
    say "not present: $HOOK_DST"
fi
if [ -L "$NOTIFY_HOOK_DST" ]; then
    rm "$NOTIFY_HOOK_DST" && say "removed $NOTIFY_HOOK_DST"
else
    say "not present: $NOTIFY_HOOK_DST"
fi
if [ -L "$NOTIFY_WAIT_HOOK_DST" ]; then
    rm "$NOTIFY_WAIT_HOOK_DST" && say "removed $NOTIFY_WAIT_HOOK_DST"
else
    say "not present: $NOTIFY_WAIT_HOOK_DST"
fi


step "4. Strip our hook entries from settings.json"
if [ -f "$SETTINGS" ]; then
    cp "$SETTINGS" "$SETTINGS_BACKUP"
    say "backup: $SETTINGS_BACKUP"
    # Walk the "hooks" map and drop any matcher whose command references
    # our agent-state.sh, then collapse the resulting empty arrays and
    # empty event entries so we don't leave dangling structure behind.
    /usr/bin/jq '
        .hooks = (.hooks // {})
        | .hooks |= with_entries(
            .value |= map(
                .hooks |= map(
                    select((.command // "") | test("agent-state\\.sh|notify-stop\\.sh|notify-wait\\.sh") | not)
                )
            )
            | .value |= map(select((.hooks // []) | length > 0))
        )
        | .hooks |= with_entries(select((.value | length) > 0))
    ' "$SETTINGS" > "${SETTINGS}.tmp" && mv "${SETTINGS}.tmp" "$SETTINGS"
    say "cleaned"
fi


step "5. Refresh SwiftBar"
if pgrep -q SwiftBar; then
    open "swiftbar://refreshallplugins"
fi


echo
echo "Uninstalled. Leftover artefacts you may want to delete manually:"
echo "  ~/.claude/agent-state.tsv"
echo "  ~/.claude/agent-state.subagents.tsv"
echo "  ~/.claude/agent-state.clicks"
echo "  ~/.claude/agent-state.dismiss"
echo "  ~/.claude/agent-state.forget"
echo "  ~/.claude/agent-state.quiet-until"
echo "  ~/.claude/agent-state.quiet-bypass-until"
echo "  ~/.claude/agent-state.keep-awake.mode"
echo "  ~/.claude/agent-state.caffeinate"
