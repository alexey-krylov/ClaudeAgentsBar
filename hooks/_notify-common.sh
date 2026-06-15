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
#   * `_editor_app_for_scheme` — editor_url_scheme → the `.app` that
#                                registers it (empty for unknown schemes).
#   * `_cfg_number`            — like _cfg_int but keeps fractions (for
#                                editor_focus_settle_sec).
#   * `_multi_workspace_enabled` — effective multi-workspace mode
#                                ("true"/"false"); sidecar toggle wins
#                                over the config knob.
#   * `_raise_open_cmd`        — build the terminal-notifier `-execute`
#                                command that raises the window matching a
#                                session's cwd, then opens its deeplink.
#   * `_CAB_HOOK_DIR`          — real (symlink-resolved) hooks directory,
#                                used to locate the sibling helper scripts.
#   * `_compute_quiet_state`   — sets `QUIET_NOW` plus
#                                `SUPPRESS_SOUND` / `SUPPRESS_VOICE` /
#                                `SUPPRESS_BANNER` from the scheduled
#                                window + ad-hoc sidecar + silences list.
#
# Sourced via the symlink-following resolver at the top of each hook;
# this file is never executed directly. Callers run with `set -u`, so
# every global we touch is initialised before first read.

# ── Hooks directory ──────────────────────────────────────────────────────────
# This file is sourced through the already-symlink-resolved path the hook
# computed (`${__HOOK_DIR}/_notify-common.sh`), so `BASH_SOURCE[0]` points
# at the real file in the repo. Sibling helpers (raise-and-open.sh) live
# next to it; resolve the directory once here.
_CAB_HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P)"

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

# Like _cfg_int but keeps the fractional part — for sub-second knobs such
# as editor_focus_settle_sec (e.g. 0.1). Falls back to default on missing
# key / non-number / missing file.
_cfg_number() {
    local key="$1" default="$2"
    [ -f "$_CAB_CONFIG" ] || { echo "$default"; return; }
    local val
    val=$(/usr/bin/jq -r \
        --arg k "$key" \
        'if .[$k] | type == "number" then (.[$k] | tostring) else empty end' \
        "$_CAB_CONFIG" 2>/dev/null)
    echo "${val:-$default}"
}

# Effective multi-workspace mode as "true"/"false". The runtime toggle
# sidecar (~/.claude/agent-state.multi-workspace.mode, "on"/"off", written
# by the Tools checkbox) wins over the multi_workspace_mode config knob —
# mirrors core.multi_workspace_enabled() in Python; keep the two in step.
_multi_workspace_enabled() {
    local sidecar="${HOME}/.claude/agent-state.multi-workspace.mode" v
    if [ -r "$sidecar" ]; then
        v=$(/usr/bin/tr -d '[:space:]' < "$sidecar" 2>/dev/null)
        case "$v" in
            on)  echo "true";  return ;;
            off) echo "false"; return ;;
        esac
    fi
    _cfg_bool "multi_workspace_mode" "true"
}

# Effective notification-audio mode as "true"/"false". The runtime toggle
# sidecar (~/.claude/agent-state.notify-audio.mode, "on"/"off", written by
# the Tools → Notifications radio pair) wins over the notify_audio config
# knob — mirrors core.notify_audio_enabled() in Python; keep the two in
# step. "false" means banner only: the caller mutes chime + say.
_notify_audio_enabled() {
    local sidecar="${HOME}/.claude/agent-state.notify-audio.mode" v
    if [ -r "$sidecar" ]; then
        v=$(/usr/bin/tr -d '[:space:]' < "$sidecar" 2>/dev/null)
        case "$v" in
            on)  echo "true";  return ;;
            off) echo "false"; return ;;
        esac
    fi
    _cfg_bool "notify_audio" "true"
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

# ── Spoken-summary extraction (spec 0005) ────────────────────────────────────
# The marker line carries two fields: `<marker>Name - Summary` (e.g.
# `*-- Чиню баг - нашёл причину*`). The marker (`-- ` by default) is the line
# *prefix*; the two fields are split on the first ` - ` (a lone hyphen padded
# with spaces). A line with no ` - ` after the marker is the legacy
# single-field form — summary only, no name.
#
#   _extract_summary       → the Summary field (text after the first ` - `,
#                            or the whole remainder when single-field). Spoken
#                            on Stop and re-spoken by the Remind click.
#   _extract_marker_name   → the Name field (text before the first ` - `, or
#                            "" when single-field). Spoken with the summary on
#                            an awaiting (PermissionRequest) prompt.
#
# Both look only at the LAST non-blank line of the assistant's latest reply
# (the closing line is the marker), strip leading/trailing markdown emphasis
# (`*`/`_`), then test the prefix literally (`index==1`, no regex,
# Unicode-safe). Empty marker, missing transcript, missing jq, or a last line
# that isn't a marker line all yield "" — a silent, non-fatal fallback.
_extract_summary() {
    local transcript="$1" marker="$2"
    [ -n "$marker" ] && [ -n "$transcript" ] && [ -f "$transcript" ] || return
    /usr/bin/jq -r 'select(.type=="assistant")
                    | .message.content[]?
                    | select(.type=="text") | .text' "$transcript" 2>/dev/null \
        | /usr/bin/awk -v m="$marker" -v d=" - " \
            'NF { last = $0 }
             END {
                 sub(/^[*_]+/, "", last)        # strip leading markdown italic/bold (*, _, **, ***)
                 sub(/[*_]+$/, "", last)        # strip trailing markers
                 if (index(last, m) == 1) {
                     rest = substr(last, length(m) + 1)
                     p = index(rest, d)         # first " - " splits Name | Summary
                     if (p > 0) rest = substr(rest, p + length(d))  # drop the Name field
                     print rest
                 }
             }' \
        | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

# Echo the Name and Summary of the latest *completed* marker turn as two lines
# (NAME then SUMMARY), or nothing when no turn carried a marker. Unlike
# _extract_summary (which inspects only the very last line of the whole reply
# stream), this scans per turn — each turn's closing non-blank line — and keeps
# the last one that is a marker line. That matters for notify-wait.sh: at a
# PermissionRequest the current turn is mid-flight and hasn't emitted its
# closing marker yet, so the useful name+summary live on the previous completed
# turn. A single-field turn yields an empty NAME. Same prefix/emphasis rules as
# _extract_summary.
_marker_fields_latest() {
    local transcript="$1" marker="$2"
    [ -n "$marker" ] && [ -n "$transcript" ] && [ -f "$transcript" ] || return
    /usr/bin/jq -rc '
        select(.type=="assistant")
        | [ .message.content[]? | select(.type=="text") | .text ]
        | join("\n") | split("\n")
        | map(select(test("\\S"))) | last // empty
    ' "$transcript" 2>/dev/null \
    | /usr/bin/awk -v m="$marker" -v d=" - " '
        { line = $0
          sub(/^[*_]+/, "", line)
          sub(/[*_]+$/, "", line)
          if (index(line, m) == 1) {
              rest = substr(line, length(m) + 1)
              p = index(rest, d)
              if (p > 0) { nm = substr(rest, 1, p - 1); sm = substr(rest, p + length(d)) }
              else       { nm = "";                     sm = rest }
              sub(/^[[:space:]]+/, "", nm); sub(/[[:space:]]+$/, "", nm)
              sub(/^[[:space:]]+/, "", sm); sub(/[[:space:]]+$/, "", sm)
              last_nm = nm; last_sm = sm; have = 1
          } }
        END { if (have) { print last_nm; print last_sm } }'
}

# Echo the FIRST and LAST spoken-summary of a session as two lines (in that
# order), or nothing when the session has none. Unlike _extract_summary (which
# only looks at the very last reply), this scans every assistant turn: for each
# it takes the turn's last non-blank line and, if that starts with the marker,
# treats it as that turn's summary. The first such line is the session's
# opening summary ("what was this about?"), the last is its current state. Only
# each turn's *closing* line is tested, so a `-- ` mid-reply can't false-match.
# first == last when the session has exactly one summary — caller de-dupes.
# Powers the Remind click, which can speak both to refresh context.
_summary_endpoints() {
    local transcript="$1" marker="$2"
    [ -n "$marker" ] && [ -n "$transcript" ] && [ -f "$transcript" ] || return
    /usr/bin/jq -rc '
        select(.type=="assistant")
        | [ .message.content[]? | select(.type=="text") | .text ]
        | join("\n") | split("\n")
        | map(select(test("\\S"))) | last // empty
    ' "$transcript" 2>/dev/null \
    | /usr/bin/awk -v m="$marker" -v d=" - " '
        { line = $0
          sub(/^[*_]+/, "", line)        # strip leading markdown italic/bold
          sub(/[*_]+$/, "", line)        # strip trailing markers
          if (index(line, m) == 1) {
              s = substr(line, length(m) + 1)
              p = index(s, d)            # drop the Name field → speak the summary
              if (p > 0) s = substr(s, p + length(d))
              sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s)
              if (!fs) { first = s; fs = 1 }
              last = s
          } }
        END { if (fs) { print first; print last } }'
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

# ── Editor deeplink → multi-window banner action ─────────────────────────────
# A banner click opens `<scheme>anthropic.claude-code/open?session=<id>`,
# which the editor delivers to whichever window is *frontmost* — not the
# one whose workspace matches the session. With several windows open that
# resumes in the wrong one. The fix: before opening the deeplink, raise
# the window holding the session's cwd. These helpers let the hooks wire
# that up through terminal-notifier's `-execute`.

# Map an editor_url_scheme to the `.app` that registers it. Mirrors
# `_EDITOR_SCHEME_APP` in claude_agents_bar/doctor.py — keep the two in
# lockstep. Unknown / custom schemes echo nothing, so the caller falls
# back to a plain deeplink open (the pre-fix behaviour).
_editor_app_for_scheme() {
    case "$1" in
        "vscode://")   echo "/Applications/Visual Studio Code.app" ;;
        "vscodium://") echo "/Applications/VSCodium.app" ;;
        "cursor://")   echo "/Applications/Cursor.app" ;;
        "windsurf://") echo "/Applications/Windsurf.app" ;;
        "positron://") echo "/Applications/Positron.app" ;;
    esac
}

# Single-quote $1 for safe embedding in the `/bin/sh -c …` command line
# terminal-notifier runs for `-execute`. Each embedded single quote
# becomes the standard '\'' sequence.
_shq() {
    local out=$1
    out=${out//\'/\'\\\'\'}
    printf "'%s'" "$out"
}

# Echo the `-execute` command that raises the window matching <cwd> for
# <app>, then opens <url>. <sid> lets the helper pick the session's last
# touched file as the focus anchor; <settle> is the post-raise pause
# before the deeplink. Invoked via `/bin/bash <helper>` rather than the
# bare path so it doesn't depend on the helper's executable bit surviving
# distribution (Homebrew bottle / zip). The result is a single shell-ready
# string; callers pass it verbatim as one `-execute` arg.
_raise_open_cmd() {
    local url="$1" cwd="$2" app="$3" sid="${4:-}" settle="${5:-}"
    local helper="${_CAB_HOOK_DIR}/raise-and-open.sh"
    printf '/bin/bash %s %s %s %s %s %s' \
        "$(_shq "$helper")" "$(_shq "$url")" "$(_shq "$cwd")" \
        "$(_shq "$app")" "$(_shq "$sid")" "$(_shq "$settle")"
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
# Default silences = sound + voice, so a quiet-hours window with no
# explicit list mutes audio but still shows the banner. A user who wants
# full silence adds "banner"; one who wants the chime lists only "voice".
# Times are compared as
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
    local qh="" silences="sound,voice"
    if [ -f "$_CAB_CONFIG" ]; then
        {
            IFS= read -r qh || true
            IFS= read -r silences || true
        } < <(/usr/bin/jq -r '
            (.quiet_hours // "" | tostring),
            (if (.quiet_hours_silences // null) | type == "array"
             then [.quiet_hours_silences[] | strings] | join(",")
             else "sound,voice" end)
        ' "$_CAB_CONFIG" 2>/dev/null)
        silences="${silences:-sound,voice}"
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

# ── Random phrase picker ─────────────────────────────────────────────────────
# Echo one random phrase for an event. Reads the JSON array at <config_key>
# (e.g. notify_phrases / notify_wait_phrases / notify_idle_phrases); when it's
# missing/empty/not-an-array, falls back to the defaults passed as the
# remaining arguments. macOS ships bash 3.2 (no `mapfile`), hence the while
# loop. Empty output only if the config array is empty AND no defaults given.
_pick_phrase() {
    local key="$1"; shift
    local phrases=() p
    while IFS= read -r p; do
        [ -n "$p" ] && phrases+=("$p")
    done < <(
        [ -f "$_CAB_CONFIG" ] && /usr/bin/jq -r \
            --arg k "$key" '.[$k] // empty | .[]?' "$_CAB_CONFIG" 2>/dev/null
    )
    if [ "${#phrases[@]}" -eq 0 ]; then
        phrases=("$@")
    fi
    [ "${#phrases[@]}" -eq 0 ] && return
    echo "${phrases[$RANDOM % ${#phrases[@]}]}"
}

# ── Spoken-segment separator ─────────────────────────────────────────────────
# Joins the spoken segments (phrase → name → summary) for say(1). The period
# gives a sentence break; `[[slnc N]]` is a say embedded command inserting N ms
# of silence, so the segments don't run together as one breath. Tune the number
# here — deliberately a hook constant, not a config knob.
_SAY_SEP=". [[slnc 100]] "

# ── Banner subtitle: "<project> / <branch>" from the session cwd ─────────────
# Spec 0009. Recomputed at banner time from the session's working dir so the
# subtitle matches the menu submenu (project = basename, branch read straight
# from .git/HEAD — worktree-aware, detached HEAD → short SHA). A couple of
# small file reads, no `git` subprocess — cheap enough for an event hook. No
# JSONL fallback: a deleted cwd yields project-only / empty, the accepted
# trade-off in spec 0009.
_git_branch_from_cwd() {
    local cwd="$1" gitmarker head_file indirection gitdir head
    [ -n "$cwd" ] || return 0
    gitmarker="$cwd/.git"
    if [ -d "$gitmarker" ]; then
        head_file="$gitmarker/HEAD"
    elif [ -f "$gitmarker" ]; then
        # Linked worktree: .git is a file "gitdir: <path>"; HEAD lives there.
        indirection=$(cat "$gitmarker" 2>/dev/null)
        case "$indirection" in
            gitdir:*) gitdir="${indirection#gitdir:}"
                      gitdir="${gitdir# }"
                      head_file="$gitdir/HEAD" ;;
            *) return 0 ;;
        esac
    else
        return 0
    fi
    head=$(cat "$head_file" 2>/dev/null) || return 0
    case "$head" in
        "ref: refs/heads/"*) printf '%s' "${head#ref: refs/heads/}" ;;
        "") return 0 ;;
        *) printf '%s' "${head:0:7}" ;;   # detached HEAD → short SHA
    esac
}

# "<project> — <icon> <branch>", or just "<project>" outside a repo, or empty
# when cwd is unknown. The icon before the branch marks the checkout kind: ⓦ
# for a linked worktree (.git is a file), ⎇ for an ordinary branch — the
# plain-text banner analogue of the submenu's worktree marker / branch glyph.
_banner_subtitle() {
    local cwd="$1" project branch icon
    [ -n "$cwd" ] || return 0
    project=$(basename "$cwd")
    branch=$(_git_branch_from_cwd "$cwd")
    if [ -z "$branch" ]; then
        printf '%s' "$project"
        return 0
    fi
    if [ -f "$cwd/.git" ]; then
        icon="ⓦ"   # linked worktree
    else
        icon="⎇"   # ordinary branch
    fi
    printf '%s — %s %s' "$project" "$icon" "$branch"
}

# ── Speech serialization lock (spec 0010) ────────────────────────────────────
# say(1) overlap is the problem: every notify hook and the Remind click spawn
# their own backgrounded `say`, so two events landing in the same second talk
# over each other and become unintelligible. There's no daemon to serialize
# through (stateless project, no IPC), so the cross-process mutex is an atomic
# `mkdir` at a fixed sidecar dir — macOS ships no flock(1). Only speech is
# serialized; the chime (afplay) and banner still fire in parallel as before.
#
#   _say_lock_acquire  Spin on mkdir until we own the lock. Returns 0 when held
#                      — the caller then speaks and MUST call _say_lock_release.
#                      Returns 1 when this utterance has waited longer than
#                      notify_say_stale_sec (default 30) — the caller drops it
#                      unspoken, because a stale spoken notification is noise.
#   _say_lock_release  Hold the lock for notify_say_gap_sec (default 1) — the
#                      inter-utterance pause, since the next waiter can't mkdir
#                      until we rmdir — then release.
#
# Staleness of the *holder* (crash recovery): the lock dir stores the holder's
# PID. A waiter steals it (rm -rf + retry) when that PID is gone. `$$` inside a
# `( ) &` subshell is the PARENT's pid (bash 3.2 quirk, and there's no BASHPID),
# so the holder records its real pid via `sh -c 'echo $PPID'` — the forked sh's
# parent IS the speaking subshell. _SAY_LOCK_CEILING is a backstop for the rare
# case the dead holder's PID got reused by an unrelated live process: a lock dir
# older than the ceiling is stolen regardless.
_SAY_LOCK_DIR="${HOME}/.claude/agent-state.say.lock"
_SAY_LOCK_CEILING=120

_say_lock_acquire() {
    local stale stale_int
    stale=$(_cfg_number "notify_say_stale_sec" "30")
    case "$stale" in ''|*[!0-9.]*) stale=30 ;; esac
    stale_int=${stale%.*}; [ -n "$stale_int" ] || stale_int=0
    SECONDS=0
    while ! mkdir "$_SAY_LOCK_DIR" 2>/dev/null; do
        # Steal an orphaned lock — holder PID gone, or dir older than the
        # crash backstop. An empty/unwritten pid file means someone just
        # grabbed it (race window before the pid write); don't steal that.
        local pid mtime now
        pid=$(cat "$_SAY_LOCK_DIR/pid" 2>/dev/null)
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            rm -rf "$_SAY_LOCK_DIR" 2>/dev/null
            continue
        fi
        mtime=$(stat -f %m "$_SAY_LOCK_DIR" 2>/dev/null || echo 0)
        now=$(date +%s)
        if [ "$mtime" -gt 0 ] && [ $((now - mtime)) -ge "$_SAY_LOCK_CEILING" ]; then
            rm -rf "$_SAY_LOCK_DIR" 2>/dev/null
            continue
        fi
        # Drop this utterance once we've waited past the staleness budget.
        [ "$SECONDS" -ge "$stale_int" ] && return 1
        sleep 0.2
    done
    printf '%s' "$(sh -c 'echo $PPID')" > "$_SAY_LOCK_DIR/pid" 2>/dev/null || true
    return 0
}

_say_lock_release() {
    local gap
    gap=$(_cfg_number "notify_say_gap_sec" "1")
    sleep "$gap" 2>/dev/null || sleep 1
    rmdir "$_SAY_LOCK_DIR" 2>/dev/null || rm -rf "$_SAY_LOCK_DIR" 2>/dev/null
}

# ── Notification emit (chime + speech + banner) ──────────────────────────────
# The shared tail of every notify hook: play the chime, speak the phrase,
# and pop the clickable terminal-notifier banner. Reads the channel state
# the caller has already computed — the SUPPRESS_* flags (quiet hours +
# audio master switch), SOUND_PATH / VOICE (resolved audio), and the
# banner-click context SCHEME / MULTI_WS / SETTLE. Everything event-specific
# (which sound, which phrase, which title, how SAY/BANNER read) is decided by
# the caller and handed in as arguments:
#
#   $1 title        banner title line
#   $2 banner_msg   banner body
#   $3 say_text     full sentence read by say(1)
#   $4 session_url  deeplink for the banner click ("" → non-clickable banner)
#   $5 sid          session id (anchors the multi-workspace window raise)
#   $6 cwd          session cwd (picks the editor window to raise)
#
# All audio/banner work is fire-and-forget (backgrounded + disowned) so the
# hook never blocks on afplay/say/terminal-notifier.
_emit_notification() {
    local title="$1" banner_msg="$2" say_text="$3" session_url="$4" sid="$5" cwd="$6"

    if [ "$SUPPRESS_SOUND" = "false" ] && [ -n "$SOUND_PATH" ]; then
        afplay "$SOUND_PATH" >/dev/null 2>&1 &
    fi
    if [ "$SUPPRESS_VOICE" = "false" ] && [ "$VOICE" != "off" ]; then
        # The 1 s lead lets the chime play before the voice starts; it stays
        # OUTSIDE the lock (per-notification, not a queued cost). Then serialize
        # through the speech lock so concurrent notifications don't talk over
        # each other; a too-long wait drops this utterance unspoken (spec 0010).
        (
            sleep 1
            _say_lock_acquire || exit 0
            if [ -n "$VOICE" ]; then
                say -v "$VOICE" "$say_text"
            else
                say "$say_text"
            fi
            _say_lock_release
        ) >/dev/null 2>&1 &
    fi
    disown 2>/dev/null || true

    [ "$SUPPRESS_BANNER" = "false" ] || return

    # terminal-notifier ignores -appIcon on recent macOS; -contentImage is
    # the side icon. The banner is clickable — with multi_workspace_mode on
    # and the session's cwd + a window-raising .app known, the click runs
    # raise-and-open.sh (via -execute) so it lands in the window matching the
    # workspace rather than whatever is frontmost; otherwise it falls back to
    # a plain -open of the deeplink.
    local icon="${HOME}/.claude/hooks/assets/claude-icon.png"
    # Subtitle is the session's project / branch (spec 0009), computed from cwd;
    # omitted when cwd is unknown so the banner doesn't carry a blank line.
    local subtitle notifier_args
    subtitle=$(_banner_subtitle "$cwd")
    notifier_args=(-title "$title" -message "$banner_msg")
    [ -n "$subtitle" ] && notifier_args+=(-subtitle "$subtitle")
    [ -f "$icon" ] && notifier_args+=(-contentImage "$icon")
    if [ -n "$session_url" ]; then
        # Route the click through raise-and-open.sh (via -execute) instead of
        # a bare -open, so it records the click (ack) before resuming the
        # session — the same as the menu-row path. Without this the session
        # stays 🟢 FRESH until fresh_sec elapses even though the user already
        # opened it from the banner. With multi_workspace_mode on and a known
        # cwd + window-raising .app, pass them too so the click lands in the
        # matching window; otherwise pass blanks and the helper just acks +
        # opens (single-window: no raise, matching the prior -open behaviour).
        local rc_cwd="" rc_app="" editor_app
        editor_app=$(_editor_app_for_scheme "$SCHEME")
        if [ "$MULTI_WS" = "true" ] && [ -n "$cwd" ] && [ -n "$editor_app" ] \
                && [ -d "$cwd" ] && [ -d "$editor_app" ]; then
            rc_cwd="$cwd"; rc_app="$editor_app"
        fi
        notifier_args+=(-execute "$(_raise_open_cmd "$session_url" "$rc_cwd" "$rc_app" "$sid" "$SETTLE")")
    fi
    if command -v terminal-notifier >/dev/null 2>&1; then
        terminal-notifier "${notifier_args[@]}" >/dev/null 2>&1 &
        disown 2>/dev/null || true
    fi
}
