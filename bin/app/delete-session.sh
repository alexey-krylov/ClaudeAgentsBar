#!/bin/bash
#
# Submenu action: delete one Claude Code session after user confirmation.
#
# Wired up from claude-agents.5s.py — each session row's "Delete session…"
# submenu item runs:
#
#     delete-session.sh <session-id>
#
# We confirm via a native macOS dialog (so a stray click in the menu can't
# nuke a transcript) and then remove every file Claude Code keeps for the
# session:
#
#     ~/.claude/projects/<slug>/<session-id>.jsonl   ← chat transcript
#     ~/.claude/projects/<slug>/<session-id>/        ← tool-results dir
#     row matching <session-id> in agent-state.tsv   ← live state index
#
# The Claude Code VSCode extension watches the transcript files with an
# fs watcher, so the deletion shows up in its sidebar automatically. After
# the cleanup we ping SwiftBar to refresh — otherwise the row would linger
# in the menu until the next 5 s tick.
#
# Security: every value that originates outside this script (the session
# id passed in by SwiftBar; the on-disk ``aiTitle`` written by an LLM) is
# treated as untrusted. The session id is validated against the UUID
# shape before we touch anything, and the AppleScript dialogs are invoked
# via ``osascript /dev/stdin "$arg1" "$arg2" …`` so dynamic values arrive
# as AppleScript string values rather than being spliced into the script
# source. The TSV row is removed by awk on field equality, not by a
# regex match on the line prefix.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd -P)"
PLUGIN="${HERE}/../../claude-agents.5s.py"

# Pull localized dialog strings (MSG_DIALOG_*) from the plugin so we don't
# duplicate the string tables here. The plugin uses ``shlex.quote`` on every
# emitted value, so this ``eval`` only sees safely-quoted shell literals.
eval "$(/usr/bin/python3 "$PLUGIN" --print-strings 2>/dev/null || true)"
: "${MSG_DIALOG_NO_SID_TITLE:=ClaudeAgentsBar}"
: "${MSG_DIALOG_NO_SID_BODY:=No session id passed to delete-session.sh.}"
: "${MSG_DIALOG_NOT_FOUND_TITLE:=Session not found}"
: "${MSG_DIALOG_NOT_FOUND_BODY:=No transcript for session {sid}}"
: "${MSG_DIALOG_DELETE_TITLE:=Delete this Claude Code session?}"
: "${MSG_DIALOG_DELETE_BODY:={title}

{transcript_label}
  {transcript_path}{artifacts_section}

Close this session's tab in your IDE first — typing into a deleted session creates a new one.}"
: "${MSG_DIALOG_DELETE_LABEL_TRANSCRIPT:=Transcript:}"
: "${MSG_DIALOG_DELETE_LABEL_ARTIFACTS:=Tool artifacts:}"
: "${MSG_DIALOG_DELETE_CONFIRM:=Delete}"
: "${MSG_DIALOG_DELETE_CANCEL:=Cancel}"

ICON_PATH="/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/TrashIcon.icns"

# Display an AppleScript ``alert`` with values passed as argv so embedded
# quotes/newlines/AppleScript metacharacters in the message are inert.
# We use ``osascript -e ... -e ...`` with ``-- argv`` rather than feeding
# the script on stdin: ``osascript /dev/stdin`` does not accept heredoc
# input on macOS, raising ``I/O error (bummers)`` at run time.
show_alert() {
    local title="$1" body="$2" style="${3:-informational}"
    /usr/bin/osascript \
        -e 'on run argv' \
        -e 'set theTitle to item 1 of argv' \
        -e 'set theBody to item 2 of argv' \
        -e 'set theStyle to item 3 of argv' \
        -e 'if theStyle is "critical" then' \
        -e '  display alert theTitle message theBody as critical' \
        -e 'else' \
        -e '  display alert theTitle message theBody' \
        -e 'end if' \
        -e 'end run' \
        -- "$title" "$body" "$style" >/dev/null 2>&1 || true
}

# Confirm dialog with two buttons. Exits 0 if the user clicked the confirm
# button, non-zero otherwise (cancel, dismiss, AppleScript error). All
# dynamic strings travel as argv values, never as AppleScript source.
confirm_delete() {
    local message="$1" body="$2" icon="$3" cancel_label="$4" confirm_label="$5"
    /usr/bin/osascript \
        -e 'on run argv' \
        -e 'set theMessage to item 1 of argv' \
        -e 'set theBody to item 2 of argv' \
        -e 'set theIcon to item 3 of argv' \
        -e 'set theCancel to item 4 of argv' \
        -e 'set theConfirm to item 5 of argv' \
        -e 'try' \
        -e '  display dialog theMessage & return & return & theBody with icon alias POSIX file theIcon buttons {theCancel, theConfirm} default button theCancel cancel button theCancel' \
        -e '  return 0' \
        -e 'on error number -128' \
        -e '  error number 1' \
        -e 'end try' \
        -e 'end run' \
        -- "$message" "$body" "$icon" "$cancel_label" "$confirm_label" >/dev/null 2>&1
}

SID="${1:-}"
if [ -z "$SID" ]; then
    show_alert "$MSG_DIALOG_NO_SID_TITLE" "$MSG_DIALOG_NO_SID_BODY" critical
    exit 1
fi

# Defence-in-depth: refuse anything outside the safe session-id alphabet
# (mirrors ``_SESSION_ID_RE`` in claude-agents.5s.py). The plugin already
# filters unsafe session ids out of the menu, so the only way an invalid
# value reaches this script is through a manual invocation — in which
# case we want to fail loudly rather than splice the value into find or
# awk. Done in pure bash to avoid forking a Python interpreter just for
# a regex check on a hot menu-click path.
case "$SID" in
    "" | *[!A-Za-z0-9_-]* )
        show_alert "$MSG_DIALOG_NO_SID_TITLE" "Invalid session id" critical
        exit 1
        ;;
esac
# Enforce the length cap separately — the pattern above can't express
# "≤ 64 chars" without exploding into alternations.
if [ "${#SID}" -gt 64 ]; then
    show_alert "$MSG_DIALOG_NO_SID_TITLE" "Invalid session id" critical
    exit 1
fi

CLAUDE_DIR="${HOME}/.claude"
PROJECTS_DIR="${CLAUDE_DIR}/projects"
STATE_FILE="${CLAUDE_DIR}/agent-state.tsv"
BOOKMARKS_FILE="${CLAUDE_DIR}/agent-state.bookmarks"

# Sessions live one level deep: ~/.claude/projects/<slug>/<sid>.jsonl.
# Bounded ``find`` with maxdepth 2 keeps it cheap and avoids scanning into
# any tool-result subdirectories. ``$SID`` is UUID-shaped per the check
# above, so it can't smuggle find predicates.
JSONL_PATH=""
while IFS= read -r candidate; do
    JSONL_PATH="$candidate"
    break
done < <(find "$PROJECTS_DIR" -maxdepth 2 -name "${SID}.jsonl" -type f 2>/dev/null)

if [ -z "$JSONL_PATH" ]; then
    NOT_FOUND_BODY="${MSG_DIALOG_NOT_FOUND_BODY//\{sid\}/${SID}}"
    show_alert "$MSG_DIALOG_NOT_FOUND_TITLE" "$NOT_FOUND_BODY"
    exit 0
fi
TOOL_RESULTS_DIR="${JSONL_PATH%.jsonl}"

# Show the AI-generated title in the confirm dialog so the user can tell
# *which* session they're about to delete. Scan only the first 200 lines —
# the ai-title event is emitted right after the first turn, anything deeper
# in the file just slows us down.
#
# Security: ``aiTitle`` is LLM-written and influenced by anything Claude
# read during the session (file contents, web fetches, tool outputs). A
# prompt injection in that content can drive the LLM to emit AppleScript
# metacharacters in the title — quotes, escapes, newlines. By the time
# the value reaches the dialog it's an argv element, not template source,
# so those characters are inert. The Python below ignores titles that
# don't parse as a string.
TITLE="$(/usr/bin/python3 - "$JSONL_PATH" <<'PYEOF' 2>/dev/null || true
import json, sys

with open(sys.argv[1], "rb") as f:
    for i, line in enumerate(f):
        if i > 200:
            break
        if b'"type":"ai-title"' not in line:
            continue
        try:
            value = json.loads(line).get("aiTitle", "")
        except Exception:
            continue
        if isinstance(value, str):
            print(value.strip())
        break
PYEOF
)"
[ -z "$TITLE" ] && TITLE="$SID"

# Collapse the user's home prefix to ``~`` so the dialog stays readable
# regardless of how long the actual /Users/<name>/… prefix is. Done via
# a POSIX-safe ``case`` rather than ``${var/#…}`` so we don't depend on
# bash's pattern substitution quirks with slashes inside $HOME.
TRANSCRIPT_PATH="$JSONL_PATH"
case "$JSONL_PATH" in
    "$HOME"/*) TRANSCRIPT_PATH="~${JSONL_PATH#$HOME}" ;;
esac

# The tool-results subdir exists only if the session actually invoked any
# tools — sessions that never did would otherwise show an empty-looking
# section. Build the artifacts block only when the dir is on disk; the
# locale body inlines this via the {artifacts_section} placeholder, which
# expands to "" when no artifacts are present.
ARTIFACTS_SECTION=""
if [ -d "$TOOL_RESULTS_DIR" ]; then
    ARTIFACTS_PATH="${TOOL_RESULTS_DIR}/"
    case "$TOOL_RESULTS_DIR" in
        "$HOME"/*) ARTIFACTS_PATH="~${TOOL_RESULTS_DIR#$HOME}/" ;;
    esac
    ARTIFACTS_SECTION=$'\n\n'"${MSG_DIALOG_DELETE_LABEL_ARTIFACTS}"$'\n  '"${ARTIFACTS_PATH}"
fi

# Splice the AI-generated session title and on-disk paths into the
# localized dialog body. This is plain bash text substitution — the
# resulting string is later handed to AppleScript as a single argv
# element by ``confirm_delete``, so its contents are inert from the
# scripting language's perspective.
DIALOG_BODY="$MSG_DIALOG_DELETE_BODY"
DIALOG_BODY="${DIALOG_BODY//\{title\}/${TITLE}}"
DIALOG_BODY="${DIALOG_BODY//\{transcript_label\}/${MSG_DIALOG_DELETE_LABEL_TRANSCRIPT}}"
DIALOG_BODY="${DIALOG_BODY//\{transcript_path\}/${TRANSCRIPT_PATH}}"
DIALOG_BODY="${DIALOG_BODY//\{artifacts_section\}/${ARTIFACTS_SECTION}}"

# Native macOS confirm dialog. We use ``display dialog`` (not ``display
# alert``) because it accepts ``with icon`` — that lets us replace the
# default folder icon (osascript's parent app icon) with the system
# trash icon, which matches what the button actually does. The price is
# no bold headline, so we prepend the question as the first line of the
# message argument.
if ! confirm_delete \
        "$MSG_DIALOG_DELETE_TITLE" \
        "$DIALOG_BODY" \
        "$ICON_PATH" \
        "$MSG_DIALOG_DELETE_CANCEL" \
        "$MSG_DIALOG_DELETE_CONFIRM"; then
    exit 0
fi

rm -f -- "$JSONL_PATH"
if [ -d "$TOOL_RESULTS_DIR" ]; then
    rm -rf -- "$TOOL_RESULTS_DIR"
fi

# Strip the matching row from the sidecar by field equality. ``awk`` here
# replaces the previous ``grep -v "^${SID}\t"`` — UUIDs don't contain
# regex metacharacters today, but field-equality semantics are simpler
# to reason about and immune to future schema changes.
if [ -f "$STATE_FILE" ]; then
    TMP="${STATE_FILE}.$$"
    /usr/bin/awk -F'\t' -v sid="$SID" '$1 != sid' "$STATE_FILE" > "$TMP" || true
    mv "$TMP" "$STATE_FILE"
fi

# Drop the session's bookmark too, if it was pinned — deleting a session
# should leave no pin residue. Same field-equality awk + mkdir lock as
# bin/app/bookmark-set.sh, so a concurrent pin/unpin click serializes on the
# same ``.lock.d`` (and a leaked lock self-heals: bookmark-set.sh steals a
# stuck lock after 40 attempts). The render-time orphan GC would also prune
# the pin on the next tick, but doing it here makes deletion self-contained
# and immediate rather than eventually-consistent.
if [ -f "$BOOKMARKS_FILE" ]; then
    BM_LOCK_DIR="${BOOKMARKS_FILE}.lock.d"
    bm_attempts=0
    until mkdir "$BM_LOCK_DIR" 2>/dev/null; do
        bm_attempts=$((bm_attempts + 1))
        if [ "$bm_attempts" -gt 40 ]; then
            rmdir "$BM_LOCK_DIR" 2>/dev/null || true   # steal a stuck lock
        fi
        sleep 0.05
    done
    BM_TMP="${BOOKMARKS_FILE}.$$"
    if /usr/bin/awk -F'\t' -v sid="$SID" '$1 != sid' "$BOOKMARKS_FILE" > "$BM_TMP"; then
        mv "$BM_TMP" "$BOOKMARKS_FILE"
    else
        rm -f "$BM_TMP"
    fi
    rmdir "$BM_LOCK_DIR" 2>/dev/null || true
fi

# Refresh SwiftBar so the row disappears in the next render rather than at
# the next 5 s tick.
/usr/bin/open "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

exit 0
