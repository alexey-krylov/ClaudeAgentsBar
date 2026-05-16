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
# this script just stores whatever it was told. ``SessionStart`` is
# deliberately **not** registered: Claude Code fires it not only on a
# cold start but also when the user merely re-opens an existing session
# in the IDE (the VSCode extension emits it on every tab switch). The
# plugin only renders sessions that have a TSV row, so leaving
# SessionStart unregistered keeps "I just clicked the tab" out of the
# menu — a session only appears once a real event (UserPromptSubmit,
# PreToolUse, PostToolUse, Notification, Stop) has fired.
#
# TSV schema (tab-separated, one line per session, latest event wins):
#     <session_id> <state> <last_event_ts> <last_event_kind> <cwd> <state_since>
#
# ``state_since`` is the Unix time at which the session entered its current
# ``state`` — preserved across consecutive events of the same state (so each
# PreToolUse/PostToolUse during one "working" cycle keeps the original start
# time), and bumped to ``last_event_ts`` on a state transition. Old 5‑column
# rows are still accepted on read; the next event upgrades them in place.

set -u

STATE_NEW="${1:-}"
STATE_FILE="${HOME}/.claude/agent-state.tsv"
LOCK_DIR="${STATE_FILE}.lock.d"

# Reject anything we don't understand silently — the plugin can't render an
# unknown state and we'd rather drop the event than corrupt the TSV. This
# also gracefully handles stale ``session-start`` registrations left behind
# by a previous version: they just no-op until the next ``setup.sh`` purges
# them out of the user's settings.json.
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
# The ``state_since`` column is preserved when the new event continues the
# previous state, and bumped to ``ts`` on a transition (or when the old row
# is a legacy 5-column entry that doesn't carry a since-stamp).
TMP="${STATE_FILE}.$$"
/usr/bin/awk \
    -v sid="$SID" \
    -v new_state="$STATE_NEW" \
    -v new_kind="$KIND" \
    -v new_cwd="$CWD" \
    -v ts="$TS" '
    BEGIN              { FS = OFS = "\t"; written = 0 }
    $1 == sid {
        if (!written) {
            since = ($2 == new_state && NF >= 6 && $6 ~ /^[0-9]+$/) ? $6 : ts
            print sid, new_state, ts, new_kind, new_cwd, since
            written = 1
        }
        next
    }
                       { print }
    END {
        if (!written) print sid, new_state, ts, new_kind, new_cwd, ts
    }
' "$STATE_FILE" > "$TMP" && mv "$TMP" "$STATE_FILE"

exit 0
