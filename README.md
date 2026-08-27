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

**Session titles** — by default the row shows Claude's auto-generated `ai-title`, the same label VSCode displays, so the menu stays consistent with the editor. Set `use_session_titles_for_menubar: true` to instead use the **name** from Claude's response marker (`*-- Session name - Summary*`) — your own wording in place of the English auto-title. Either way that marker drives the spoken notifications (its primary purpose) — see [docs/configuration.md § Spoken summary](./docs/configuration.md#spoken-summary). Claude agents are instructed to end each reply with this line; you set that up once in your CLAUDE.md.

**Session groups** — file sessions into named groups in the editor's
Claude Code sidebar (extension 2.1.241+) and the menu mirrors them. By
default the group name prefixes the row title; switch *Tools → Grouping*
to *As in the extension* and each group folds into its own top-level
entry, its header carrying a counter per live state (`🟡 🟢2 · Backend`)
so a collapsed group still says whether anything inside needs you.
Read-only — creating, renaming and moving stay in the sidebar.

**Terminal sessions** — a session started in a shell rather than in the
editor is marked with a grey `>` symbol, and clicking it raises the
terminal tab that owns the process (Terminal.app / iTerm2, then `tmux`,
then `screen`) instead of firing the editor deeplink, which would have
resumed the same transcript a second time. Falls back to
`claude --resume` in a fresh window when there's nothing live to raise.
No opt-in.

**Submenu per row** — *Mark as read*, *Forget*, *Bookmark*, a *Session ▸*
submenu (*Reveal in Finder*, *Copy ID* — the id you hand to another agent or
to `claude --resume` — and *Delete…*) and a *Tags ▸* color picker, plus git
branch, the model running the session (`claude-opus-4-7` / `claude-fable-5`
/ …) and context %.
Hovering the context line shows the session's last `tool_use` as a
tooltip (e.g. `Read: main.py`, `Bash: pytest …`) — quick answer to
*what is this session doing?* without expanding anything.

**Subagent rollup** — sessions that spawn `Task` subagents grow a
`🤖×N` badge with the live count, and the submenu lists each
subagent (description, model chip, current tool, runtime). The
parent row stays 🟡 while any subagent is in flight, so a long
`Task` doesn't drift the parent into a misleading 🟢.

**Tools submenu** — bulk actions (acknowledge all / forget all),
today's activity summary (sessions, turns, tokens, breakdown by
model and subagent), a *Notifications* block with one-click pause /
resume / bypass for quiet hours, *Grouping* (how IDE session groups
are laid out), *Keep awake* (off / auto / always — wraps
`caffeinate -i`), a feedback link, and one-click jump to your
`config.json`.

**Audible nudges** — when a session finishes (`Stop`) or stalls on a
permission prompt (`PermissionRequest`), the plugin plays a short
chime, speaks a random phrase via macOS `say` (default sets:
`"Done"` / `"Your turn"` for stop, `"Awaiting input"` / `"I'm blocked"`
for permission), and shows a clickable banner that jumps straight to
the waiting session. Both phrase lists are config knobs — replace
them with whatever you find funnier (jokes, your cat's name, a Star
Wars soundboard). One-liner exchanges are skipped via
`notify_threshold_sec`. A finished session you never came back to gets
**re-nudged** at doubling intervals (20, 40, … min) while it sits 🟢
unread — `notify_idle_interval_min`, off with `0`. Requires
`terminal-notifier`; see
[docs/configuration.md § Notifications](./docs/configuration.md#notifications)
to silence one channel without losing the other.

## Install

```bash
brew install --cask swiftbar               # if not already installed
brew install terminal-notifier             # optional, enables banners + chime
brew install alexey-krylov/claude-agents-bar/claude-agents-bar
claude-agents-bar setup
```

`terminal-notifier` is optional — the menu-bar UI works without it,
but stop/permission banners and their click-to-jump-to-session
behaviour don't fire until it's installed. `claude-agents-bar doctor`
warns when it's missing.

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

> **No Homebrew?** See [docs/install-manual.md](./docs/install-manual.md)
> for a git-clone install path.

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

Full reference (~29 fields incl. notification chimes / voice /
quiet hours / keep-awake, icon formats, compact mode for notched
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

Suggestions and bug reports — open an issue. If you want to make a
change, fork the repo and send a PR. [PLUGIN.md](./PLUGIN.md) covers
the architecture and dev workflow. User-visible changes go in
[CHANGELOG.md](./CHANGELOG.md). For installing or upgrading via a
Claude Code agent, see [CLAUDE.md](./CLAUDE.md).
