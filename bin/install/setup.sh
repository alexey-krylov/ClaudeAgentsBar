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

# This script lives in <repo>/bin/install/. The repo root is two levels up.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_DIR="$(cd "$HERE/../.." && pwd -P)"

# Homebrew: re-anchor at the stable `opt` prefix, not the versioned Cellar
# keg (ADR-0017). `claude-agents-bar setup` is reached through the symlink
# chain $HOMEBREW_PREFIX/bin → Cellar/<version>/…; the dispatcher resolves it
# with `cd -P`, so REPO_DIR here is the *versioned* keg path. Symlinking the
# plugin/hooks at it would dangle after the next `brew upgrade`, which deletes
# the old keg — the "stops working after an upgrade" bug. The `opt` prefix
# ($HOMEBREW_PREFIX/opt/claude-agents-bar) is a symlink Homebrew repoints to
# the current version on every upgrade, so anchoring there lets the install
# survive upgrades with no re-run of setup. Outside Homebrew (git clone) the
# case doesn't match and REPO_DIR is left as-is.
case "$REPO_DIR" in
    */Cellar/claude-agents-bar/*/libexec)
        _opt="$(brew --prefix claude-agents-bar 2>/dev/null || true)"
        if [ -z "$_opt" ]; then
            # brew not on PATH — derive the opt path from the Cellar layout.
            _opt="${REPO_DIR%%/Cellar/claude-agents-bar/*}/opt/claude-agents-bar"
        fi
        if [ -f "${_opt}/libexec/claude-agents.5s.py" ]; then
            REPO_DIR="${_opt}/libexec"
        fi
        ;;
esac

PLUGIN_SRC="${REPO_DIR}/claude-agents.5s.py"
HOOK_SRC="${REPO_DIR}/hooks/agent-state.sh"
NOTIFY_HOOK_SRC="${REPO_DIR}/hooks/notify-stop.sh"
NOTIFY_WAIT_HOOK_SRC="${REPO_DIR}/hooks/notify-wait.sh"
# Usage sensor (spec 0011): a statusLine wrapper, so unlike the notify hooks
# it is wired into .statusLine (not .hooks). notify-usage.sh is NOT symlinked —
# the plugin invokes it from its own hooks dir via /bin/bash, like notify-idle.sh.
SENSOR_SRC="${REPO_DIR}/hooks/usage-sensor.sh"
HOOK_PATCH="${REPO_DIR}/hooks/settings-hooks.json"

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
NOTIFY_HOOK_DST="${HOOKS_DIR}/notify-stop.sh"
NOTIFY_WAIT_HOOK_DST="${HOOKS_DIR}/notify-wait.sh"
SENSOR_DST="${HOOKS_DIR}/usage-sensor.sh"
STATUSLINE_ORIG_FILE="${HOME}/.claude/agent-state.statusline.orig"
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
chmod +x "$PLUGIN_SRC" "$HOOK_SRC" "$NOTIFY_HOOK_SRC" "$NOTIFY_WAIT_HOOK_SRC"
# Every menu-row click target lives under bin/app/ — chmod the whole
# directory so a new script (e.g. quiet-pause.sh, keep-awake-set.sh) is
# picked up without having to remember to extend the explicit list.
chmod +x "$REPO_DIR"/bin/app/*.sh 2>/dev/null || true
say "ok"


step "3. Symlink plugin into SwiftBar plugins dir: $SWIFTBAR_PLUGINS_DIR"
mkdir -p "$SWIFTBAR_PLUGINS_DIR"
if [ -L "$PLUGIN_DST" ] || [ -e "$PLUGIN_DST" ]; then
    say "existing entry at $PLUGIN_DST — replacing"
    rm -f "$PLUGIN_DST"
fi
ln -s "$PLUGIN_SRC" "$PLUGIN_DST"
say "linked: $PLUGIN_DST -> $PLUGIN_SRC"


step "4. Symlink hooks into $HOOKS_DIR"
mkdir -p "$HOOKS_DIR"
if [ -L "$HOOK_DST" ] || [ -e "$HOOK_DST" ]; then
    say "existing entry at $HOOK_DST — replacing"
    rm -f "$HOOK_DST"
fi
ln -s "$HOOK_SRC" "$HOOK_DST"
say "linked: $HOOK_DST -> $HOOK_SRC"
if [ -L "$NOTIFY_HOOK_DST" ] || [ -e "$NOTIFY_HOOK_DST" ]; then
    say "existing entry at $NOTIFY_HOOK_DST — replacing"
    rm -f "$NOTIFY_HOOK_DST"
fi
ln -s "$NOTIFY_HOOK_SRC" "$NOTIFY_HOOK_DST"
say "linked: $NOTIFY_HOOK_DST -> $NOTIFY_HOOK_SRC"
if [ -L "$NOTIFY_WAIT_HOOK_DST" ] || [ -e "$NOTIFY_WAIT_HOOK_DST" ]; then
    say "existing entry at $NOTIFY_WAIT_HOOK_DST — replacing"
    rm -f "$NOTIFY_WAIT_HOOK_DST"
fi
ln -s "$NOTIFY_WAIT_HOOK_SRC" "$NOTIFY_WAIT_HOOK_DST"
say "linked: $NOTIFY_WAIT_HOOK_DST -> $NOTIFY_WAIT_HOOK_SRC"
if [ -L "$SENSOR_DST" ] || [ -e "$SENSOR_DST" ]; then
    say "existing entry at $SENSOR_DST — replacing"
    rm -f "$SENSOR_DST"
fi
ln -s "$SENSOR_SRC" "$SENSOR_DST"
say "linked: $SENSOR_DST -> $SENSOR_SRC"


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
    def is_ours: (.command // "") | (contains("agent-state.sh") or contains("notify-stop.sh") or contains("notify-wait.sh"));
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


step "5b. Wire usage sensor into statusLine (chain)"
# rate_limits are only on the statusLine stdin (spec 0011 / ADR-0018), so the
# sensor has to BE the statusLine command. We wrap whatever the user already
# had: the sensor writes the usage snapshot, then chains to the original
# command (passed as its argument) so the user's status line still renders.
# The original is saved to a sidecar for a clean teardown. Idempotent — a
# command already wrapping usage-sensor.sh is left untouched.
CUR_STATUSLINE=$(/usr/bin/jq -r '.statusLine.command // ""' "$SETTINGS" 2>/dev/null || echo "")
case "$CUR_STATUSLINE" in
    *usage-sensor.sh*)
        say "statusLine already chains usage-sensor.sh — leaving as is"
        ;;
    *)
        printf '%s' "$CUR_STATUSLINE" > "$STATUSLINE_ORIG_FILE"
        if [ -n "$CUR_STATUSLINE" ]; then
            NEW_STATUSLINE="bash \"$SENSOR_DST\" \"$CUR_STATUSLINE\""
            say "wrapping existing statusLine (saved original for teardown)"
        else
            NEW_STATUSLINE="bash \"$SENSOR_DST\""
            say "no existing statusLine — installing sensor only (blank status line)"
        fi
        /usr/bin/jq --arg cmd "$NEW_STATUSLINE" \
            '.statusLine = ((.statusLine // {}) + {type: "command", command: $cmd})' \
            "$SETTINGS" > "${SETTINGS}.tmp" && mv "${SETTINGS}.tmp" "$SETTINGS"
        say "statusLine wired"
        ;;
esac

# refreshInterval (seconds) keeps the background session's status line — and
# thus the usage snapshot's record_ts — ticking on a timer. 8s is comfortably
# inside the 5-10s band. Set unconditionally when our sensor owns the
# statusLine (covers the idempotent re-run path above).
case "$(/usr/bin/jq -r '.statusLine.command // ""' "$SETTINGS" 2>/dev/null)" in
    *usage-sensor.sh*)
        /usr/bin/jq '.statusLine.refreshInterval = 8' \
            "$SETTINGS" > "${SETTINGS}.tmp" && mv "${SETTINGS}.tmp" "$SETTINGS"
        say "statusLine refreshInterval set to 8s"
        ;;
esac


step "5c. Usage-monitor trusted workdir (spec 0011)"
# The background claude session (Statistics → Usage monitor) needs a CWD Claude
# Code already trusts, or the folder-trust prompt blocks its TUI and the status
# line never fires. It must ALSO clear past the first-run onboarding / upsell
# prompts — on v2.1.181+ a fresh interactive session blocks on "Try the new
# fullscreen renderer?" and never reaches the ready TUI, so the sensor stays
# silent. Claude Code gates that upsell on ~/.claude.json keys, so we pre-seed
# them: fullscreenUpsellSeenCount past its show-threshold (without lowering an
# existing higher value) and hasCompletedOnboarding. The ~/.claude.json format
# is undocumented — we only add additively, with a backup, and skip gracefully
# if the file is absent. Side effect: you also stop seeing the fullscreen-
# renderer upsell in your own interactive sessions (a wash). There is no
# supported flag/env to suppress these prompts — see ADR-0018.
USAGE_MON_DIR="${HOME}/.claude/cab-usage-monitor"
mkdir -p "$USAGE_MON_DIR"
CLAUDE_JSON="${HOME}/.claude.json"
if [ -f "$CLAUDE_JSON" ]; then
    cp "$CLAUDE_JSON" "${CLAUDE_JSON}.bak.$(date +%Y%m%d-%H%M%S)"
    if /usr/bin/jq --arg p "$USAGE_MON_DIR" \
        '.projects[$p] = ((.projects[$p] // {}) + {hasTrustDialogAccepted: true})
         | .fullscreenUpsellSeenCount = ([(.fullscreenUpsellSeenCount // 0), 99] | max)
         | .hasCompletedOnboarding = true' \
        "$CLAUDE_JSON" > "${CLAUDE_JSON}.tmp" 2>/dev/null; then
        mv "${CLAUDE_JSON}.tmp" "$CLAUDE_JSON"
        say "trusted $USAGE_MON_DIR + silenced onboarding prompts in ~/.claude.json"
    else
        rm -f "${CLAUDE_JSON}.tmp"
        say "WARN: couldn't update ~/.claude.json — usage monitor may hit a trust/onboarding prompt"
    fi
else
    say "no ~/.claude.json yet — usage monitor will need a one-time trust on first run"
fi


step "6. Notification icon"
ASSETS_DIR="${HOOKS_DIR}/assets"
ICON_DST="${ASSETS_DIR}/claude-icon.png"
mkdir -p "$ASSETS_DIR"
if [ -f "$ICON_DST" ]; then
    say "already present: $ICON_DST"
else
    CLAUDE_ICNS="/Applications/Claude.app/Contents/Resources/AppIcon.icns"
    if [ -f "$CLAUDE_ICNS" ]; then
        sips -s format png "$CLAUDE_ICNS" --out "$ICON_DST" \
            --resampleHeightWidth 256 256 >/dev/null 2>&1 \
            && say "extracted from Claude.app → $ICON_DST" \
            || say "WARN: sips failed — notifications will show without custom icon"
    else
        say "Claude.app not found — notifications will show without custom icon"
    fi
fi


step "7. Sanity check hook script"
# Round-trip a fake event through the hook and verify the row lands in the
# TSV. Clean up the test row afterwards so we don't pollute the index.
# PreToolUse → working is the path the hook is most often exercised on
# (every tool call fires it), so smoke-testing that branch tells us the
# real wiring works end-to-end.
echo '{"session_id":"00000000-test","cwd":"/tmp","hook_event_name":"PreToolUse"}' \
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


step "8. Refresh SwiftBar"
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
