#!/bin/bash
#
# ClaudeAgentsBar subscription usage alert (spec 0011).
#
# Like notify-idle.sh this is NOT a Claude Code hook — it is fired by the
# plugin itself (claude_agents_bar/usage_alerts.py) on the SwiftBar tick when
# the 5-hour subscription window's used-% first crosses a threshold
# (50/60/70/80/90 %, template A) or the critical 95 % (template B). The
# rate_limits that drive this are only on the statusLine stdin, captured into
# agent-state.usage by hooks/usage-sensor.sh; the threshold bookkeeping lives
# on the Python side. This script just speaks.
#
# The alert is account-wide — there is no session behind it — so it takes no
# session id / cwd: the banner is non-clickable and carries no project line.
# Arguments are positional (NOT a JSON payload on stdin):
#
#     notify-usage.sh <pct> <kind>      kind = A (threshold) | B (critical)
#
# Can be run by hand for a smoke test: `notify-usage.sh 70 A`.
#
# Requires: terminal-notifier  (brew install terminal-notifier)
# Optional: jq  (for config parsing); afplay/say are macOS builtins.
#
# Config keys read from the ClaudeAgentsBar config.json (all optional):
#   notify_sound_usage           string  "Glass" — built-in sound name, path,
#                                                 ~-path, or null to skip the
#                                                 chime (distinct from Hero =
#                                                 done, Funk = awaiting,
#                                                 Submarine = idle)
#   notify_usage_phrase_threshold string "Session limit at {pct}%" — template A;
#                                                 "{pct}" is replaced with the
#                                                 current percentage
#   notify_usage_phrase_critical  string "Session limit almost exhausted — only
#                                                 a refresh restores it" —
#                                                 template B (95 %)
#   notify_voice                 string  null    — say(1) voice (shared)
#   quiet_hours / quiet_hours_silences           — same silence window as the
#                                                 other notify hooks

set -u

# ── Source the shared helpers ────────────────────────────────────────────────
# Mirrors the symlink-following resolver in the registered hooks; harmless here
# (this script is invoked from the plugin's own hooks dir).
__target="${BASH_SOURCE[0]}"
while [ -L "$__target" ]; do
    __link=$(/usr/bin/readlink -- "$__target")
    case "$__link" in
        /*) __target="$__link" ;;
        *)  __target="$(cd "$(dirname "$__target")" && pwd -P)/$__link" ;;
    esac
done
__HOOK_DIR="$(cd "$(dirname "$__target")" && pwd -P)"
if [ ! -f "${__HOOK_DIR}/_notify-common.sh" ]; then
    echo "[notify-usage] missing ${__HOOK_DIR}/_notify-common.sh; re-run setup" >&2
    exit 0
fi
# shellcheck source=/dev/null
. "${__HOOK_DIR}/_notify-common.sh"

# ── Read config ──────────────────────────────────────────────────────────────
# Default chime is Glass — a crisp, distinct tone set apart from Hero (done),
# Funk (awaiting) and Submarine (idle) so a usage alert reads as its own kind.
SOUND_RAW=$(_cfg_string_or_null "notify_sound_usage" "Glass")
SOUND_PATH=$(_resolve_sound "$SOUND_RAW")
VOICE=$(_cfg_string             "notify_voice"        "")

# Quiet-hours gate (spec 0002) — identical to stop/wait/idle, so a usage alert
# is muted in quiet hours just like every other notification.
_compute_quiet_state

# Notification-audio master switch (Tools → Notifications). "Banner only"
# mutes both audio channels regardless of quiet hours; the banner still fires.
if [ "$(_notify_audio_enabled)" = "false" ]; then
    SUPPRESS_SOUND=true
    SUPPRESS_VOICE=true
fi

# ── Positional input (from the plugin, not a hook payload) ───────────────────
PCT="${1:-0}"
KIND="${2:-A}"

if [ "$KIND" = "B" ]; then
    TMPL=$(_cfg_string "notify_usage_phrase_critical" \
        "Session limit almost exhausted — only a refresh restores it")
    MSG="$TMPL"
    ICON="🪫"
else
    TMPL=$(_cfg_string "notify_usage_phrase_threshold" "Session limit at {pct}%")
    MSG="${TMPL//\{pct\}/$PCT}"
    ICON="📊"
fi

# ── Chime + speech + banner (shared emit) ────────────────────────────────────
# Account-wide: no session url / id / cwd, so the banner is non-clickable and
# has no project-branch subtitle (_emit_notification / _banner_subtitle handle
# the empty positionals). Line 1 carries the message with a type icon; the
# spoken text is the message without the icon (no "chart emoji" read aloud).
_emit_notification "${ICON} $MSG" "" "$MSG" "" "" ""
