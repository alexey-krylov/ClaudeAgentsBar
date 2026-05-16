#!/bin/bash
#
# ClaudeAgentsBar completion-notification hook.
#
# Fires on every Stop event. Plays a chime, speaks a random phrase, and pops
# a terminal-notifier banner so the user knows a session finished without
# having to glance at the menu bar.
#
# Requires: terminal-notifier  (brew install terminal-notifier)
# Optional: jq  (for config/transcript parsing); afplay/say are macOS builtins.
#
# Config keys read from the ClaudeAgentsBar config.json (all optional):
#   notify_on_stop         bool    true     — false to silence all notifications
#   notify_threshold_sec   int     30       — skip sessions whose last user
#                                             turn was less than this many
#                                             seconds ago (avoids noise from
#                                             quick one-liner exchanges)
#   notify_phrases         array   [...]    — phrases spoken aloud and shown in
#                                             the banner; one is picked at random
#   editor_url_scheme      string  "vscode://" — used to build the deeplink;
#                                             mirrors the same key the plugin
#                                             uses for row clicks

set -u

# ── Config path (mirrors XDG logic in claude-agents.5s.py) ──────────────────
if [ -n "${CLAUDE_AGENTS_BAR_CONFIG:-}" ]; then
    _CAB_CONFIG="$CLAUDE_AGENTS_BAR_CONFIG"
else
    _XDG="${XDG_CONFIG_HOME:-$HOME/.config}"
    _CAB_CONFIG="${_XDG}/claude-agents-bar/config.json"
fi

# ── Config readers (all gracefully no-op when jq or the file is absent) ─────
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

# Emits one phrase per line; caller collects into an array.
_cfg_phrases() {
    [ -f "$_CAB_CONFIG" ] || return
    /usr/bin/jq -r '.notify_phrases // empty | .[]?' "$_CAB_CONFIG" 2>/dev/null
}

# ── Read config ──────────────────────────────────────────────────────────────
NOTIFY_ON=$(_cfg_bool  "notify_on_stop"        "true")
[ "$NOTIFY_ON" = "false" ] && exit 0

THRESHOLD=$(_cfg_int   "notify_threshold_sec"  "30")
SCHEME=$(_cfg_string   "editor_url_scheme"     "vscode://")

# ── Parse hook payload ───────────────────────────────────────────────────────
INPUT=$(cat)
TRANSCRIPT=$(/usr/bin/jq -r '.transcript_path // empty' <<<"$INPUT" 2>/dev/null)
SID=$(/usr/bin/jq -r '.session_id // empty'    <<<"$INPUT" 2>/dev/null)

SESSION_URL=""
[ -n "$SID" ] && SESSION_URL="${SCHEME}anthropic.claude-code/open?session=${SID}"

# ── Threshold: skip short turns ──────────────────────────────────────────────
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    LAST_USER_TS=$(tail -r "$TRANSCRIPT" 2>/dev/null \
        | /usr/bin/jq -r \
            'select(.type=="user")
             | select((.message.content | type) == "array")
             | select(.message.content[0].type? == "text")
             | .timestamp // empty' 2>/dev/null \
        | head -n 1)
    if [ -n "$LAST_USER_TS" ]; then
        TS_TRIM="${LAST_USER_TS%%.*}"
        LAST_EPOCH=$(date -j -u -f "%Y-%m-%dT%H:%M:%S" "$TS_TRIM" +%s 2>/dev/null)
        if [ -n "$LAST_EPOCH" ]; then
            ELAPSED=$(( $(date +%s) - LAST_EPOCH ))
            [ "$ELAPSED" -lt "$THRESHOLD" ] && exit 0
        fi
    fi
fi

# ── Extract task title from transcript ───────────────────────────────────────
TASK=""
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    TASK=$(tail -r "$TRANSCRIPT" 2>/dev/null \
        | /usr/bin/jq -r 'select(.type=="ai-title") | .aiTitle // empty' 2>/dev/null \
        | head -n 1)
    if [ -z "$TASK" ]; then
        TASK=$(tail -r "$TRANSCRIPT" 2>/dev/null \
            | /usr/bin/jq -r \
                'select(.type=="user")
                 | select((.message.content | type) == "array")
                 | select(.message.content[0].type? == "text")
                 | .message.content[0].text' 2>/dev/null \
            | head -n 1)
    fi
fi
TITLE=$(printf '%s' "$TASK" | tr '\n' ' ' | head -c 240)
TITLE="${TITLE:-Done}"

# ── Pick a random phrase ─────────────────────────────────────────────────────
# Bash 3.2 (system /bin/bash on macOS) lacks mapfile, so we use a while loop.
PHRASES=()
while IFS= read -r _phrase; do
    [ -n "$_phrase" ] && PHRASES+=("$_phrase")
done < <(_cfg_phrases)

if [ "${#PHRASES[@]}" -eq 0 ]; then
    PHRASES=("Check it" "Done" "Ready for review" "Your turn")
fi

PHRASE="${PHRASES[$RANDOM % ${#PHRASES[@]}]}"

# ── Sound + speech (fire-and-forget, never block the hook) ───────────────────
afplay /System/Library/Sounds/Hero.aiff >/dev/null 2>&1 &
(sleep 1 && say "$PHRASE") >/dev/null 2>&1 &
disown 2>/dev/null || true

# ── Banner notification ───────────────────────────────────────────────────────
# terminal-notifier blocks macOS impersonation of Apple-signed bundles and
# ignores -appIcon on recent macOS versions. We use -contentImage for the
# side icon. -open makes the banner clickable — clicking jumps to the
# right session in the editor.
ICON="${HOME}/.claude/hooks/assets/claude-icon.png"
NOTIFIER_ARGS=(-title "$TITLE" -subtitle "Claude Code" -message "$PHRASE")
[ -f "$ICON" ]         && NOTIFIER_ARGS+=(-contentImage "$ICON")
[ -n "$SESSION_URL" ]  && NOTIFIER_ARGS+=(-open "$SESSION_URL")

if command -v terminal-notifier >/dev/null 2>&1; then
    terminal-notifier "${NOTIFIER_ARGS[@]}" >/dev/null 2>&1 &
    disown 2>/dev/null || true
fi
