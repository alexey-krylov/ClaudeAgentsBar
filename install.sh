#!/bin/bash
#
# ClaudeAgentsBar installer.
#
# Three steps, idempotent:
#
#   1. Symlink the SwiftBar plugin into the SwiftBar plugins directory.
#   2. Symlink the Claude Code hook into ~/.claude/hooks/.
#   3. Merge our hook registrations into ~/.claude/settings.json (with
#      a timestamped backup taken first).
#
# Re-running is safe — existing symlinks are replaced and the settings.json
# merge is additive (existing hooks of yours are preserved).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_SRC="${REPO_DIR}/claude-agents.5s.py"
HOOK_SRC="${REPO_DIR}/hooks/agent-state.sh"
HOOK_PATCH="${REPO_DIR}/settings-hooks.json"

# Resolve the SwiftBar plugins folder. Override with SWIFTBAR_PLUGINS_DIR=...
# if you've moved it; otherwise we ask SwiftBar itself and fall back to a
# sensible default.
SWIFTBAR_PLUGINS_DIR="${SWIFTBAR_PLUGINS_DIR:-$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null \
    || echo "${HOME}/SwiftBar")}"

# SwiftBar recursively scans its plugins folder with MakePluginExecutable=1,
# which means it'll happily run *this* installer (or the uninstaller) as a
# plugin if the project lives inside that tree. Refuse early — once SwiftBar
# starts running uninstall.sh in a tight loop you have a fun time.
if [ "$REPO_DIR" = "${SWIFTBAR_PLUGINS_DIR%/}" ] || [[ "$REPO_DIR" == "${SWIFTBAR_PLUGINS_DIR%/}/"* ]]; then
    cat >&2 <<EOF
ERROR: this project lives inside the SwiftBar plugins folder
       ($SWIFTBAR_PLUGINS_DIR)
       SwiftBar will scan it and run scripts like uninstall.sh as plugins.
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

# Deep-merge our patch into the existing settings: for each event in the
# patch's "hooks" map, append our matcher objects to whatever the user
# already had. Never overwrites existing hooks.
/usr/bin/jq --argjson patch "$PATCH_EXPANDED" '
    .hooks = (.hooks // {})
    | reduce ($patch.hooks | to_entries[]) as $kv (
        .;
        .hooks[$kv.key] = ((.hooks[$kv.key] // []) + $kv.value)
    )
' "$SETTINGS" > "${SETTINGS}.tmp" && mv "${SETTINGS}.tmp" "$SETTINGS"
say "merged"


step "6. Sanity check hook script"
# Round-trip a fake event through the hook and verify the row lands in the
# TSV. Clean up the test row afterwards so we don't pollute the index.
echo '{"session_id":"00000000-test","cwd":"/tmp","hook_event_name":"SessionStart"}' \
    | "$HOOK_DST" working >/dev/null
if /usr/bin/grep -q '^00000000-test	working' "${HOME}/.claude/agent-state.tsv"; then
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
echo "To uninstall: bash $REPO_DIR/uninstall.sh"
