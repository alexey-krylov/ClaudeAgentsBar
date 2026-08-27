#!/bin/bash
#
# Row action: open a **terminal** Claude Code session where it actually lives.
#
# Wired up from claude-agents.5s.py — every row whose entrypoint is in
# core.TERMINAL_ENTRYPOINTS runs:
#
#     open-terminal-session.sh <session-id> [cwd] [terminal-app]
#
# <terminal-app> is the `terminal_app` config knob: "auto" (default; iTerm
# when installed, else macOS Terminal), "Terminal", or "iTerm".
#
# Why a separate action instead of the editor deeplink used by
# open-session.sh: a terminal session is already running in a tab somewhere.
# Firing `<scheme>anthropic.claude-code/open?session=<id>` at it would resume
# the same transcript in a *second*, parallel session inside the editor —
# two live sessions writing one transcript, which is exactly what the user
# didn't ask for. So we go to the running one instead, in this order:
#
#   1. The tab that owns the process — found by matching the process's tty
#      against the tty of every open tab (Terminal.app / iTerm2 both expose
#      it over AppleScript). This is the only branch that's a genuine
#      "switch to it"; the rest are fallbacks.
#   2. `tmux attach` — when the session registry says it runs inside tmux.
#      Its tty belongs to the tmux server, so no window owns it directly.
#   3. `screen -r` — same story for GNU screen, detected via the parent
#      process.
#   4. `claude --resume <id>` in a fresh window — the session is dead, or
#      detached with nothing to raise. A new session on an old transcript,
#      which is correct once nothing is running.
#
# The process is located through the session registry Claude Code 2.1.228+
# maintains: ~/.claude/sessions/<pid>.json, carrying sessionId, pid, cwd and
# (inside tmux) the tmux session name. No registry / no jq → straight to
# step 4, which needs neither.
#
# Note: driving Terminal.app or iTerm2 over AppleScript needs Automation
# permission. macOS asks once, on the first click; denying it leaves the
# click doing nothing (see docs/troubleshooting.md).

set -u

SID="${1:-}"
CWD="${2:-}"
APP_PREF="${3:-auto}"

if [ -z "$SID" ]; then
    # Misuse — exit silently rather than popping a dialog on a stray click.
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd -P)"

# Record the click first, exactly like open-session.sh: the plugin reads it
# to move the row 🟢 FRESH → 🔵 ACKNOWLEDGED on the next tick. Done before
# the open so a slow AppleScript round-trip can't delay the colour change.
/bin/bash "${SCRIPT_DIR}/../../hooks/record-click.sh" "$SID"

# --- which terminal do we drive? ------------------------------------------ #

case "$APP_PREF" in
    Terminal|iTerm) TERM_APP="$APP_PREF" ;;
    *)
        if [ -d "/Applications/iTerm.app" ]; then
            TERM_APP="iTerm"
        else
            TERM_APP="Terminal"
        fi
        ;;
esac

# --- locate the running process ------------------------------------------- #

PID=""
TMUX_NAME=""
REGISTRY="${HOME}/.claude/sessions"

if command -v jq >/dev/null 2>&1 && [ -d "$REGISTRY" ]; then
    for f in "$REGISTRY"/*.json; do
        [ -f "$f" ] || continue
        found=$(jq -r --arg sid "$SID" \
            'select(.sessionId == $sid) | "\(.pid // "")\t\(.tmux // "")"' \
            "$f" 2>/dev/null) || continue
        [ -n "$found" ] || continue
        PID="${found%%$'\t'*}"
        TMUX_NAME="${found#*$'\t'}"
        break
    done
fi

# A registry entry outlives the process it describes (the file is cleaned up
# lazily), so liveness is checked separately.
if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then
    PID=""
    TMUX_NAME=""
fi

# --- helpers -------------------------------------------------------------- #

# raise_tab <app> <tty>  — bring the tab owning <tty> to the front.
# Prints "ok" when a tab matched, "miss" otherwise. Arguments go through
# argv rather than string interpolation so a path can't break the script.
raise_tab() {
    local app="$1" tty_path="$2" result
    if [ "$app" = "iTerm" ]; then
        result=$(/usr/bin/osascript - "$tty_path" <<'APPLESCRIPT' 2>/dev/null
on run argv
    set target_tty to item 1 of argv
    tell application "iTerm"
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if tty of s is target_tty then
                        select w
                        select t
                        select s
                        activate
                        return "ok"
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    return "miss"
end run
APPLESCRIPT
        )
    else
        result=$(/usr/bin/osascript - "$tty_path" <<'APPLESCRIPT' 2>/dev/null
on run argv
    set target_tty to item 1 of argv
    tell application "Terminal"
        repeat with w in windows
            repeat with t in tabs of w
                if tty of t is target_tty then
                    set selected of t to true
                    set index of w to 1
                    activate
                    return "ok"
                end if
            end repeat
        end repeat
    end tell
    return "miss"
end run
APPLESCRIPT
        )
    fi
    [ "$result" = "ok" ]
}

# run_in_new_window <app> <shell-command>
run_in_new_window() {
    local app="$1" cmd="$2"
    if [ "$app" = "iTerm" ]; then
        /usr/bin/osascript - "$cmd" <<'APPLESCRIPT' >/dev/null 2>&1
on run argv
    set the_cmd to item 1 of argv
    tell application "iTerm"
        activate
        set new_window to (create window with default profile)
        tell current session of new_window to write text the_cmd
    end tell
end run
APPLESCRIPT
    else
        /usr/bin/osascript - "$cmd" <<'APPLESCRIPT' >/dev/null 2>&1
on run argv
    set the_cmd to item 1 of argv
    tell application "Terminal"
        activate
        do script the_cmd
    end tell
end run
APPLESCRIPT
    fi
}

# --- 1. the tab that owns the process ------------------------------------- #

opened=0
if [ -n "$PID" ]; then
    TTY=$(ps -o tty= -p "$PID" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$TTY" ] && [ "$TTY" != "??" ]; then
        if raise_tab "$TERM_APP" "/dev/$TTY"; then
            opened=1
        fi
    fi
fi

# --- 2. tmux -------------------------------------------------------------- #

# Only plain session names are accepted: the value is interpolated into a
# shell command, and Claude Code itself only ever writes a bare name here.
if [ "$opened" -eq 0 ] && [ -n "$TMUX_NAME" ] &&
   printf '%s' "$TMUX_NAME" | grep -Eq '^[A-Za-z0-9_.-]+$'; then
    run_in_new_window "$TERM_APP" "$(printf 'tmux attach -t %q' "$TMUX_NAME")"
    opened=1
fi

# --- 3. GNU screen -------------------------------------------------------- #

if [ "$opened" -eq 0 ] && [ -n "$PID" ]; then
    PPID_OF_SESSION=$(ps -o ppid= -p "$PID" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$PPID_OF_SESSION" ]; then
        PARENT_CMD=$(ps -o comm= -p "$PPID_OF_SESSION" 2>/dev/null)
        case "$PARENT_CMD" in
            *SCREEN*|*screen*)
                run_in_new_window "$TERM_APP" \
                    "$(printf 'screen -r %q' "$PPID_OF_SESSION")"
                opened=1
                ;;
        esac
    fi
fi

# --- 4. resume in a fresh window ------------------------------------------ #

if [ "$opened" -eq 0 ]; then
    if [ -n "$CWD" ] && [ -d "$CWD" ]; then
        CMD=$(printf 'cd %q && claude --resume %q' "$CWD" "$SID")
    else
        CMD=$(printf 'claude --resume %q' "$SID")
    fi
    run_in_new_window "$TERM_APP" "$CMD"
fi

# Nudge SwiftBar so the ack colour change shows up immediately instead of
# waiting for the next 5 s tick.
/usr/bin/open "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

exit 0
