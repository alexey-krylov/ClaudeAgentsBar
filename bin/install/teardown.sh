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
SENSOR_DST="${HOME}/.claude/hooks/usage-sensor.sh"
STATUSLINE_ORIG_FILE="${HOME}/.claude/agent-state.statusline.orig"
SETTINGS="${HOME}/.claude/settings.json"

say()  { printf '  %s\n' "$*"; }
step() { printf '\n→ %s\n' "$*"; }

#: Kept in step with setup.sh — both write into the same ``.bak.*`` family,
#: so both have to rotate it or the count still grows (issue #3).
BACKUPS_KEPT=5

rotate_backups() {
    local prefix="$1" keep="$2" old
    ls -t "${prefix}".bak.* 2>/dev/null | tail -n "+$((keep + 1))" | while IFS= read -r old; do
        rm -f "$old"
    done
}

# Publish "$1".tmp over "$1" only when it differs, taking a rotated backup
# first. Returns 1 when the file was already identical. Mirrors setup.sh.
publish_if_changed() {
    local target="$1" label="${2:-$1}"
    if [ ! -f "${target}.tmp" ]; then
        # No candidate to publish — a caller whose jq failed. Refuse rather
        # than letting `cmp` report "differs" and `mv` a file that isn't there.
        say "WARN: $label — nothing staged to publish"
        return 1
    fi
    if cmp -s "${target}.tmp" "$target"; then
        rm -f "${target}.tmp"
        say "$label already clean — not rewritten, no backup taken"
        # Rotate anyway — see the note in setup.sh.
        rotate_backups "$target" "$BACKUPS_KEPT"
        return 1
    fi
    local backup="${target}.bak.$(date +%Y%m%d-%H%M%S)"
    cp "$target" "$backup"
    say "backup: $backup"
    mv "${target}.tmp" "$target"
    rotate_backups "$target" "$BACKUPS_KEPT"
    return 0
}


step "1. Stop background processes we own"
# spec 0003 — kill any caffeinate holding the machine awake. ADR-0020 — quit a
# leftover pre-1.5.0 background claude usage session, if one is somehow still
# up. Both before we strip the symlink, so the menu's off-switches don't vanish
# out from under the user.
if [ -f "${REPO_DIR}/claude-agents.5s.py" ]; then
    /usr/bin/python3 "${REPO_DIR}/claude-agents.5s.py" --keep-awake-shutdown \
        2>/dev/null || true
    /usr/bin/python3 "${REPO_DIR}/claude-agents.5s.py" --usage-monitor-shutdown \
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
if [ -L "$SENSOR_DST" ]; then
    rm "$SENSOR_DST" && say "removed $SENSOR_DST"
else
    say "not present: $SENSOR_DST"
fi


step "4. Strip our hook entries from settings.json"
if [ -f "$SETTINGS" ]; then
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
    ' "$SETTINGS" > "${SETTINGS}.tmp" || { rm -f "${SETTINGS}.tmp"; echo "jq strip failed — settings.json left untouched"; exit 1; }
    # A second teardown has nothing left to strip, so the result is identical
    # and no backup is taken (issue #3).
    if publish_if_changed "$SETTINGS" "settings.json"; then
        say "cleaned"
    fi

    # Restore the original statusLine. Only relevant when tearing down an
    # install that predates ADR-0020 (setup.sh now unwires this on upgrade):
    # the old setup saved whatever the user had before it wrapped them. Only
    # touch a statusLine that's still ours (chains usage-sensor.sh); a non-empty
    # saved original is put back, an empty one means there was no statusLine,
    # so we drop the key entirely.
    CUR_STATUSLINE=$(/usr/bin/jq -r '.statusLine.command // ""' "$SETTINGS" 2>/dev/null || echo "")
    case "$CUR_STATUSLINE" in
        *usage-sensor.sh*)
            if [ -f "$STATUSLINE_ORIG_FILE" ] && [ -s "$STATUSLINE_ORIG_FILE" ]; then
                ORIG_STATUSLINE=$(cat "$STATUSLINE_ORIG_FILE")
                # Restore the original command and drop our refreshInterval.
                if /usr/bin/jq --arg c "$ORIG_STATUSLINE" \
                    '.statusLine.command = $c | del(.statusLine.refreshInterval)' \
                    "$SETTINGS" > "${SETTINGS}.tmp"; then
                    if publish_if_changed "$SETTINGS" "statusLine"; then
                        say "restored original statusLine"
                    fi
                else
                    rm -f "${SETTINGS}.tmp"
                    say "WARN: couldn't restore statusLine — settings.json left untouched"
                fi
            else
                if /usr/bin/jq 'del(.statusLine)' \
                    "$SETTINGS" > "${SETTINGS}.tmp"; then
                    if publish_if_changed "$SETTINGS" "statusLine"; then
                        say "removed sensor statusLine (no original to restore)"
                    fi
                else
                    rm -f "${SETTINGS}.tmp"
                    say "WARN: couldn't remove statusLine — settings.json left untouched"
                fi
            fi
            rm -f "$STATUSLINE_ORIG_FILE"
            ;;
        *)
            say "statusLine not ours — leaving as is"
            ;;
    esac
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
echo "  ~/.claude/agent-state.usage"
echo "  ~/.claude/agent-state.usage-alerts"
echo "  ~/.claude/agent-state.usage.fetch"
echo "  ~/.claude/agent-state.usage-monitor.mode   (pre-1.5.0)"
echo "  ~/.claude/agent-state.usage-monitor.ping   (pre-1.5.0)"
echo "  ~/.claude/cab-usage-monitor/               (pre-1.5.0 workdir)"
echo "  ~/.claude.json projects entry for cab-usage-monitor (trust flag — left in place)"
