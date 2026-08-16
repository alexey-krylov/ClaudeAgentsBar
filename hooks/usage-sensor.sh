#!/bin/bash
#
# ClaudeAgentsBar usage sensor (spec 0011) — a statusLine wrapper.
#
# Claude Code exposes the Claude.ai subscription rate limits (the rolling
# 5-hour window and the 7-day window) ONLY on the statusLine command's stdin —
# never in a transcript JSONL nor a hook payload. The SwiftBar plugin can't
# see them. So this script is wired in as the statusLine command: it reads the
# JSON payload, writes the usage snapshot to ~/.claude/agent-state.usage for
# the plugin to read on its tick, and then CHAINS to the user's original
# statusLine command (passed as $1) so their status line still renders.
#
#     "statusLine": { "type": "command",
#                     "command": "~/.claude/hooks/usage-sensor.sh \"<orig cmd>\"" }
#
# setup.sh wires this up and saves the original command for a clean teardown.
# With no original ($1 empty) the status line is simply blank.
#
# Snapshot format (one row, tab-separated), read by sidecars.read_usage:
#
#     record_ts  five_used  five_resets_at  seven_used  seven_resets_at
#
# record_ts / *_resets_at are unix epoch seconds; five_used / seven_used are
# floored integer percentages.
# Nothing is written when the payload carries no rate_limits (API-key auth) —
# the chain still runs, so a non-subscription user is unaffected. Nothing is
# written either unless the calling session's cwd is the monitor's trusted work
# folder: the statusLine is global, so every interactive session fires this, and
# a non-monitor session's stale cached rate_limits would otherwise clobber the
# daemon's live snapshot and make the menu flap.
#
# Requires: jq. (date is a macOS builtin.)

set -u

USAGE_SIDECAR="${HOME}/.claude/agent-state.usage"
ORIG="${1:-}"

# ── Parse rate_limits off the statusLine payload ─────────────────────────────
input=$(cat)

# The statusLine command is GLOBAL — every interactive claude session calls it,
# not just our background monitor. A non-monitor session (especially an old one
# reopened via resume) carries a STALE cached rate_limits snapshot from its last
# response; if we wrote that, it would clobber the daemon's live number and the
# menu would flap (5% → 20% → 5% as focus moves between sessions). So ONLY the
# monitor daemon writes the sidecar: gate on cwd being our trusted work folder
# (the daemon always runs there). Every other session falls straight through to
# the chain below and leaves the sidecar untouched.
MON_DIR="${HOME}/.claude/cab-usage-monitor"

snapshot=""
if command -v jq >/dev/null 2>&1 \
   && [ "$(printf '%s' "$input" | jq -r '.cwd // .workspace.current_dir // ""' 2>/dev/null)" = "$MON_DIR" ]; then
    # Single jq pass. Emits "five_used five_resets_at seven_used seven_resets_at"
    # when the 5-hour window is present (subscription auth, after the first
    # response), nothing otherwise.
    read -r five_used five_reset seven_used seven_reset < <(
        printf '%s' "$input" | jq -r '
            if .rate_limits.five_hour then
                "\(.rate_limits.five_hour.used_percentage // 0) " +
                "\(.rate_limits.five_hour.resets_at // 0) " +
                "\(.rate_limits.seven_day.used_percentage // 0) " +
                "\(.rate_limits.seven_day.resets_at // 0)"
            else empty end' 2>/dev/null
    )

    if [ -n "${five_reset:-}" ]; then
        # Floor the percentages (truncate the fraction) so they match Claude
        # Code's own USAGE view — which never shows a percentage higher than
        # the real one — and so a threshold like 50 % only fires at a real
        # >= 50 %, not at 49.6 %. Epochs are already integral (printf rounds any
        # rare fractional form harmlessly).
        now=$(date +%s)
        five_used_i=${five_used%.*};   five_used_i=${five_used_i:-0}
        seven_used_i=${seven_used%.*}; seven_used_i=${seven_used_i:-0}
        five_reset_i=$(printf '%.0f' "$five_reset" 2>/dev/null || echo 0)
        seven_reset_i=$(printf '%.0f' "${seven_reset:-0}" 2>/dev/null || echo 0)
        snapshot=$(printf '%s\t%s\t%s\t%s\t%s\n' \
            "$now" "$five_used_i" "$five_reset_i" "$seven_used_i" "$seven_reset_i")
    fi
fi

# ── Write the snapshot atomically (only when we have one) ────────────────────
if [ -n "$snapshot" ]; then
    tmp="${USAGE_SIDECAR}.$$.tmp"
    # Drop the temp on *both* failure branches (issue #3). The redirect creates
    # the file before printf writes to it, so a failed write leaves a 0-byte
    # orphan behind — and this runs every ~8 s off the statusLine, the highest
    # write frequency in the project.
    if printf '%s' "$snapshot" > "$tmp" 2>/dev/null; then
        mv "$tmp" "$USAGE_SIDECAR" 2>/dev/null || rm -f "$tmp" 2>/dev/null
    else
        rm -f "$tmp" 2>/dev/null
    fi
fi

# ── Chain to the original statusLine, proxying its stdout ────────────────────
# Claude Code stores .statusLine.command as a shell command string, so we run
# the saved original the same way (via the shell) on the same stdin.
if [ -n "$ORIG" ]; then
    printf '%s' "$input" | eval "$ORIG"
fi
