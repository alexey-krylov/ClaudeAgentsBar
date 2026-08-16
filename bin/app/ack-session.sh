#!/bin/bash
#
# Submenu action: mark one 🟢 FRESH session as read without opening it.
#
# Wired up from claude-agents.5s.py — the per-row submenu under FRESH
# sessions runs:
#
#     ack-session.sh <session-id>
#
# Mirrors the click-recording half of ``open-session.sh`` but skips the
# deeplink: the user wanted to dismiss the unread badge, not jump into
# the editor. The plugin reads the same sidecar and reclassifies the row
# from 🟢 FRESH to 🔵 ACKNOWLEDGED on the next tick.
#
# TSV schema (tab-separated, one row per session, latest click wins):
#
#     <session-id> <click_ts>

set -u

SID="${1:-}"

if [ -z "$SID" ]; then
    exit 1
fi

CLICKS_FILE="${HOME}/.claude/agent-state.clicks"
LOCK_DIR="${CLICKS_FILE}.lock.d"
TS="$(date +%s)"
LINE="${SID}	${TS}"

mkdir -p "$(dirname "$CLICKS_FILE")"
touch "$CLICKS_FILE"

# Mutex via mkdir, matching the scheme used by hooks/agent-state.sh and
# open-session.sh. Atomic on every POSIX filesystem.
acquire_lock() {
    local attempts=0
    until mkdir "$LOCK_DIR" 2>/dev/null; do
        attempts=$((attempts + 1))
        if [ "$attempts" -gt 40 ]; then
            rmdir "$LOCK_DIR" 2>/dev/null || true
        fi
        sleep 0.05
    done
}
release_lock() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
# The trap also drops ``$TMP`` — ``awk … && mv`` strands it on a non-zero
# awk or a kill mid-write (issue #3). ``rm -f ""`` is a silent no-op.
trap 'rm -f "${TMP:-}"; release_lock' EXIT
acquire_lock

TMP="${CLICKS_FILE}.$$"
/usr/bin/awk -v sid="$SID" -v new="$LINE" '
    BEGIN              { FS = OFS = "\t"; written = 0 }
    $1 == sid          { if (!written) { print new; written = 1 } ; next }
                       { print }
    END                { if (!written) print new }
' "$CLICKS_FILE" > "$TMP" && mv "$TMP" "$CLICKS_FILE"

# Nudge SwiftBar so the colour change shows up immediately rather than
# at the next 5 s tick — same pattern as the other row/Tools actions.
/usr/bin/open "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

exit 0
