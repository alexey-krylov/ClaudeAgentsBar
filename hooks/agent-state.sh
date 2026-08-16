#!/bin/bash
#
# Claude Code hook that maintains live state indexes for ClaudeAgentsBar.
#
# Registered against UserPromptSubmit / PreToolUse / PostToolUse /
# Notification / PermissionRequest / Stop / SubagentStop. The Claude
# Code runtime invokes this script on every matching event, piping a
# JSON payload to stdin of the shape:
#
#     {
#       "session_id":      "<parent-uuid>",
#       "cwd":             "/path/to/working/dir",
#       "hook_event_name": "<event>",
#       "agent_id":        "<16-hex>",      # only present inside subagents
#       "agent_type":      "Explore",       # only present inside subagents
#       ...
#     }
#
# Two writes are possible per event, routed by ``agent_id``:
#
# * **Parent-side** event (``agent_id`` absent) — updates the row keyed on
#   ``session_id`` in ~/.claude/agent-state.tsv. Same TSV the plugin has
#   read since v1.0.
# * **Subagent-side** event (``agent_id`` present) — updates the row keyed
#   on ``(session_id, agent_id)`` in ~/.claude/agent-state.subagents.tsv.
#   This keeps subagent activity from clobbering the parent row's
#   ``last_event_kind`` / ``cwd``, while still giving the plugin a
#   liveness signal to roll up into the parent's state (so the parent
#   doesn't drift 🟡 → 🟢 → 🔵 while the subagent is in flight). See
#   docs/specs/0004-subagent-grouping.md § Spike outcome.
#
# Usage:
#     hooks/agent-state.sh {working|waiting|idle|stopped}
#
# The state argument is chosen by the caller (see hooks/settings-hooks.json):
#
# * ``working`` — UserPromptSubmit / PreToolUse / PostToolUse
# * ``waiting`` — Notification / PermissionRequest
# * ``idle``    — Stop  (parent-side only — Stop never carries agent_id)
# * ``stopped`` — SubagentStop  (subagent-side only — agent_id always set)
#
# ``SessionStart`` is deliberately **not** registered: Claude Code fires
# it not only on a cold start but also when the user merely re-opens an
# existing session in the IDE (the VSCode extension emits it on every
# tab switch). The plugin only renders sessions that have a TSV row, so
# leaving SessionStart unregistered keeps "I just clicked the tab" out of
# the menu.
#
# Parent TSV schema (unchanged from v1.0):
#     <session_id> <state> <last_event_ts> <last_event_kind> <cwd> <state_since>
#
# Subagent TSV schema (new in v1.1):
#     <parent_sid> <agent_id> <agent_type> <state> <state_since> <last_event_ts> <first_event_ts>
#
# ``state_since`` is preserved across consecutive events of the same state
# and bumped on a transition, identical semantics to the parent TSV.
# ``first_event_ts`` is set once on the row's first sighting and never
# touched again, so the plugin can render "ran Xs" for stopped subagents
# (= last_event_ts - first_event_ts). Legacy 6-column rows lack this
# field; the parser treats it as missing and the renderer omits the
# duration suffix for those rows.

set -u

STATE_NEW="${1:-}"
PARENT_STATE_FILE="${HOME}/.claude/agent-state.tsv"
PARENT_LOCK_DIR="${PARENT_STATE_FILE}.lock.d"
SUBAGENT_STATE_FILE="${HOME}/.claude/agent-state.subagents.tsv"
SUBAGENT_LOCK_DIR="${SUBAGENT_STATE_FILE}.lock.d"

# Reject anything we don't understand silently — the plugin can't render an
# unknown state and we'd rather drop the event than corrupt the TSV. This
# also gracefully handles stale ``session-start`` registrations left behind
# by a previous version: they just no-op until the next ``setup.sh`` purges
# them out of the user's settings.json.
case "$STATE_NEW" in
    working|waiting|idle|stopped) ;;
    *) exit 0 ;;
esac

# Claude Code provides the payload on stdin; without it we have no session id
# to address, so just exit cleanly.
PAYLOAD="$(cat 2>/dev/null || true)"
[ -z "$PAYLOAD" ] && exit 0

# Pull all the fields we care about in a single jq invocation — fork/exec is
# the dominant cost in a hook this small, so we want exactly one. The order
# matches the IFS read below; missing fields come back as empty strings.
IFS=$'\t' read -r SID CWD KIND AGENT_ID AGENT_TYPE < <(
    /usr/bin/jq -r '[.session_id // "", .cwd // "", .hook_event_name // "", .agent_id // "", .agent_type // ""] | @tsv' \
        <<<"$PAYLOAD"
)

# Without a session_id there's no row to update — drop the event.
[ -z "${SID:-}" ] && exit 0

TS="$(date +%s)"

# Route: subagent-side events go to the subagent sidecar, everything else
# to the parent sidecar. ``stopped`` is only meaningful for subagents
# (SubagentStop), and ``idle`` only for parents (Stop). Cross combinations
# are silently dropped to keep both TSVs schema-clean.
if [ -n "${AGENT_ID:-}" ]; then
    case "$STATE_NEW" in
        working|stopped) ;;
        *) exit 0 ;;
    esac
else
    case "$STATE_NEW" in
        working|waiting|idle) ;;
        *) exit 0 ;;
    esac
fi

# Mutex via mkdir: atomic on every POSIX filesystem, and unlike `flock` it
# doesn't require util-linux (which isn't on stock macOS). Busy-wait with a
# short sleep — contention is rare (one hook per Claude Code event) and the
# critical section is microseconds long.
acquire_lock() {
    local lock_dir="$1"
    local attempts=0
    until mkdir "$lock_dir" 2>/dev/null; do
        attempts=$((attempts + 1))
        # Cap waiting at ~2 s; if a previous hook crashed without releasing,
        # steal the lock so we don't deadlock indefinitely.
        if [ "$attempts" -gt 40 ]; then
            rmdir "$lock_dir" 2>/dev/null || true
        fi
        sleep 0.05
    done
}
release_lock() {
    local lock_dir="$1"
    rmdir "$lock_dir" 2>/dev/null || true
}

if [ -n "${AGENT_ID:-}" ]; then
    # Subagent-side: update the (parent_sid, agent_id) row in the subagent
    # sidecar. We deliberately do not touch the parent sidecar — subagent
    # tool calls aren't parent activity, and treating them as such was
    # clobbering the parent row's last_event_kind / cwd.
    mkdir -p "$(dirname "$SUBAGENT_STATE_FILE")"
    touch "$SUBAGENT_STATE_FILE"

    # The EXIT trap also drops the temp file: the write below is
    # ``awk … > "$TMP" && mv``, so a non-zero awk — or a kill mid-write —
    # would otherwise strand ``$TMP`` in ~/.claude forever (issue #3). ``${TMP:-}``
    # because the trap is installed before TMP is assigned, and ``rm -f ""`` is a
    # silent no-op; after a successful mv the path is gone and this is one too.
    trap 'rm -f "${TMP:-}"; release_lock "$SUBAGENT_LOCK_DIR"' EXIT
    acquire_lock "$SUBAGENT_LOCK_DIR"

    TMP="${SUBAGENT_STATE_FILE}.$$"
    /usr/bin/awk \
        -v sid="$SID" \
        -v aid="$AGENT_ID" \
        -v atype="$AGENT_TYPE" \
        -v new_state="$STATE_NEW" \
        -v ts="$TS" '
        BEGIN              { FS = OFS = "\t"; written = 0 }
        $1 == sid && $2 == aid {
            if (!written) {
                # Preserve state_since across consecutive same-state events;
                # bump it on a transition (and accept missing/garbage as ts).
                since = ($4 == new_state && NF >= 6 && $5 ~ /^[0-9]+$/) ? $5 : ts
                # Agent type is set on first sighting and pinned; never overwrite
                # with an empty string (some events may omit it).
                type_out = (atype != "") ? atype : (NF >= 3 ? $3 : "")
                # ``first_event_ts`` (col 7) is set once on first sighting
                # and pinned forever. Legacy 6-column rows backfill from
                # state_since — best approximation we have post-hoc; new
                # rows always carry it explicitly.
                first_ts = (NF >= 7 && $7 ~ /^[0-9]+$/) ? $7 : (($5 ~ /^[0-9]+$/) ? $5 : ts)
                print sid, aid, type_out, new_state, since, ts, first_ts
                written = 1
            }
            next
        }
                           { print }
        END {
            if (!written) print sid, aid, atype, new_state, ts, ts, ts
        }
    ' "$SUBAGENT_STATE_FILE" > "$TMP" && mv "$TMP" "$SUBAGENT_STATE_FILE"
    exit 0
fi

# Parent-side: update the session_id row in the main sidecar — exactly the
# pre-v1.1 codepath. The ``state_since`` column is preserved when the new
# event continues the previous state, and bumped to ``ts`` on a transition
# (or when the old row is a legacy 5-column entry that doesn't carry a
# since-stamp).
mkdir -p "$(dirname "$PARENT_STATE_FILE")"
touch "$PARENT_STATE_FILE"

# The trap also drops ``$TMP`` — ``awk … && mv`` strands it on a non-zero
# awk or a kill mid-write (issue #3). ``rm -f ""`` is a silent no-op.
trap 'rm -f "${TMP:-}"; release_lock "$PARENT_LOCK_DIR"' EXIT
acquire_lock "$PARENT_LOCK_DIR"

TMP="${PARENT_STATE_FILE}.$$"
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
' "$PARENT_STATE_FILE" > "$TMP" && mv "$TMP" "$PARENT_STATE_FILE"

exit 0
