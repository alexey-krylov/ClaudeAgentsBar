#!/bin/bash
#
# Claude Code hook that maintains a live state index for ClaudeAgentsBar.
#
# Registered against SessionStart / UserPromptSubmit / PreToolUse /
# PostToolUse / Notification / Stop. The Claude Code runtime invokes this
# script on every matching event, piping a JSON payload to stdin of the
# shape:
#
#     {
#       "session_id":      "<uuid>",
#       "cwd":             "/path/to/working/dir",
#       "hook_event_name": "<event>"
#     }
#
# We extract three fields, stamp the current Unix time, and atomically
# rewrite the row for this session in ~/.claude/agent-state.tsv. The
# SwiftBar plugin reads that file every 5 s to colour the menu.
#
# Usage:
#     hooks/agent-state.sh {working|waiting|idle}
#
# The state argument is chosen by the caller (see settings-hooks.json) —
# this script just stores whatever it was told.
#
# TSV schema (tab-separated, one line per session, latest event wins):
#     <session_id> <state> <last_event_ts> <last_event_kind> <cwd>

set -u

STATE_NEW="${1:-}"
STATE_FILE="${HOME}/.claude/agent-state.tsv"
LOCK_DIR="${STATE_FILE}.lock.d"

# Reject anything we don't understand silently — the plugin can't render an
# unknown state and we'd rather drop the event than corrupt the TSV.
case "$STATE_NEW" in
    working|waiting|idle) ;;
    *) exit 0 ;;
esac

# Claude Code provides the payload on stdin; without it we have no session id
# to address, so just exit cleanly.
PAYLOAD="$(cat 2>/dev/null || true)"
[ -z "$PAYLOAD" ] && exit 0

# Pull the three fields we care about in a single jq invocation — fork/exec is
# the dominant cost in a hook this small, so we want exactly one.
IFS=$'\t' read -r SID CWD KIND < <(
    /usr/bin/jq -r '[.session_id // "", .cwd // "", .hook_event_name // ""] | @tsv' \
        <<<"$PAYLOAD"
)

# Without a session_id there's no row to update — drop the event.
[ -z "${SID:-}" ] && exit 0

TS="$(date +%s)"
LINE="${SID}	${STATE_NEW}	${TS}	${KIND}	${CWD}"

mkdir -p "$(dirname "$STATE_FILE")"
touch "$STATE_FILE"

# Mutex via mkdir: atomic on every POSIX filesystem, and unlike `flock` it
# doesn't require util-linux (which isn't on stock macOS). Busy-wait with a
# short sleep — contention is rare (one hook per Claude Code event) and the
# critical section is microseconds long.
acquire_lock() {
    local attempts=0
    until mkdir "$LOCK_DIR" 2>/dev/null; do
        attempts=$((attempts + 1))
        # Cap waiting at ~2 s; if a previous hook crashed without releasing,
        # steal the lock so we don't deadlock indefinitely.
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

# Replace this session's row (or append it if absent) and atomically swap the
# file into place. ``awk`` is fast enough for the typical few-dozen-row TSV.
TMP="${STATE_FILE}.$$"
/usr/bin/awk -v sid="$SID" -v new="$LINE" '
    BEGIN              { FS = OFS = "\t"; written = 0 }
    $1 == sid          { if (!written) { print new; written = 1 } ; next }
                       { print }
    END                { if (!written) print new }
' "$STATE_FILE" > "$TMP" && mv "$TMP" "$STATE_FILE"

exit 0
