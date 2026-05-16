# ClaudeAgentsBar

See which Claude Code session needs you, without ⌘-tabbing through
windows. A [SwiftBar](https://github.com/swiftbar/SwiftBar) plugin for
macOS.

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

## What it shows

**Menu-bar counters** — at a glance, no text labels:

| Glyph | Meaning |
|---|---|
| 🟡 N | N sessions are **working** or **waiting on a permission prompt** |
| 🟢 M | M sessions **finished, you haven't seen them yet** |
| 🔵 K | K sessions you've **opened, still in active follow-up** |
| (dim) | nothing urgent — the title fades so it doesn't shout |

**Session states** — each row in the dropdown carries one of four colours:

| Glyph | State | Meaning |
|---|---|---|
| 🟡 | **working / waiting** | Claude is running a tool, or paused on a permission prompt |
| 🟢 | **fresh** | Session finished, you haven't opened it yet |
| 🔵 | **acknowledged** | You've opened it; still within the follow-up window |
| ⚪ | **stale** | No interaction for a while — fades to grey, still reachable |

**Dropdown** — every session active in the last 3 hours, sorted by
urgency (active → fresh → acknowledged → stale). Click a row to open
it in your editor (VSCode by default; VSCodium and Cursor via a
one-line `editor_url_scheme` change). Above 80 % context usage the
row gets an inline `⚠ {pct}%` warning (red past 90 %), so a glance
tells you which sessions are close to auto-compact.

**Submenu per row** — *Mark as read*, *Forget*, *Delete…*, *Reveal
in Finder*, plus git branch + context %. Hovering the context line
shows the session's last `tool_use` as a tooltip (e.g. `Read: main.py`,
`Bash: pytest …`) — quick answer to *what is this session doing?*
without expanding anything.

**Tools submenu** — bulk actions (acknowledge all / forget all),
today's activity summary, a feedback link, and one-click jump to
your `config.json`.

## Install

```bash
brew install --cask swiftbar               # if not already installed
brew install alexey-krylov/claude-agents-bar/claude-agents-bar
claude-agents-bar setup
```

`setup` symlinks the plugin into SwiftBar's plugins directory,
registers the Claude Code hook in `~/.claude/hooks/`, and merges
(with timestamped backup) hook entries into `~/.claude/settings.json`.
Idempotent — safe to re-run.

Sessions started **after** setup populate the sidecar TSV and show
live state. Sessions started before will appear (from JSONL mtime)
but as plain `idle` until they emit their next hook event.

Verify everything is wired up:

```bash
claude-agents-bar doctor    # jq + python3 + SwiftBar.app, hook
                            # registration, sidecar freshness, editor app
```

### Uninstall

```bash
claude-agents-bar teardown
brew uninstall claude-agents-bar
```

Symlinks and hook entries are removed (a fresh `settings.json` backup
is taken first). The four sidecar files under `~/.claude/` are left
in place — they may contain forget/dismiss cutoffs you want to keep.

> Hacking on the plugin? Clone the repo instead of `brew install`-ing —
> see [PLUGIN.md](./PLUGIN.md).

## Configuration

All knobs are optional — defaults work. To customise, click
**Tools → Configuration…** in the menu (creates the file on first
open) and edit it. SwiftBar picks up new values on the next 5 s tick.

The three fields you're most likely to touch:

| Key | Default | Meaning |
|---|---|---|
| `fresh_minutes` | `60` | How long a finished session stays 🟢 before auto-promoting to 🔵. |
| `ack_minutes` | `60` | How long an acknowledged 🔵 session stays before fading to ⚪. |
| `menubar_icon` | Claude.app glyph | Plain glyph, `sf:<name>`, `template:<path>`, or `image:<path>`. |

Full reference (all 12 fields, icon formats, compact mode for notched
MacBooks, refresh cadence, sidecar files on disk):
[docs/configuration.md](./docs/configuration.md).

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

## Troubleshooting

**Icon not showing on a notched MacBook.** macOS clips menu-bar items
behind the notch when the bar fills up. Cheapest fix: enable compact
mode (`"compact": true` in `config.json`) — drops ~30 px by hiding
the icon and swapping `🟡🟢🔵` for narrow ANSI `●` bullets. Other
fixes: [docs/troubleshooting.md](./docs/troubleshooting.md).

**Plugin appears stuck or empty.** Run it standalone —
`/usr/bin/python3 claude-agents.5s.py | head -40`. If that prints
the icon line and a session list, the plugin is healthy and only the
menu-bar rendering is hiding it. Otherwise:
[docs/troubleshooting.md](./docs/troubleshooting.md).

## Contributing

[PLUGIN.md](./PLUGIN.md) has everything for hackers. User-visible
changes go in [CHANGELOG.md](./CHANGELOG.md). For installing or
upgrading via a Claude Code agent, see [CLAUDE.md](./CLAUDE.md).
