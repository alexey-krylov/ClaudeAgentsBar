#!/bin/bash
#
# Record a click (ack) against a Claude Code session into
# ~/.claude/agent-state.clicks. Single source of truth for the ack write,
# shared by every path that resumes a session in the editor:
#
#   * bin/app/open-session.sh — menu-bar dropdown row click
#   * hooks/raise-and-open.sh — notification-banner click
#
# The plugin reads the recorded click to move the session 🟢 FRESH →
# 🔵 ACKNOWLEDGED on the next tick (~5s) and to restart the 60-minute stale
# timer. Without this sidecar it can't tell "user already looked at this
# thread" from "thread finished but is still unread".
#
#   record-click.sh <session-id>
#
# TSV schema (tab-separated, one row per session, latest click wins):
#
#     <session-id> <click_ts>
#
# Best-effort: a missing/empty id is a no-op. Invoked via `/bin/bash` from
# both callers so it doesn't depend on an executable bit surviving
# distribution (Homebrew bottle / zip) or setup's chmod.

set -u

SID="${1:-}"
[ -n "$SID" ] || exit 0

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
            # Stuck holder — steal the lock so the click write doesn't hang.
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

exit 0
