#!/bin/bash
#
# Tools-submenu action: pop a modal dialog with today's Claude Code usage.
#
# Wired up from claude-agents.5s.py — Tools → Stats today runs:
#
#     stats-today.sh
#
# All real logic lives inside the plugin itself behind the
# ``--stats-today`` subcommand: it scans ~/.claude/projects/*/*.jsonl
# for transcripts touched since local midnight, aggregates turns +
# tokens + top projects, and shows the result via osascript display
# dialog. This shell wrapper exists only because SwiftBar binds menu
# actions to executable scripts, not to Python interpreter args.

set -u

HERE="$(cd "$(dirname "$0")" && pwd -P)"
PLUGIN="${HERE}/../claude-agents.5s.py"

exec /usr/bin/python3 "$PLUGIN" --stats-today
