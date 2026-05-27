#!/bin/bash
#
# Shared library for hooks/notify-stop.sh and hooks/notify-wait.sh.
#
# Both hooks read the same JSON config the plugin uses, resolve the same
# audio knobs, and gate on the same quiet-hours window — so the actual
# logic lives here and the hook scripts stay focused on their event-
# specific concerns (which sound, which phrase list, which banner title).
#
# Exports:
#
#   * `_CAB_CONFIG`            — resolved config path.
#   * `_cfg_bool/int/string`   — jq-backed readers, fall back to default
#                                on missing key / wrong type / missing
#                                file / missing jq.
#   * `_cfg_string_or_null`    — like _cfg_string but lets an explicit
#                                JSON `null` flow through as the empty
#                                string (used by nullable knobs where
#                                `null` means *suppress*).
#   * `_resolve_sound`         — built-in name / abs path / ~-path /
#                                empty → existing file path or empty.
#   * `_compute_quiet_state`   — sets `QUIET_NOW` plus
#                                `SUPPRESS_SOUND` / `SUPPRESS_VOICE` /
#                                `SUPPRESS_BANNER` from the scheduled
#                                window + ad-hoc sidecar + silences list.
#
# Sourced via the symlink-following resolver at the top of each hook;
# this file is never executed directly. Callers run with `set -u`, so
# every global we touch is initialised before first read.

# ── Config path (mirrors XDG logic in claude_agents_bar/core.py) ─────────────
if [ -n "${CLAUDE_AGENTS_BAR_CONFIG:-}" ]; then
    _CAB_CONFIG="$CLAUDE_AGENTS_BAR_CONFIG"
else
    _XDG="${XDG_CONFIG_HOME:-$HOME/.config}"
    _CAB_CONFIG="${_XDG}/claude-agents-bar/config.json"
fi

# ── jq-backed config readers (graceful no-op when jq / file absent) ─────────
_cfg_bool() {
    local key="$1" default="$2"
    [ -f "$_CAB_CONFIG" ] || { echo "$default"; return; }
    local val
    val=$(/usr/bin/jq -r \
        --arg k "$key" \
        'if .[$k] | type == "boolean" then (if .[$k] then "true" else "false" end) else empty end' \
        "$_CAB_CONFIG" 2>/dev/null)
    echo "${val:-$default}"
}

_cfg_int() {
    local key="$1" default="$2"
    [ -f "$_CAB_CONFIG" ] || { echo "$default"; return; }
    local val
    val=$(/usr/bin/jq -r \
        --arg k "$key" \
        'if .[$k] | type == "number" then (.[$k] | floor | tostring) else empty end' \
        "$_CAB_CONFIG" 2>/dev/null)
    echo "${val:-$default}"
}

_cfg_string() {
    local key="$1" default="$2"
    [ -f "$_CAB_CONFIG" ] || { echo "$default"; return; }
    local val
    val=$(/usr/bin/jq -r \
        --arg k "$key" --arg d "$default" \
        'if .[$k] | type == "string" then .[$k] else $d end' \
        "$_CAB_CONFIG" 2>/dev/null)
    echo "${val:-$default}"
}

# Like _cfg_string but treats an explicit JSON `null` as the empty string
# rather than falling back to `$default`. Used by nullable knobs
# (`notify_sound_*`) where `null` means *suppress* and absence means
# *use default*. Anything that isn't a string or null still falls back.
_cfg_string_or_null() {
    local key="$1" default="$2"
    [ -f "$_CAB_CONFIG" ] || { echo "$default"; return; }
    /usr/bin/jq -r \
        --arg k "$key" --arg d "$default" \
        'if has($k) then
            (if .[$k] == null then ""
             elif (.[$k] | type) == "string" then .[$k]
             else $d end)
         else $d end' \
        "$_CAB_CONFIG" 2>/dev/null
}

# ── Sound resolver ───────────────────────────────────────────────────────────
# Resolve a `notify_sound_*` value to an absolute, existing file path.
# Echoes the resolved path on stdout or the empty string when the chime
# should be skipped. Warnings go to stderr; SwiftBar captures the hook's
# stderr in its log.
#
#   ""                        → "" (suppress; `null` in config arrives as empty)
#   "/abs/path.aiff"          → that path if it exists; "" + warning otherwise
#   "~/x.aiff"                → expanded; "" + warning otherwise
#   "Hero" (bare name)        → /System/Library/Sounds/Hero.aiff if it exists
#   "foo/bar" (relative path) → "" + warning (ambiguous)
_resolve_sound() {
    local raw="$1"
    [ -z "$raw" ] && return
    case "$raw" in
        /*)
            if [ -f "$raw" ]; then
                echo "$raw"
            else
                echo "[notify] sound file missing: $raw" >&2
            fi
            ;;
        "~"|"~/"*)
            local expanded="${raw/#\~/$HOME}"
            if [ -f "$expanded" ]; then
                echo "$expanded"
            else
                echo "[notify] sound file missing: $raw" >&2
            fi
            ;;
        */*)
            echo "[notify] sound must be a bare name or absolute path: $raw" >&2
            ;;
        *)
            local candidate="/System/Library/Sounds/${raw}.aiff"
            if [ -f "$candidate" ]; then
                echo "$candidate"
            else
                echo "[notify] unknown built-in sound: $raw" >&2
            fi
            ;;
    esac
}

# ── Quiet hours (spec 0002) ──────────────────────────────────────────────────
# Sets four globals based on the scheduled `quiet_hours` window and the
# ad-hoc pause sidecar at ${HOME}/.claude/agent-state.quiet-until:
#
#   QUIET_NOW       true iff either gate says we're quiet right now.
#   SUPPRESS_SOUND  true iff QUIET_NOW and "sound"  is in quiet_hours_silences.
#   SUPPRESS_VOICE  true iff QUIET_NOW and "voice"  is in quiet_hours_silences.
#   SUPPRESS_BANNER true iff QUIET_NOW and "banner" is in quiet_hours_silences.
#
# Default silences = all three channels, so a quiet-hours window with no
# explicit list silences everything. A user who wants the banner but not
# the chime drops "banner" from the list. Times are compared as
# minutes-of-day in local time; DST transitions just let one minute slip
# through, which is fine — the next minute is back inside the window.
_compute_quiet_state() {
    QUIET_NOW=false
    SUPPRESS_SOUND=false
    SUPPRESS_VOICE=false
    SUPPRESS_BANNER=false

    # Single jq pass — quiet_hours on line 1, the silences CSV on
    # line 2. Cuts the hook's jq invocations down by one; small per call
    # but it adds up over a day of notifications.
    local qh="" silences="sound,voice,banner"
    if [ -f "$_CAB_CONFIG" ]; then
        {
            IFS= read -r qh || true
            IFS= read -r silences || true
        } < <(/usr/bin/jq -r '
            (.quiet_hours // "" | tostring),
            (if (.quiet_hours_silences // null) | type == "array"
             then [.quiet_hours_silences[] | strings] | join(",")
             else "sound,voice,banner" end)
        ' "$_CAB_CONFIG" 2>/dev/null)
        silences="${silences:-sound,voice,banner}"
    fi

    # Scheduled window.
    if [[ "$qh" =~ ^(2[0-3]|[01][0-9]):([0-5][0-9])-(2[0-3]|[01][0-9]):([0-5][0-9])$ ]]; then
        local start_hm end_hm now_hm
        start_hm=$((10#${BASH_REMATCH[1]}${BASH_REMATCH[2]}))
        end_hm=$((10#${BASH_REMATCH[3]}${BASH_REMATCH[4]}))
        # start == end → zero-length window, treat as "never quiet"
        # rather than "always quiet" (the safer default if the user
        # typo'd matching values).
        if [ "$start_hm" -ne "$end_hm" ]; then
            now_hm=$((10#$(date +%H%M)))
            if [ "$start_hm" -lt "$end_hm" ]; then
                if [ "$now_hm" -ge "$start_hm" ] && [ "$now_hm" -lt "$end_hm" ]; then
                    QUIET_NOW=true
                fi
            else
                # Wraps midnight: 23:00-09:00 → active when ≥ 23:00 OR < 09:00.
                if [ "$now_hm" -ge "$start_hm" ] || [ "$now_hm" -lt "$end_hm" ]; then
                    QUIET_NOW=true
                fi
            fi
        fi
    elif [ -n "$qh" ]; then
        echo "[notify] ignoring malformed quiet_hours: $qh" >&2
    fi

    # Ad-hoc pause sidecar — overrides "off" but doesn't override a
    # scheduled active window (both end up with QUIET_NOW=true).
    local sidecar="${HOME}/.claude/agent-state.quiet-until"
    if [ -r "$sidecar" ]; then
        local until until_epoch
        until=$(/usr/bin/tr -d '[:space:]' < "$sidecar" 2>/dev/null)
        if [ -n "$until" ]; then
            until_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%S" "$until" +%s 2>/dev/null || echo 0)
            [ "$until_epoch" -gt "$(date +%s)" ] && QUIET_NOW=true
        fi
    fi

    # Bypass sidecar — inverse of the pause channel: a future timestamp
    # forces QUIET_NOW=false so notifications fire even inside the
    # scheduled window. Pause wins over bypass (the user has more
    # recently or more explicitly asked for quiet), matching the Python
    # quiet_status() precedence rules.
    local bypass_sidecar="${HOME}/.claude/agent-state.quiet-bypass-until"
    if [ -r "$bypass_sidecar" ] && [ "$QUIET_NOW" = "true" ]; then
        # Re-check the pause to make sure bypass doesn't also unsilence a
        # user-paused window — the pause path above just set QUIET_NOW
        # without recording *why*. Cheaper to recompute the pause flag
        # locally than to thread a third variable through the function.
        local paused=false
        if [ -r "$sidecar" ]; then
            local p_until p_until_epoch
            p_until=$(/usr/bin/tr -d '[:space:]' < "$sidecar" 2>/dev/null)
            if [ -n "$p_until" ]; then
                p_until_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%S" "$p_until" +%s 2>/dev/null || echo 0)
                [ "$p_until_epoch" -gt "$(date +%s)" ] && paused=true
            fi
        fi
        if [ "$paused" = "false" ]; then
            local b_until b_until_epoch
            b_until=$(/usr/bin/tr -d '[:space:]' < "$bypass_sidecar" 2>/dev/null)
            if [ -n "$b_until" ]; then
                b_until_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%S" "$b_until" +%s 2>/dev/null || echo 0)
                [ "$b_until_epoch" -gt "$(date +%s)" ] && QUIET_NOW=false
            fi
        fi
    fi

    [ "$QUIET_NOW" = "false" ] && return

    case ",$silences," in
        *,sound,*)  SUPPRESS_SOUND=true ;;
    esac
    case ",$silences," in
        *,voice,*)  SUPPRESS_VOICE=true ;;
    esac
    case ",$silences," in
        *,banner,*) SUPPRESS_BANNER=true ;;
    esac
}
