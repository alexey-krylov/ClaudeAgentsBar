#!/bin/bash
#
# Submenu action: make the plugin forget every session it currently knows.
#
# Wired up from claude-agents.5s.py under the *Tools → Forget all sessions*
# entry. "Forget" means we wipe the plugin-owned sidecars so the menu
# starts from a clean slate; we do *not* touch the JSONL transcripts under
# ~/.claude/projects/, which belong to Claude Code itself.
#
# Three things happen, in order:
#
#   1. ~/.claude/agent-state.tsv is truncated under its mkdir-based mutex
#      (the same lock that hooks/agent-state.sh and open-session.sh use).
#      Live working/waiting sessions repopulate it on the next hook event
#      (0–5 s), so this is non-destructive for active work.
#   2. ~/.claude/agent-state.clicks is truncated under its own mutex,
#      resetting every 🔵 ACKNOWLEDGED row back to 🟢 FRESH.
#   3. The current Unix time is written to ~/.claude/agent-state.dismiss
#      as a cutoff. The plugin can still surface a session purely from its
#      JSONL mtime (no TSV row needed), and the cutoff hides those too
#      until they get a fresh hook event.
#
# Nothing on disk that Claude Code itself owns is destroyed. To actually
# delete a session, use the per-row *Delete session…* action.

set -u

STATE_DIR="${HOME}/.claude"
TSV_FILE="${STATE_DIR}/agent-state.tsv"
CLICKS_FILE="${STATE_DIR}/agent-state.clicks"
DISMISS_FILE="${STATE_DIR}/agent-state.dismiss"

mkdir -p "$STATE_DIR"

# Mutex via mkdir, matching the scheme used by hooks/agent-state.sh and
# bin/open-session.sh. Atomic on every POSIX filesystem and no util-linux
# dependency. We factor the acquire/release pair so each sidecar can be
# wiped under *its own* lock — the TSV and the clicks file have separate
# writers and separate lock directories, so a single shared lock would be
# wrong.
acquire_lock() {
    local lock_dir="$1"
    local attempts=0
    until mkdir "$lock_dir" 2>/dev/null; do
        attempts=$((attempts + 1))
        # Cap waiting at ~2 s; if a previous holder crashed without releasing,
        # steal the lock so the menu action doesn't hang.
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

# Atomically replace $1 with an empty file. Writing to a tmp file and
# renaming avoids exposing a half-truncated state to a concurrent reader
# (the plugin reads these files every 5 s) — `> $FILE` alone would.
wipe_file() {
    local target="$1"
    local tmp="${target}.$$"
    : > "$tmp"
    mv "$tmp" "$target"
}

# --- TSV (live state per session) --------------------------------------- #
TSV_LOCK="${TSV_FILE}.lock.d"
trap 'release_lock "$TSV_LOCK"' EXIT
acquire_lock "$TSV_LOCK"
touch "$TSV_FILE"
wipe_file "$TSV_FILE"
release_lock "$TSV_LOCK"
trap - EXIT

# --- Clicks (acknowledgement timestamps) -------------------------------- #
CLICKS_LOCK="${CLICKS_FILE}.lock.d"
trap 'release_lock "$CLICKS_LOCK"' EXIT
acquire_lock "$CLICKS_LOCK"
touch "$CLICKS_FILE"
wipe_file "$CLICKS_FILE"
release_lock "$CLICKS_LOCK"
trap - EXIT

# --- Dismiss cutoff ----------------------------------------------------- #
# Even with TSV wiped, a session can still show up purely from its JSONL
# mtime under ~/.claude/projects/. The cutoff hides anything whose latest
# activity is at or before this moment; the next hook event from a still-
# running session pushes its timestamp past the cutoff and re-surfaces it.
TMP="${DISMISS_FILE}.$$"
date +%s > "$TMP"
mv "$TMP" "$DISMISS_FILE"

# Ping SwiftBar so the menu drops to its empty state in the next render
# rather than at the next 5 s tick.
/usr/bin/open "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

exit 0
