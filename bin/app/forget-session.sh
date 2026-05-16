#!/bin/bash
#
# Row action: forget a Claude Code session — hide its row from the menu
# without deleting the transcript.
#
# Wired up from claude-agents.5s.py — each session row's "Forget" submenu
# item runs:
#
#     forget-session.sh <session-id>
#
# "Forget" is the row-level twin of the global *Tools → Forget all sessions*
# action: we record a cutoff timestamp for this session id, and the plugin
# filters rows whose ``last_event_ts`` is at or before that cutoff. A fresh
# hook event or click pushes ``last_event_ts`` past the cutoff and the row
# re-surfaces — which is the intended escape hatch if the row turns out to
# matter again. Use *Delete session…* to physically remove the transcript.
#
# TSV schema (tab-separated, one row per session, latest forget wins):
#
#     <session-id> <forget_ts>

set -u

SID="${1:-}"
if [ -z "$SID" ]; then
    # Misuse — exit silently. A dialog on every malformed click would be
    # far worse UX than the row not hiding.
    exit 1
fi

FORGET_FILE="${HOME}/.claude/agent-state.forget"
LOCK_DIR="${FORGET_FILE}.lock.d"
TS="$(date +%s)"
LINE="${SID}	${TS}"

mkdir -p "$(dirname "$FORGET_FILE")"
touch "$FORGET_FILE"

# Mutex via mkdir, matching the scheme used by the other sidecars
# (hooks/agent-state.sh, bin/open-session.sh). Atomic on every POSIX
# filesystem and no util-linux dependency.
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

# Replace this session's row, or append if absent, atomically — same shape
# as open-session.sh.
TMP="${FORGET_FILE}.$$"
/usr/bin/awk -v sid="$SID" -v new="$LINE" '
    BEGIN              { FS = OFS = "\t"; written = 0 }
    $1 == sid          { if (!written) { print new; written = 1 } ; next }
                       { print }
    END                { if (!written) print new }
' "$FORGET_FILE" > "$TMP" && mv "$TMP" "$FORGET_FILE"

# Nudge SwiftBar so the row disappears immediately instead of waiting for
# the next 5 s tick.
/usr/bin/open "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

exit 0
