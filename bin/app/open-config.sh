#!/bin/bash
#
# Submenu action: open the user's config.json in the default text editor.
#
# Wired up from claude-agents.5s.py under *Tools → Configuration*. Invoked
# as:
#
#     open-config.sh <target-config-path> <example-config-path>
#
# Both paths are computed Python-side so the resolution rules (env-var
# override → XDG → ~/.config) live in exactly one place — _config_path()
# in claude-agents.5s.py. Duplicating them in bash would be a bug waiting
# to happen the next time the order changes.
#
# If <target> does not exist yet, the bundled example is copied to that
# location first. The user lands directly in a documented starter file
# instead of an empty buffer or a "file not found" dialog. The parent
# directory is created with `mkdir -p` if needed.
#
# `open -t` defers to whichever app macOS has registered as the default
# text editor (TextEdit out of the box, but VS Code / Sublime / BBEdit
# pick this up automatically once installed). Same affordance as
# right-clicking the file in Finder → "Open With…" — no hardcoded editor.

set -u

TARGET="${1:-}"
EXAMPLE="${2:-}"

if [ -z "$TARGET" ]; then
    osascript \
        -e 'display alert "ClaudeAgentsBar" message "No config path passed to open-config.sh." as critical' \
        >/dev/null
    exit 1
fi

# Bootstrap on first run. A missing file is the *normal* state — most
# users never need to override the defaults, so this is what they see the
# first time they click *Tools → Configuration*.
if [ ! -e "$TARGET" ]; then
    mkdir -p "$(dirname "$TARGET")"
    if [ -n "$EXAMPLE" ] && [ -f "$EXAMPLE" ]; then
        cp "$EXAMPLE" "$TARGET"
    else
        # Fall back to a minimal valid JSON object so the editor doesn't
        # immediately complain about a non-JSON file.
        printf '{}\n' > "$TARGET"
    fi
fi

/usr/bin/open -t "$TARGET"

exit 0
