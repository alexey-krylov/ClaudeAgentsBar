#!/bin/bash
#
# Submenu action: reveal a Claude Code session's JSONL transcript in Finder.
#
# Wired up from claude-agents.5s.py — each session row's "Reveal in Finder"
# submenu item runs:
#
#     reveal-session.sh <session-id>
#
# We find the transcript under ~/.claude/projects/<slug>/<sid>.jsonl and
# pop a Finder window with that file selected via ``open -R``. Useful for
# inspecting raw JSONL, exporting transcripts, or finding the tool-results
# subdirectory next to a session.
#
# Silent on failure — a malformed click should not surface a dialog.

set -u

SID="${1:-}"
if [ -z "$SID" ]; then
    exit 1
fi

# Defence-in-depth: refuse anything outside the safe session-id alphabet
# (mirrors ``_SESSION_ID_RE`` in claude-agents.5s.py and the same check
# in delete-session.sh). The plugin already filters unsafe ids out of
# the menu, so an invalid value only reaches us via manual invocation —
# fail loudly there rather than splicing into find.
case "$SID" in
    "" | *[!A-Za-z0-9_-]* )
        exit 1
        ;;
esac
if [ "${#SID}" -gt 64 ]; then
    exit 1
fi

PROJECTS_DIR="${HOME}/.claude/projects"

# Sessions live one level deep: ~/.claude/projects/<slug>/<sid>.jsonl.
# Bounded ``find`` with maxdepth 2 stays cheap; the session id is in the
# safe alphabet per the check above so it can't smuggle find predicates.
JSONL_PATH=""
while IFS= read -r candidate; do
    JSONL_PATH="$candidate"
    break
done < <(find "$PROJECTS_DIR" -maxdepth 2 -name "${SID}.jsonl" -type f 2>/dev/null)

if [ -z "$JSONL_PATH" ]; then
    # Transcript already deleted or never existed — nothing to reveal.
    exit 0
fi

/usr/bin/open -R "$JSONL_PATH" >/dev/null 2>&1 || true

exit 0
