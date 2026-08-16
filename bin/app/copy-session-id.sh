#!/bin/bash
#
# Submenu action: copy a Claude Code session's id to the clipboard.
#
# Wired up from claude-agents.5s.py — the "Copy ID" item under a session
# row's "Session ▸" submenu runs it via /bin/bash so it doesn't depend on
# the executable bit (same reason as bookmark-set.sh / tag-set.sh):
#
#     /bin/bash copy-session-id.sh <session-id>
#
# The id is what identifies a session to `claude --resume <id>` and to any
# other agent you want to point at a parallel session, so the clipboard is
# the only useful destination for it.
#
# Silent on failure — a malformed click should not surface a dialog.

set -u

SID="${1:-}"

# Defence-in-depth: refuse anything outside the safe session-id alphabet
# (mirrors ``_SESSION_ID_RE`` in claude-agents.5s.py and the same check in
# reveal-session.sh / delete-session.sh). The plugin already filters unsafe
# ids out of the menu, so an invalid value only reaches us via manual
# invocation — refuse it there rather than pasting junk into the clipboard.
case "$SID" in
    "" | *[!A-Za-z0-9_-]* )
        exit 1
        ;;
esac
if [ "${#SID}" -gt 64 ]; then
    exit 1
fi

# No trailing newline — the id is meant to be pasted inline into a prompt or
# a ``--resume`` argument, and a stray \n turns into a submitted line.
printf '%s' "$SID" | /usr/bin/pbcopy >/dev/null 2>&1 || exit 1

exit 0
