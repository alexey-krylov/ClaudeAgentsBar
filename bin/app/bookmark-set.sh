#!/bin/bash
#
# Row action: pin (bookmark) or unpin a Claude Code session so it survives
# the render window — see docs/specs/0012-bookmarks.md.
#
# Wired up from claude-agents.5s.py — the "Bookmark" checkbox in every
# session's submenu runs it via /bin/bash so it doesn't depend on the
# executable bit (same reason as multi-workspace-set.sh):
#
#     /bin/bash bookmark-set.sh <session-id> <on|off>
#
# "on"  → record <session-id> <bookmarked_at> if the session isn't already
#         pinned (re-pinning keeps the original date — the pin age is shown
#         in the menu).
# "off" → drop the session's row.
#
# TSV schema (tab-separated, one row per pinned session):
#
#     <session-id> <bookmarked_at>

set -u

SID="${1:-}"
MODE="${2:-}"
if [ -z "$SID" ] || { [ "$MODE" != "on" ] && [ "$MODE" != "off" ]; }; then
    # Misuse — exit silently. A dialog on every malformed click would be
    # far worse UX than the checkbox not flipping.
    exit 1
fi

BOOKMARKS_FILE="${HOME}/.claude/agent-state.bookmarks"
LOCK_DIR="${BOOKMARKS_FILE}.lock.d"
TS="$(date +%s)"
LINE="${SID}	${TS}"

mkdir -p "$(dirname "$BOOKMARKS_FILE")"
touch "$BOOKMARKS_FILE"

# Mutex via mkdir, matching the scheme used by the other sidecars
# (hooks/agent-state.sh, bin/app/forget-session.sh). Atomic on every POSIX
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

TMP="${BOOKMARKS_FILE}.$$"
if [ "$MODE" = "on" ]; then
    # Add only if absent — a re-pin must not reset bookmarked_at, so the
    # displayed "Added Xm ago" keeps counting from the first pin.
    /usr/bin/awk -v sid="$SID" -v new="$LINE" '
        BEGIN     { FS = OFS = "\t"; seen = 0 }
        $1 == sid { seen = 1 }
                  { print }
        END       { if (!seen) print new }
    ' "$BOOKMARKS_FILE" > "$TMP" && mv "$TMP" "$BOOKMARKS_FILE"
else
    # off — drop the session's row.
    /usr/bin/awk -v sid="$SID" '
        BEGIN     { FS = OFS = "\t" }
        $1 == sid { next }
                  { print }
    ' "$BOOKMARKS_FILE" > "$TMP" && mv "$TMP" "$BOOKMARKS_FILE"
fi

# Nudge SwiftBar so the checkbox + Bookmarks list update immediately instead
# of waiting for the next 5 s tick.
/usr/bin/open "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

exit 0
