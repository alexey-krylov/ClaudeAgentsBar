#!/bin/bash
#
# Row action: record a click against a Claude Code session and open it in VSCode.
#
# Wired up from claude-agents.5s.py — every main session row runs:
#
#     open-session.sh <session-id> <vscode-url>
#
# The plugin uses the recorded click to move the session from 🟢 FRESH to
# 🔵 ACKNOWLEDGED on the next tick (~5s), and to restart the 60-minute
# stale timer each time the row is opened. Without this sidecar the
# plugin can't distinguish "user already looked at this thread" from
# "thread finished but is still unread".
#
# TSV schema (tab-separated, one row per session, latest click wins):
#
#     <session-id> <click_ts>

set -u

SID="${1:-}"
URL="${2:-}"

if [ -z "$SID" ] || [ -z "$URL" ]; then
    # Misuse — just exit silently. Showing an osascript dialog on every
    # accidental misclick of a malformed row would be far worse than the
    # row not opening.
    exit 1
fi

CLICKS_FILE="${HOME}/.claude/agent-state.clicks"
LOCK_DIR="${CLICKS_FILE}.lock.d"
TS="$(date +%s)"
LINE="${SID}	${TS}"

mkdir -p "$(dirname "$CLICKS_FILE")"
touch "$CLICKS_FILE"

# Mutex via mkdir, matching the scheme used by hooks/agent-state.sh.
# Atomic on every POSIX filesystem and no util-linux dependency.
acquire_lock() {
    local attempts=0
    until mkdir "$LOCK_DIR" 2>/dev/null; do
        attempts=$((attempts + 1))
        if [ "$attempts" -gt 40 ]; then
            # Stuck holder — steal the lock so the row click doesn't hang.
            rmdir "$LOCK_DIR" 2>/dev/null || true
        fi
        sleep 0.05
    done
}
release_lock() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap release_lock EXIT
acquire_lock

# Replace this session's row, or append if absent, atomically.
TMP="${CLICKS_FILE}.$$"
/usr/bin/awk -v sid="$SID" -v new="$LINE" '
    BEGIN              { FS = OFS = "\t"; written = 0 }
    $1 == sid          { if (!written) { print new; written = 1 } ; next }
                       { print }
    END                { if (!written) print new }
' "$CLICKS_FILE" > "$TMP" && mv "$TMP" "$CLICKS_FILE"

# Open the VSCode session deeplink. We deliberately do this *after*
# recording the click — if the click write blocks (it shouldn't, but
# disk weirdness happens), we'd rather the deeplink lag than the row's
# colour fail to update.
/usr/bin/open "$URL" >/dev/null 2>&1 || true

# Nudge SwiftBar so the colour change shows up immediately instead of
# waiting for the next 5 s tick.
/usr/bin/open "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

exit 0
