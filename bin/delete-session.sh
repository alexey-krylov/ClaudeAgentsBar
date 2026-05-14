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

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd -P)"
PLUGIN="${HERE}/../claude-agents.5s.py"

# Pull localized dialog strings (MSG_DIALOG_*) from the plugin so we don't
# duplicate the string tables here. ``|| true`` keeps an obscure Python
# failure from killing the script before we can even show a dialog —
# unset MSG_* vars then expand to the English fallbacks below.
eval "$(/usr/bin/python3 "$PLUGIN" --print-strings 2>/dev/null || true)"
: "${MSG_DIALOG_NO_SID_TITLE:=ClaudeAgentsBar}"
: "${MSG_DIALOG_NO_SID_BODY:=No session id passed to delete-session.sh.}"
: "${MSG_DIALOG_NOT_FOUND_TITLE:=Session not found}"
: "${MSG_DIALOG_NOT_FOUND_BODY:=No transcript for session {sid}}"
: "${MSG_DIALOG_DELETE_TITLE:=Delete this Claude Code session?}"
: "${MSG_DIALOG_DELETE_BODY:={title}

{transcript_label}
  {transcript_path}{artifacts_section}

VSCode's sidebar should refresh automatically.}"
: "${MSG_DIALOG_DELETE_LABEL_TRANSCRIPT:=Transcript:}"
: "${MSG_DIALOG_DELETE_LABEL_ARTIFACTS:=Tool artifacts:}"
: "${MSG_DIALOG_DELETE_CONFIRM:=Delete}"
: "${MSG_DIALOG_DELETE_CANCEL:=Cancel}"

SID="${1:-}"
if [ -z "$SID" ]; then
    osascript \
        -e "display alert \"${MSG_DIALOG_NO_SID_TITLE}\" message \"${MSG_DIALOG_NO_SID_BODY}\" as critical" \
        >/dev/null
    exit 1
fi

CLAUDE_DIR="${HOME}/.claude"
PROJECTS_DIR="${CLAUDE_DIR}/projects"
STATE_FILE="${CLAUDE_DIR}/agent-state.tsv"

# Sessions live one level deep: ~/.claude/projects/<slug>/<sid>.jsonl.
# Bounded ``find`` with maxdepth 2 keeps it cheap and avoids scanning into
# any tool-result subdirectories.
JSONL_PATH=""
while IFS= read -r candidate; do
    JSONL_PATH="$candidate"
    break
done < <(find "$PROJECTS_DIR" -maxdepth 2 -name "${SID}.jsonl" -type f 2>/dev/null)

if [ -z "$JSONL_PATH" ]; then
    NOT_FOUND_BODY="${MSG_DIALOG_NOT_FOUND_BODY//\{sid\}/${SID}}"
    osascript \
        -e "display alert \"${MSG_DIALOG_NOT_FOUND_TITLE}\" message \"${NOT_FOUND_BODY}\"" \
        >/dev/null
    exit 0
fi
TOOL_RESULTS_DIR="${JSONL_PATH%.jsonl}"

# Show the AI-generated title in the confirm dialog so the user can tell
# *which* session they're about to delete. Scan only the first 200 lines —
# the ai-title event is emitted right after the first turn, anything deeper
# in the file just slows us down.
TITLE="$(/usr/bin/python3 - "$JSONL_PATH" <<'PYEOF' 2>/dev/null || true
import json, sys

with open(sys.argv[1], "rb") as f:
    for i, line in enumerate(f):
        if i > 200:
            break
        if b'"type":"ai-title"' not in line:
            continue
        try:
            print(json.loads(line).get("aiTitle", "").strip())
            break
        except Exception:
            pass
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
# localized dialog body. Plain string substitution (no AppleScript
# escaping needed because $TITLE comes from the transcript and the paths
# are filesystem-derived, not user-controlled AppleScript syntax).
DIALOG_BODY="$MSG_DIALOG_DELETE_BODY"
DIALOG_BODY="${DIALOG_BODY//\{title\}/${TITLE}}"
DIALOG_BODY="${DIALOG_BODY//\{transcript_label\}/${MSG_DIALOG_DELETE_LABEL_TRANSCRIPT}}"
DIALOG_BODY="${DIALOG_BODY//\{transcript_path\}/${TRANSCRIPT_PATH}}"
DIALOG_BODY="${DIALOG_BODY//\{artifacts_section\}/${ARTIFACTS_SECTION}}"

# Native macOS confirm dialog. ``display alert`` (instead of ``display
# dialog``) gives us a bold headline + smaller message body — so the
# question stands out from the file paths underneath. AppleScript raises
# error -128 on cancel, which under ``set -e`` would abort the script
# before we can inspect the answer — wrap in ``|| true`` so we fall
# through and decide based on the returned string. We pin the answer-
# matching token to the localized confirm label so the case statement
# below stays stable.
CHOICE="$(/usr/bin/osascript \
    -e "display alert \"${MSG_DIALOG_DELETE_TITLE}\" \
        message \"${DIALOG_BODY}\" \
        as warning \
        buttons {\"${MSG_DIALOG_DELETE_CANCEL}\", \"${MSG_DIALOG_DELETE_CONFIRM}\"} \
        default button \"${MSG_DIALOG_DELETE_CANCEL}\" \
        cancel button \"${MSG_DIALOG_DELETE_CANCEL}\"" \
    || true)"

case "$CHOICE" in
    *"button returned:${MSG_DIALOG_DELETE_CONFIRM}"*) ;;
    *) exit 0 ;;
esac

rm -f -- "$JSONL_PATH"
if [ -d "$TOOL_RESULTS_DIR" ]; then
    rm -rf -- "$TOOL_RESULTS_DIR"
fi

# Strip the matching row from the sidecar. Atomic rewrite so the SwiftBar
# plugin never reads a half-written file.
if [ -f "$STATE_FILE" ]; then
    TMP="${STATE_FILE}.$$"
    /usr/bin/grep -v "^${SID}	" "$STATE_FILE" > "$TMP" || true
    mv "$TMP" "$STATE_FILE"
fi

# Refresh SwiftBar so the row disappears in the next render rather than at
# the next 5 s tick.
/usr/bin/open "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

exit 0
