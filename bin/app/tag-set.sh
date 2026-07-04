#!/bin/bash
#
# Row action: set or clear a session's Finder-style color tag — see
# docs/specs/0013-tags.md.
#
# Wired up from claude-agents.5s.py — each color in a session's "Tags ▸"
# submenu runs it via /bin/bash so it doesn't depend on the executable bit
# (same reason as bookmark-set.sh):
#
#     /bin/bash tag-set.sh <session-id> <color-key|clear>
#
# <color-key> (red|orange|yellow|green|blue|purple|white) → REPLACE: drop any
#             existing row for the session, then write the new color. Changing
#             color overwrites (unlike bookmark-set.sh's preserve-on-add).
# "clear"   → delete the session's row.
#
# TSV schema (tab-separated, one row per tagged session):
#
#     <session-id> <color-key>

set -u

SID="${1:-}"
MODE="${2:-}"
case "$MODE" in
    red|orange|yellow|green|blue|purple|white|clear) ;;
    *)
        # Misuse — exit silently. Render only ever passes valid values.
        exit 1
        ;;
esac
if [ -z "$SID" ]; then
    exit 1
fi

TAGS_FILE="${HOME}/.claude/agent-state.tags"
LOCK_DIR="${TAGS_FILE}.lock.d"
LINE="${SID}	${MODE}"

mkdir -p "$(dirname "$TAGS_FILE")"
touch "$TAGS_FILE"

# Mutex via mkdir, matching the scheme used by the other sidecars
# (hooks/agent-state.sh, bin/app/bookmark-set.sh). Atomic on every POSIX
# filesystem and no util-linux dependency.
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
trap release_lock EXIT
acquire_lock

TMP="${TAGS_FILE}.$$"
if [ "$MODE" = "clear" ]; then
    /usr/bin/awk -v sid="$SID" '
        BEGIN     { FS = OFS = "\t" }
        $1 == sid { next }
                  { print }
    ' "$TAGS_FILE" > "$TMP" && mv "$TMP" "$TAGS_FILE"
else
    # Replace: drop any existing row for the session, append the new color.
    /usr/bin/awk -v sid="$SID" -v new="$LINE" '
        BEGIN     { FS = OFS = "\t" }
        $1 == sid { next }
                  { print }
        END       { print new }
    ' "$TAGS_FILE" > "$TMP" && mv "$TMP" "$TAGS_FILE"
fi

# Nudge SwiftBar so the flag updates immediately instead of waiting for the
# next 5 s tick.
/usr/bin/open "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

exit 0
