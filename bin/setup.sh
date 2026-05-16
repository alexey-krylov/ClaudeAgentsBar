#!/bin/bash
#
# ClaudeAgentsBar setup — wires the bundled plugin/hook into the user's
# SwiftBar plugins dir and ~/.claude/ tree. Invoked by `claude-agents-bar
# setup` (and indirectly by the legacy `bash install.sh` wrapper).
#
# Three steps, idempotent:
#
#   1. Symlink the SwiftBar plugin into the SwiftBar plugins directory.
#   2. Symlink the Claude Code hook into ~/.claude/hooks/.
#   3. Merge our hook registrations into ~/.claude/settings.json (with
#      a timestamped backup taken first).
#
# Re-running is safe — existing symlinks are replaced, and the
# settings.json merge first purges any prior ``agent-state.sh`` matchers
# (so when the bundled command line changes, e.g. ``working`` →
# ``session-start``, a re-run *updates* the registration rather than
# appending a duplicate alongside the stale one). Hooks belonging to
# the user — anything whose command does not contain ``agent-state.sh``
# — are preserved untouched.

set -euo pipefail

# This script lives in <repo>/bin/. The repo root is one level up.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_DIR="$(cd "$HERE/.." && pwd -P)"
PLUGIN_SRC="${REPO_DIR}/claude-agents.5s.py"
HOOK_SRC="${REPO_DIR}/hooks/agent-state.sh"
HOOK_PATCH="${REPO_DIR}/settings-hooks.json"

# Resolve the SwiftBar plugins folder. Override with SWIFTBAR_PLUGINS_DIR=...
# if you've moved it; otherwise we ask SwiftBar itself and fall back to a
# sensible default.
SWIFTBAR_PLUGINS_DIR="${SWIFTBAR_PLUGINS_DIR:-$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null \
    || echo "${HOME}/SwiftBar")}"

# SwiftBar recursively scans its plugins folder with MakePluginExecutable=1,
# which means it'll happily run *this* script (or its wrappers) as a plugin
# if the project lives inside that tree. Refuse early — once SwiftBar starts
# running teardown.sh in a tight loop you have a fun time.
if [ "$REPO_DIR" = "${SWIFTBAR_PLUGINS_DIR%/}" ] || [[ "$REPO_DIR" == "${SWIFTBAR_PLUGINS_DIR%/}/"* ]]; then
    cat >&2 <<EOF
ERROR: this project lives inside the SwiftBar plugins folder
       ($SWIFTBAR_PLUGINS_DIR)
       SwiftBar will scan it and run setup/teardown scripts as plugins.
       Move the project somewhere else (e.g. ~/Projects/ClaudeAgentsBar)
       and re-run.
EOF
    exit 1
fi

PLUGIN_DST="${SWIFTBAR_PLUGINS_DIR}/claude-agents.5s.py"
HOOKS_DIR="${HOME}/.claude/hooks"
HOOK_DST="${HOOKS_DIR}/agent-state.sh"
SETTINGS="${HOME}/.claude/settings.json"
SETTINGS_BACKUP="${SETTINGS}.bak.$(date +%Y%m%d-%H%M%S)"

say()  { printf '  %s\n' "$*"; }
step() { printf '\n→ %s\n' "$*"; }


step "1. Verify required tools"
command -v jq      >/dev/null || { echo "jq missing — brew install jq"; exit 1; }
command -v python3 >/dev/null || { echo "python3 missing"; exit 1; }
if [ ! -d /Applications/SwiftBar.app ]; then
    say "WARN: SwiftBar.app not found in /Applications — install it via"
    say "      'brew install --cask swiftbar' first."
fi


step "2. Make scripts executable"
chmod +x "$PLUGIN_SRC" "$HOOK_SRC"
say "ok"


step "3. Symlink plugin into SwiftBar plugins dir: $SWIFTBAR_PLUGINS_DIR"
mkdir -p "$SWIFTBAR_PLUGINS_DIR"
if [ -L "$PLUGIN_DST" ] || [ -e "$PLUGIN_DST" ]; then
    say "existing entry at $PLUGIN_DST — replacing"
    rm -f "$PLUGIN_DST"
fi
ln -s "$PLUGIN_SRC" "$PLUGIN_DST"
say "linked: $PLUGIN_DST -> $PLUGIN_SRC"


step "4. Symlink hook into $HOOKS_DIR"
mkdir -p "$HOOKS_DIR"
if [ -L "$HOOK_DST" ] || [ -e "$HOOK_DST" ]; then
    say "existing entry at $HOOK_DST — replacing"
    rm -f "$HOOK_DST"
fi
ln -s "$HOOK_SRC" "$HOOK_DST"
say "linked: $HOOK_DST -> $HOOK_SRC"


step "5. Merge hook registrations into $SETTINGS"
if [ ! -f "$SETTINGS" ]; then
    say "no existing settings.json — creating one"
    echo '{}' > "$SETTINGS"
fi

# Expand ${HOME} inside the patch before feeding it to jq. macOS doesn't ship
# envsubst, so we just use Python (which is always present on a system where
# the plugin can run anyway).
PATCH_EXPANDED="$(/usr/bin/python3 -c \
    "import os,sys; print(sys.stdin.read().replace('\${HOME}', os.environ['HOME']))" \
    < "$HOOK_PATCH")"

cp "$SETTINGS" "$SETTINGS_BACKUP"
say "backup written: $SETTINGS_BACKUP"

# Two-phase merge:
#   1. Purge any *existing* matchers under .hooks that reference
#      ``agent-state.sh`` — these were written by a previous setup run
#      and need to be replaced rather than duplicated (when our bundled
#      command line changes, e.g. argument rename or path change, the
#      old matcher would otherwise keep firing alongside the new one).
#      Inside each surviving matcher we also drop individual hook
#      entries whose command mentions ``agent-state.sh``, in case the
#      user has merged our hook into a matcher of their own. Anything
#      else is preserved.
#   2. Additively append our patch's matchers to whatever survived.
#      For events the user had no hooks on, the array is created fresh.
/usr/bin/jq --argjson patch "$PATCH_EXPANDED" '
    def is_ours: (.command // "") | contains("agent-state.sh");
    .hooks = (.hooks // {})
    | .hooks |= with_entries(
        .value |= (
            map(.hooks |= map(select(is_ours | not)))
            | map(select(((.hooks // []) | length) > 0))
        )
    )
    | reduce ($patch.hooks | to_entries[]) as $kv (
        .;
        .hooks[$kv.key] = ((.hooks[$kv.key] // []) + $kv.value)
    )
' "$SETTINGS" > "${SETTINGS}.tmp" && mv "${SETTINGS}.tmp" "$SETTINGS"
say "merged"


step "6. Sanity check hook script"
# Round-trip a fake event through the hook and verify the row lands in the
# TSV. Clean up the test row afterwards so we don't pollute the index. We
# use ``session-start`` with ``source=startup`` so the smoke test exercises
# the same branch a real cold-started Claude Code session would hit (the
# row should land as ``idle`` — see hooks/agent-state.sh for why).
echo '{"session_id":"00000000-test","cwd":"/tmp","hook_event_name":"SessionStart","source":"startup"}' \
    | "$HOOK_DST" session-start >/dev/null
if /usr/bin/grep -q '^00000000-test	idle' "${HOME}/.claude/agent-state.tsv"; then
    say "hook works — agent-state.tsv updated"
    /usr/bin/grep -v '^00000000-test	' "${HOME}/.claude/agent-state.tsv" \
        > "${HOME}/.claude/agent-state.tsv.tmp" || true
    mv "${HOME}/.claude/agent-state.tsv.tmp" "${HOME}/.claude/agent-state.tsv"
else
    say "WARN: hook ran but didn't update agent-state.tsv — check"
    say "      ${HOME}/.claude/agent-state.tsv"
fi


step "7. Refresh SwiftBar"
if pgrep -q SwiftBar; then
    open "swiftbar://refreshallplugins"
    say "refresh signal sent"
else
    say "SwiftBar not running — start it manually"
fi


echo
echo "Done. Look for the Claude Agents Bar icon in your menu bar."
echo "(Defaults to Claude.app's tray glyph; falls back to 🤖 when Claude.app"
echo " isn't installed. Configurable via menubar_icon — see README.)"
echo "Active sessions started after this point will show live state."
echo "To undo: claude-agents-bar teardown"
