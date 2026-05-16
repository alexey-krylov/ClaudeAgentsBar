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
#     hooks/agent-state.sh {working|waiting|idle|session-start}
#
# The state argument is chosen by the caller (see settings-hooks.json) —
# for the three literal states this script just stores whatever it was
# told. ``session-start`` is a pseudo-state for the ``SessionStart``
# hook: Claude Code fires ``SessionStart`` not only on a cold start but
# also when the user *opens* an existing session (the VSCode extension
# fires it on every tab switch, with ``source=resume``). Treating that
# as ``working`` made every tab switch flash the menu yellow even
# though the agent wasn't actually doing anything. With ``session-start``
# we instead branch on ``payload.source``:
#
#     * startup / clear / other → write ``idle`` (fresh session, awaiting
#       the first prompt — UserPromptSubmit will flip it to working).
#     * resume / compact         → leave the existing row untouched. If no
#       row exists yet, write **nothing**: the plugin already falls back
#       to the JSONL transcript's mtime when a session is missing from
#       the TSV, and writing an ``idle`` row with the current timestamp
#       would falsely paint the session FRESH ("Stop fired just now")
#       on every tab switch.
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
# unknown state and we'd rather drop the event than corrupt the TSV.
case "$STATE_NEW" in
    working|waiting|idle|session-start) ;;
    *) exit 0 ;;
esac

# Claude Code provides the payload on stdin; without it we have no session id
# to address, so just exit cleanly.
PAYLOAD="$(cat 2>/dev/null || true)"
[ -z "$PAYLOAD" ] && exit 0

# Pull the four fields we care about in a single jq invocation — fork/exec is
# the dominant cost in a hook this small, so we want exactly one. ``source``
# is only populated for ``SessionStart`` events; for the others it falls back
# to an empty string and is ignored.
IFS=$'\t' read -r SID CWD KIND SOURCE < <(
    /usr/bin/jq -r '[.session_id // "", .cwd // "", .hook_event_name // "", .source // ""] | @tsv' \
        <<<"$PAYLOAD"
)

# Without a session_id there's no row to update — drop the event.
[ -z "${SID:-}" ] && exit 0

# Resolve the ``session-start`` pseudo-state into a concrete action. ``resume``
# and ``compact`` mean "user re-attached to an existing session" — we must not
# clobber whatever state the session was in (that's the bug this branch fixes).
# ``startup`` and ``clear`` are genuinely fresh starts, so ``idle`` is the
# honest answer: the session exists but no prompt has been submitted yet.
KEEP_EXISTING=0
if [ "$STATE_NEW" = "session-start" ]; then
    case "$SOURCE" in
        resume|compact) KEEP_EXISTING=1; STATE_NEW="idle" ;;
        *)              STATE_NEW="idle" ;;
    esac
fi

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
    -v ts="$TS" \
    -v keep_existing="$KEEP_EXISTING" '
    BEGIN              { FS = OFS = "\t"; written = 0 }
    $1 == sid {
        if (!written) {
            if (keep_existing) {
                # SessionStart with source=resume|compact: a row already
                # exists for this session — leave it untouched so we do not
                # falsely flip a working/waiting/idle session back to idle
                # just because the user re-opened it in the IDE.
                print
            } else {
                since = ($2 == new_state && NF >= 6 && $6 ~ /^[0-9]+$/) ? $6 : ts
                print sid, new_state, ts, new_kind, new_cwd, since
            }
            written = 1
        }
        next
    }
                       { print }
    END {
        # No row matched. For a literal state (working/waiting/idle from a
        # real event) we append it. For ``session-start`` with
        # source=resume|compact (keep_existing=1) we write **nothing**: the
        # plugin will fall back to the JSONL mtime and the kind-less row
        # cannot be misclassified as FRESH. Writing here would paint the
        # session green on every IDE tab switch.
        if (!written && !keep_existing) {
            print sid, new_state, ts, new_kind, new_cwd, ts
        }
    }
' "$STATE_FILE" > "$TMP" && mv "$TMP" "$STATE_FILE"

exit 0
