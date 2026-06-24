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
#     record_ts  five_used  five_resets_at  seven_used  seven_target
#
# record_ts / five_resets_at are unix epoch seconds; five_used / seven_used are
# integer percentages; seven_target is the weekly pacing target (see below).
# Nothing is written when the payload carries no rate_limits (API-key auth) —
# the chain still runs, so a non-subscription user is unaffected.
#
# Requires: jq. (date is a macOS builtin.)

set -u

USAGE_SIDECAR="${HOME}/.claude/agent-state.usage"
ORIG="${1:-}"

# ── WEEKLY PACING MODEL (EDIT FOR YOUR OWN SCHEDULE) ─────────────────────────
# The weekly segment shows "used%/target%", where target% is a self-imposed
# cumulative pacing goal for how much of the weekly quota you intend to have
# spent by the end of the current day-bucket. The week is split into buckets
# from the weekly reset; each bucket's cumulative target lives in WK_CUM, in
# order from the reset: [FriPM+Sat+Sun, Mon, Tue, Wed, Thu, Fri-AM]. The
# defaults below derive from one user's office-hour weights and a Friday
# 14:00 Singapore reset — CHANGE THEM to match your timezone, reset day/hour,
# and how you want to pace your week.
WEEK_TZ="Asia/Singapore"
WEEK_RESET_HOUR=14
WK_CUM=(9.5 29.5 49.5 69.5 89.5 100)

# ── Parse rate_limits off the statusLine payload ─────────────────────────────
input=$(cat)

snapshot=""
if command -v jq >/dev/null 2>&1; then
    # Single jq pass. Emits "five_used five_resets_at seven_used" when the
    # 5-hour window is present (subscription auth, after the first response),
    # nothing otherwise.
    read -r five_used five_reset seven_used < <(
        printf '%s' "$input" | jq -r '
            if .rate_limits.five_hour then
                "\(.rate_limits.five_hour.used_percentage // 0) " +
                "\(.rate_limits.five_hour.resets_at // 0) " +
                "\(.rate_limits.seven_day.used_percentage // 0)"
            else empty end' 2>/dev/null
    )

    if [ -n "${five_reset:-}" ]; then
        # Weekly pacing target L = cumulative goal for the current day-bucket
        # (a straight WK_CUM lookup — no arithmetic, so no bc/awk dependency).
        dow=$(TZ="$WEEK_TZ" date +%u)            # 1=Mon .. 7=Sun
        hour=$((10#$(TZ="$WEEK_TZ" date +%H)))   # strip any leading zero
        bkt=0
        case "$dow" in
            5) [ "$hour" -ge "$WEEK_RESET_HOUR" ] && bkt=0 || bkt=5 ;;  # Fri
            6|7) bkt=0 ;;                                               # Sat/Sun
            1) bkt=1 ;; 2) bkt=2 ;; 3) bkt=3 ;; 4) bkt=4 ;;             # Mon..Thu
        esac
        L="${WK_CUM[$bkt]}"

        # Floor the percentages (truncate the fraction) so they match Claude
        # Code's own USAGE view — which never shows a percentage higher than
        # the real one — and so a threshold like 50 % only fires at a real
        # >= 50 %, not at 49.6 %. Epoch is already integral (printf rounds any
        # rare fractional form harmlessly).
        now=$(date +%s)
        five_used_i=${five_used%.*};   five_used_i=${five_used_i:-0}
        seven_used_i=${seven_used%.*}; seven_used_i=${seven_used_i:-0}
        five_reset_i=$(printf '%.0f' "$five_reset" 2>/dev/null || echo 0)
        snapshot=$(printf '%s\t%s\t%s\t%s\t%s\n' \
            "$now" "$five_used_i" "$five_reset_i" "$seven_used_i" "$L")
    fi
fi

# ── Write the snapshot atomically (only when we have one) ────────────────────
if [ -n "$snapshot" ]; then
    tmp="${USAGE_SIDECAR}.$$.tmp"
    if printf '%s' "$snapshot" > "$tmp" 2>/dev/null; then
        mv "$tmp" "$USAGE_SIDECAR" 2>/dev/null || rm -f "$tmp" 2>/dev/null
    fi
fi

# ── Chain to the original statusLine, proxying its stdout ────────────────────
# Claude Code stores .statusLine.command as a shell command string, so we run
# the saved original the same way (via the shell) on the same stdin.
if [ -n "$ORIG" ]; then
    printf '%s' "$input" | eval "$ORIG"
fi
