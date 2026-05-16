#!/bin/bash
#
# ClaudeAgentsBar permission-prompt notification hook.
#
# Fires on every PermissionRequest event (and optionally on Notification —
# see hooks/settings-hooks.json). Plays a short chime, speaks a random
# phrase, and pops a terminal-notifier banner so the user knows Claude is
# blocked on a tool-approval dialog without having to glance at the menu
# bar.
#
# Requires: terminal-notifier  (brew install terminal-notifier)
# Optional: jq  (for config/payload parsing); afplay/say are macOS builtins.
#
# Config keys read from the ClaudeAgentsBar config.json (all optional):
#   notify_on_wait         bool    true     — false to silence permission
#                                             notifications
#   notify_wait_phrases    array   [...]    — phrases spoken aloud and shown
#                                             in the banner; one chosen at
#                                             random per event
#   editor_url_scheme      string  "vscode://" — used to build the deeplink
#                                             so the banner click jumps
#                                             straight into the waiting
#                                             session; mirrors the same key
#                                             the plugin uses for row clicks

set -u

# ── Config path (mirrors XDG logic in claude_agents_bar/core.py) ────────────
if [ -n "${CLAUDE_AGENTS_BAR_CONFIG:-}" ]; then
    _CAB_CONFIG="$CLAUDE_AGENTS_BAR_CONFIG"
else
    _XDG="${XDG_CONFIG_HOME:-$HOME/.config}"
    _CAB_CONFIG="${_XDG}/claude-agents-bar/config.json"
fi

# ── Config readers (graceful no-op when jq or the file is absent) ───────────
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
    /usr/bin/jq -r '.notify_wait_phrases // empty | .[]?' "$_CAB_CONFIG" 2>/dev/null
}

# ── Read config ──────────────────────────────────────────────────────────────
NOTIFY_ON=$(_cfg_bool  "notify_on_wait"        "true")
[ "$NOTIFY_ON" = "false" ] && exit 0

SCHEME=$(_cfg_string   "editor_url_scheme"     "vscode://")

# ── Parse hook payload ───────────────────────────────────────────────────────
INPUT=$(cat)
SID=$(/usr/bin/jq -r '.session_id // empty' <<<"$INPUT" 2>/dev/null)

SESSION_URL=""
[ -n "$SID" ] && SESSION_URL="${SCHEME}anthropic.claude-code/open?session=${SID}"

# ── Pick a random phrase ─────────────────────────────────────────────────────
# Bash 3.2 (system /bin/bash on macOS) lacks mapfile, so we use a while loop.
PHRASES=()
while IFS= read -r _phrase; do
    [ -n "$_phrase" ] && PHRASES+=("$_phrase")
done < <(_cfg_phrases)

if [ "${#PHRASES[@]}" -eq 0 ]; then
    PHRASES=("Need instructions" "Awaiting input" "Decision needed" "I'm blocked")
fi

PHRASE="${PHRASES[$RANDOM % ${#PHRASES[@]}]}"

# ── Sound + speech (fire-and-forget, never block the hook) ───────────────────
# Funk.aiff is shorter and softer than Hero.aiff (used on Stop) — different
# semantics: "needs your attention" vs "task complete".
afplay /System/Library/Sounds/Funk.aiff >/dev/null 2>&1 &
(sleep 1 && say "$PHRASE") >/dev/null 2>&1 &
disown 2>/dev/null || true

# ── Banner notification ──────────────────────────────────────────────────────
# Click jumps straight to the session — the user almost always wants to act
# on the prompt, not just acknowledge it.
ICON="${HOME}/.claude/hooks/assets/claude-icon.png"
NOTIFIER_ARGS=(-title "Claude awaiting input" -subtitle "Claude Code" -message "$PHRASE")
[ -f "$ICON" ]         && NOTIFIER_ARGS+=(-contentImage "$ICON")
[ -n "$SESSION_URL" ]  && NOTIFIER_ARGS+=(-open "$SESSION_URL")

if command -v terminal-notifier >/dev/null 2>&1; then
    terminal-notifier "${NOTIFIER_ARGS[@]}" >/dev/null 2>&1 &
    disown 2>/dev/null || true
fi
