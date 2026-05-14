# ClaudeAgentsBar

Menu-bar widget for tracking parallel Claude Code sessions on macOS.
Built as a [SwiftBar](https://github.com/swiftbar/SwiftBar) plugin.

```
   ◐ 🟡1 🟢2 🔵1        ← menu bar: icon + colour counters
   ┌───────────────────────────────────────────────────────────┐
   │ 🟡 Refactor authentication middleware · working           │
   ├───────────────────────────────────────────────────────────┤
   │ 🟢 Add unit tests for the parser · 7m ago                 │
   │ 🟢 Investigate failing CI run · 18m ago                   │
   ├───────────────────────────────────────────────────────────┤
   │ 🔵 Wire up the new settings page · 32m ago                │
   ├───────────────────────────────────────────────────────────┤
   │ ⚪ Migrate config to TOML · 2h 14m ago                    │
   │ …                                                          │
   └───────────────────────────────────────────────────────────┘
```

## The problem

If you run more than one Claude Code session in parallel — background
agents, worktrees, long investigations — you lose track of which one
needs you. The Claude Code sidebar (in VSCode, VSCodium, or Cursor)
only sees the active workspace; ⌘-tabbing through windows to find the
yellow dot is slow. ClaudeAgentsBar puts every session's state in
your menu bar, in one glance, without pulling focus from whatever
you're actually doing.

## Install

### Via Homebrew (recommended)

```bash
brew install --cask swiftbar               # if not already installed
brew install alexey-krylov/claude-agents-bar/claude-agents-bar
claude-agents-bar setup
```

The first two lines install the prerequisites (SwiftBar.app and the
plugin itself, plus `jq` as a transitive dependency). `setup` then
symlinks the plugin into SwiftBar's plugins directory, registers the
Claude Code hook in `~/.claude/hooks/`, and merges (with timestamped
backup) hook entries into `~/.claude/settings.json`.

### From a git clone

```bash
git clone https://github.com/alexey-krylov/ClaudeAgentsBar
cd ClaudeAgentsBar
brew install --cask swiftbar               # if not already installed
brew install jq                            # if not already installed
bash install.sh                            # or: bin/claude-agents-bar setup
```

> ⚠️ **Don't place this project inside the SwiftBar plugins folder.**
> SwiftBar would scan it and try to run `setup`/`teardown` scripts as
> plugins. Anywhere else is fine (we use `~/Projects/ClaudeAgentsBar`).
> Setup refuses to run if it detects this misconfiguration.

### After install

Sessions started **after** setup populate the sidecar TSV and show
live state. Sessions started before will appear (from JSONL mtime) but
as plain `idle` until they emit their next hook event.

Check everything is in order:

```bash
claude-agents-bar doctor    # verifies jq + python3 + SwiftBar.app
```

### Uninstall

```bash
claude-agents-bar teardown   # or: bash uninstall.sh
```

Symlinks are removed, hook entries are stripped from `settings.json`
(with a fresh backup taken first), the four sidecar files under
`~/.claude/` are left in place. After a Homebrew install, follow up
with `brew uninstall claude-agents-bar` to remove the binary itself.

## What it shows

In the menu bar — counters only, no text labels:

| Glyph | Meaning |
|---|---|
| 🟡 N | N sessions are **working** or **waiting on a permission prompt** |
| 🟢 M | M sessions **finished, you haven't seen them yet** |
| 🔵 K | K sessions you've **opened, still in active follow-up** |
| (dim) | nothing urgent — the title fades so it doesn't shout |

Click the icon for the full dropdown — every session active in the
last 3 hours, sorted by urgency (active → fresh → acknowledged →
stale). Each row shows the AI-generated title and a coloured right
label: `working`, `needs you`, or `Xm ago`. Click a row to open the
session in your editor — VSCode by default, VSCodium and Cursor work
too (one-line `editor_url_scheme` change, see
[docs/configuration.md](./docs/configuration.md#all-fields)).

Hover a row for its submenu:

- **Mark as read** (🟢 rows) — flip to 🔵 without opening the editor.
- **Forget** — hide this row without deleting anything.
- **Delete…** — confirm + physically remove the JSONL transcript and
  state.
- Read-only context-window % and current git branch.

The footer **Tools** submenu has bulk actions (acknowledge all / forget
all), a feedback link, and a one-click jump to your `config.json`.

## Configuration

All knobs are optional — defaults work. To customise, click
**Tools → Configuration…** in the menu, or:

```bash
mkdir -p ~/.config/claude-agents-bar
cp config.example.json ~/.config/claude-agents-bar/config.json
$EDITOR ~/.config/claude-agents-bar/config.json
```

SwiftBar picks up new values on the next 5 s tick — no reinstall or
restart needed.

The three fields you're most likely to touch:

| Key | Default | Meaning |
|---|---|---|
| `fresh_minutes` | `60` | How long a finished session stays 🟢 before auto-promoting to 🔵. |
| `ack_minutes` | `60` | How long an acknowledged 🔵 session stays before fading to ⚪. |
| `menubar_icon` | Claude.app glyph | Plain glyph, `sf:<name>`, `template:<path>`, or `image:<path>`. |

Full reference (all 11 fields, icon formats, compact mode for notched
MacBooks, refresh cadence, sidecar files on disk):
[docs/configuration.md](./docs/configuration.md).

## Troubleshooting

**Icon not showing on a notched MacBook.** macOS clips menu-bar items
behind the notch when the bar fills up. Cheapest fix: enable compact
mode (`"compact": true` in `config.json`) — drops ~30 px from the
plugin's footprint by hiding the icon and swapping `🟡🟢🔵` for
narrow ANSI `●` bullets. Other fixes (Control Center, menu-bar
managers): [docs/troubleshooting.md](./docs/troubleshooting.md).

**Plugin appears stuck or empty.** Run it standalone:

```bash
/usr/bin/python3 claude-agents.5s.py | head -40
```

If that prints the icon line and a session list, the plugin is healthy
and only the menu-bar rendering is hiding it. Otherwise see
[docs/troubleshooting.md](./docs/troubleshooting.md).

## Why I built this

I run a handful of Claude Code sessions in parallel — background
agents, worktrees, the occasional long-running investigation — and
kept losing track of which one had just finished, which one was
sitting on a permission prompt, and which one I'd already triaged.
The Claude Code sidebar (same in VSCode, VSCodium, and Cursor — they
share the `anthropic.claude-code` extension) only sees the active
workspace; ⌘-tabbing through windows to find the yellow dot got old
fast, and I didn't want another floating panel stealing screen real
estate. A menu-bar widget was the smallest thing that could answer
*"which agent needs me right now?"* without pulling focus, so I wrote
one for myself and then cleaned it up enough to share.

## How it works

A Python script polls every 5 seconds. It reads
`~/.claude/projects/*/*.jsonl` (the transcripts Claude Code already
writes — for titles and `cwd`) plus a small sidecar TSV that a Claude
Code hook (`hooks/agent-state.sh`) updates on every session event
(`SessionStart`, `PreToolUse`, `Stop`, …). No daemon, no IPC — the
plugin is stateless and rebuilds the menu from disk on every tick.

Without the hooks the plugin still works, but every session looks
`idle` — the state TSV is what distinguishes `working` / `waiting`
from idle.

Architecture, dev workflow, code style, and how to add submenu actions
or new states: [PLUGIN.md](./PLUGIN.md). Design rationale for the
structural choices: [docs/adr/](./docs/adr/).

## Contributing

[PLUGIN.md](./PLUGIN.md) has everything for hackers. User-visible
changes go in [CHANGELOG.md](./CHANGELOG.md). For installing or
upgrading via a Claude Code agent, see [CLAUDE.md](./CLAUDE.md).
