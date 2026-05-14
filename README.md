# ClaudeAgentsBar

A macOS menu-bar widget that shows live status of every running Claude Code
session — across all your projects, worktrees, and background agents — in one
glanceable place. Built as a [SwiftBar](https://github.com/swiftbar/SwiftBar)
plugin.

```
   ◐ 🟡1 🟢2 🔵1        ← menu bar: app icon + colour counters
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

## Why

Claude Code now encourages many sessions in flight at once — background
agents and parallel worktrees in particular. The VSCode sidebar shows only
the active workspace's sessions (and no live state). Other UIs hide that
state behind tabs and clicks. ClaudeAgentsBar puts the answer to *"which
agent needs me?"* on the menu bar, always visible no matter what app is
focused.

Scripted / scheduled runs (cron jobs, Python SDK invocations, anything
launched via `claude -p`) are filtered out unconditionally — they don't
need human attention and would otherwise clutter the list.

## What you see

### In the menu bar

A configurable icon (by default the Claude.app tray glyph) followed by
coloured counters, **no text labels**:

| Glyph | Meaning |
|-------|---------|
| 🟡 N  | N sessions are **active** right now — model is running a tool call, or a permission prompt / `AskUserQuestion` is open waiting for you |
| 🟢 M  | M sessions **finished and you haven't opened them yet** (fresh, within the configured fresh window) |
| 🔵 K  | K sessions you've **opened from the menu but haven't dismissed** — still in active follow-up |
| (dim icon) | nothing urgent — title fades out so it doesn't shout at you |

The ⚪ stale bucket is intentionally **not** counted in the menu bar — it
would always be the largest number and would drown out the urgent ones.

### When you click the icon

A dropdown of every session that's been active in the last 3 hours, sorted:

1. **Active** (🟡, top) — newest first
2. **Fresh** (🟢) — idle and not yet opened from the menu
3. **Acknowledged** (🔵) — idle, you opened it (or it auto-promoted from
   fresh after the fresh window elapsed); each click restarts the
   acknowledgement timer
4. **Stale** (⚪, bottom) — past the acknowledgement window, still within
   the 3 h dropdown window

Each row shows:

```
{state-icon} {ai-generated session title} · {right label}
```

The right label is the part that's coloured independently of the row (via
ANSI escapes):

- 🟡 `working` (bold yellow) — a tool call is in flight
- 🟡 `needs you` (bold red) — permission prompt / question open
- 🟢 `Xm ago` (green) — fresh, unopened
- 🔵 `Xm ago` (bold cyan) — acknowledged
- ⚪ `Xh Xm ago` (dim grey) — stale

**Click a row** → records the click into the click sidecar (which moves
the row from 🟢 to 🔵 and restarts the acknowledgement timer) and opens
the session in VSCode via
`vscode://anthropic.claude-code/open?session=<uuid>`.

### Submenu on each row

Hover over a row to reveal the submenu (▸ on the right):

- 🗑 **Delete session…** — confirms with a native dialog, then deletes the
  JSONL transcript, the tool-results directory, and the row from the
  state TSV. VSCode's Claude Code sidebar refreshes via its own fs
  watcher.
- 📁 **`{project-name}`** — click to reveal the session's `cwd` in Finder.
- ⎇ **`{git branch}`** — read-only, the current branch of `<cwd>/.git/HEAD`
  (not the stale value from session start).
- ⏱ **`{N}% — {used}k/{total}k`** — context-window indicator. Percent
  is how much room is left before the model auto-compacts; absolute
  numbers show consumed-vs-total. Computed from the freshest `usage`
  block in the JSONL (`input_tokens + cache_creation_input_tokens +
  cache_read_input_tokens`), so it stays in lock-step with what Claude
  Code itself reports. Hidden on sessions too young to have an
  assistant reply yet. The denominator defaults to **1M** — matches
  Claude Opus 4.7 / Opus 4.6 / Sonnet 4.6, which has been Anthropic's
  API default since 2026-04-23. Override down to `200000` via
  `"context_window_tokens"` in `config.json` when running Haiku 4.5 or
  Sonnet 4.5. The Anthropic API doesn't surface the window size in
  responses, so this stays a manual setting — see
  [ADR-0011](./docs/adr/0011-configurable-context-window.md) for the
  alternatives we considered.

### Tools submenu (in the footer)

Below the session list, between *Refresh* and the SwiftBar plugin menu,
sits a **Tools** submenu with two bulk actions:

- 🔵 **Acknowledge all** — flips every currently-🟢 row to 🔵 in one shot
  (records a synthetic click for each). Useful when you've already
  triaged a batch out-of-band and just want the counter to stop nagging.
- 🟠 **Forget all sessions** — wipes the state TSV and the clicks TSV
  and writes a dismissal cutoff so anything that exists only as a JSONL
  on disk is hidden too. Live sessions reappear on their next hook
  event; nothing under `~/.claude/projects/` is touched.

## How it works

| Piece | Role |
|-------|------|
| `claude-agents.5s.py` | SwiftBar plugin. Runs every 5 s. Reads `~/.claude/projects/*/*.jsonl` for transcripts plus three sidecar files for live state. Renders the menu. Also exposes a `--ack-fresh` subcommand used by the *Tools → Acknowledge all* button. |
| `hooks/agent-state.sh` | Bash script registered as a Claude Code hook on `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification`, `Stop`. On each event, atomically updates one row in `~/.claude/agent-state.tsv`. |
| `bin/open-session.sh` | Row click: records the click into `agent-state.clicks`, then opens the VSCode deeplink. Lets the plugin tell 🟢 fresh from 🔵 acknowledged. |
| `bin/delete-session.sh` | Confirm dialog + safe deletion of a session's files and sidecar row. |
| `bin/ack-fresh.sh` | *Tools → Acknowledge all*: delegates to `claude-agents.5s.py --ack-fresh` to bulk-promote every 🟢 row to 🔵. |
| `bin/forget-sessions.sh` | *Tools → Forget all sessions*: wipes the state TSV and the clicks TSV under their mutexes and writes a cutoff timestamp into `agent-state.dismiss`. |
| `settings-hooks.json` | Fragment merged into `~/.claude/settings.json` by the installer. |
| `install.sh` / `uninstall.sh` | Manage symlinks + the `settings.json` merge. |

State derivation:
- **`waiting`** — `Notification` hook fired most recently (permission prompt
  or `AskUserQuestion`).
- **`working`** — `SessionStart` / `UserPromptSubmit` / `PreToolUse` /
  `PostToolUse` fired most recently.
- **`idle`** — `Stop` fired most recently, or the watchdog demoted a
  `working` entry that hasn't emitted a hook in `watchdog_seconds`
  (90 s by default — handles crashed sessions).

An idle session is then placed into one of three buckets:

- 🟢 **Fresh** — Stop happened, no click on the row since. Stays fresh
  for `fresh_minutes` (default 60).
- 🔵 **Acknowledged** — either a click landed after Stop, or the fresh
  timer expired on its own. Each subsequent click restarts the
  `ack_minutes` (default 60) countdown.
- ⚪ **Stale** — past the acknowledgement window. Still in the dropdown
  until the global `window_minutes` evicts it.

Without the hooks the plugin still works, but every session looks `idle` —
the state TSV is what distinguishes `waiting` / `working` from idle.

Sessions whose transcript has been deleted, or whose last activity has
fallen out of the dropdown window, are garbage-collected from both the
state TSV and the clicks TSV on the next plugin tick, under the same
locks the writers use.

Sessions whose `entrypoint` indicates a scripted runtime (`sdk-cli` —
i.e. anything launched via `claude -p`, the Python SDK, or a scheduled
job) are skipped: only interactive sessions (`claude-vscode`, `cli`) make
it into the dropdown.

## Install

> ⚠️ **Do not place this project inside the SwiftBar plugins folder.**
> SwiftBar recursively scans its plugins directory with
> `MakePluginExecutable=1` and will run `install.sh` / `uninstall.sh` as
> plugins. Keep the project anywhere else (we use `~/Projects/ClaudeAgentsBar`).
> The installer refuses to run if it detects this misconfiguration.

```bash
brew install --cask swiftbar      # if not already installed
brew install jq                   # if not already installed
bash install.sh
```

The installer auto-detects the SwiftBar plugins folder via
`defaults read com.ameba.SwiftBar PluginDirectory`. It then:

1. Symlinks `claude-agents.5s.py` into the SwiftBar plugins dir.
2. Symlinks `hooks/agent-state.sh` into `~/.claude/hooks/`.
3. Backs up `~/.claude/settings.json` and **merges** the hook
   registrations into it (existing hooks are preserved).
4. Runs a smoke test through the hook.
5. Pings SwiftBar to refresh.

Sessions started **after** install populate the sidecar TSV and show
live state. Sessions started before will appear (from JSONL mtime) but
as plain `idle` until they emit their next hook event.

## Uninstall

```bash
bash uninstall.sh
```

Removes both symlinks and strips our hook entries from `settings.json`
(a fresh timestamped backup is taken first). The three sidecar files
(`agent-state.tsv`, `agent-state.clicks`, `agent-state.dismiss`) are
left behind — delete them manually if you want.

## Files

```
ClaudeAgentsBar/
├── claude-agents.5s.py      ← SwiftBar plugin (Python 3.9-compatible)
├── hooks/
│   └── agent-state.sh       ← Claude Code hook → state TSV
├── bin/
│   ├── open-session.sh      ← row click: record click + open in VSCode
│   ├── delete-session.sh    ← submenu action: confirm + delete a session
│   ├── ack-fresh.sh         ← Tools: bulk-acknowledge every 🟢 session
│   └── forget-sessions.sh   ← Tools: wipe sidecars, set dismiss cutoff
├── tests/                   ← unittest suite (stdlib only)
├── docs/adr/                ← architecture decision records
├── config.example.json      ← copy to ~/.config/claude-agents-bar/config.json
├── settings-hooks.json      ← settings.json patch
├── install.sh
├── uninstall.sh
├── CHANGELOG.md
├── README.md
└── PLUGIN.md                ← contributor / hacking guide
```

Three sidecar files live under `~/.claude/`, all maintained by the
scripts above:

| File | Writer(s) | Purpose |
|---|---|---|
| `agent-state.tsv` | `hooks/agent-state.sh`, plugin (gc) | One row per session: latest hook state + cwd. |
| `agent-state.clicks` | `bin/open-session.sh`, `bin/ack-fresh.sh` via plugin | `{session_id: click_ts}` — drives 🟢 → 🔵 promotion. |
| `agent-state.dismiss` | `bin/forget-sessions.sh` | Single timestamp; sessions whose latest activity is at or before it are hidden. |

## Configuration

User-tunable knobs live in an **optional JSON config file**. Defaults are
applied for any field you don't set, so the file is entirely optional.

Search order (first match wins):

1. `$CLAUDE_AGENTS_BAR_CONFIG` (explicit path)
2. `$XDG_CONFIG_HOME/claude-agents-bar/config.json`
3. `~/.config/claude-agents-bar/config.json`

To start from a working example:

```bash
mkdir -p ~/.config/claude-agents-bar
cp config.example.json ~/.config/claude-agents-bar/config.json
$EDITOR ~/.config/claude-agents-bar/config.json
```

SwiftBar will pick up the new values on the next 5 s tick — no install
or restart needed.

### Fields

| Key | Default | Meaning |
|-----|---------|---------|
| `window_minutes` | `180` | Hide sessions inactive longer than this from the dropdown. |
| `fresh_minutes` | `60` | An idle session stays 🟢 fresh for this long after Stop. A click before the timer expires promotes it to 🔵 immediately; otherwise it auto-promotes when the window elapses. |
| `ack_minutes` | `60` | An acknowledged session (🔵) fades to ⚪ stale after this long without a new click. Each click restarts the timer. |
| `watchdog_seconds` | `90` | `working` entries older than this get demoted to `idle` (handles crashed sessions). |
| `title_max` | `60` | Max length of a session title shown on a row. |
| `menubar_icon` | Claude.app tray icon | Icon drawn before the counters. Accepts a plain glyph, `sf:<name>`, `template:<path>`, or `image:<path>` — see *Menu-bar icon* below. |
| `menubar_icon_fallback` | `"🤖"` | Glyph used when `menubar_icon` points at a missing file (e.g. Claude.app not installed). |
| `compact` | `false` | When `true`, drops the icon and replaces the 🟡🟢🔵 emoji counters with ANSI-coloured `●` bullets (`●2 ●1 ●3`). Saves ~30 px on the menu bar — useful on notched MacBooks. See *Compact mode* below. |

Fractional values are accepted where they make sense — e.g.
`"window_minutes": 30` for a half-hour window, or `"fresh_minutes": 0.5`
for thirty-second granularity. Keys starting with `//` are ignored, so
JSON-style "comments" in the file are fine. Unknown keys are ignored too:
forward-compatible config files don't error.

### Menu-bar icon

`menubar_icon` accepts four shapes:

| Prefix | Example | Effect |
|---|---|---|
| *(none)* | `"✱"`, `"🤖"` | Embedded as an inline glyph. Apple Color Emoji won't line up with SF Pro baselines — use sparingly. |
| `sf:` | `"sf:bubble.left.fill"` | Rendered as an SF Symbol via SwiftBar's `sfimage=`. |
| `template:` | `"template:/Applications/Claude.app/Contents/Resources/TrayIconTemplate@2x.png"` | A monochrome PNG. macOS auto-tints it for the current menu-bar appearance (light / dark / active). **Default.** |
| `image:` | `"image:~/Pictures/my-icon.png"` | A full-colour PNG, no theme adaptation. |

Paths may be absolute or relative to the plugin directory. For
`template:` and `image:` sources the plugin auto-resizes to fit the menu
bar height and stitches the 1× / 2× / 3× variants into a multi-rep TIFF
so retina displays render crisply. The cached output lives under
`$XDG_CACHE_HOME/claude-agents-bar/` (or `~/.cache/claude-agents-bar/`).

If the configured file is missing the plugin falls back to
`menubar_icon_fallback` (default `"🤖"`), keeping the bar populated even
when e.g. Claude.app isn't installed.

### Examples

```json
{ "window_minutes": 720, "watchdog_seconds": 45 }
```

```json
{ "menubar_icon": "sf:bubble.left.fill", "fresh_minutes": 30, "ack_minutes": 90 }
```

### Compact mode

Setting `"compact": true` collapses the menu-bar title to its narrowest
form:

```
●2 ●1 ●3        ← compact: true   (ANSI-coloured bullets, no icon)
◐ 🟡2 🟢1 🔵3   ← compact: false  (default)
```

The icon is suppressed and `🟡🟢🔵` are replaced with `●` rendered
through SwiftBar's `ansi=true`, picking up the same yellow / green /
cyan tones as the dropdown rows. Empty buckets are still omitted; if
nothing is active, a single dim `●` keeps the slot occupied so the
plugin doesn't disappear from the bar entirely.

The trade-off is loss of branding (no Claude mark) in exchange for
roughly 30 px of horizontal space. Recommended only on notched
MacBooks where the bar is contended (see *MacBook notch* below); on a
roomy external display the default is easier to read at a glance.

Rationale for picking ANSI bullets over SF Symbols, narrower emoji, or
plain numbers lives in [ADR-0010](./docs/adr/0010-compact-menubar-ansi-bullets.md).

### Changing the refresh rate

SwiftBar derives the polling cadence from the filename — `claude-agents.5s.py`
means *every 5 seconds*. To poll every 10 seconds, rename the file (and the
symlink) to `claude-agents.10s.py`.

## Known limits

- Sessions started before the hooks were installed have no precise state —
  they show as `idle` from JSONL mtime only.
- SwiftBar uses native `NSMenu`; rows have no inline buttons. Actions live
  in the per-row submenu (revealed by hovering the row arrow).
- The plugin polls every 5 s — that's SwiftBar's minimum useful cadence.

## MacBook notch: icon not showing

On notched MacBooks (14"/16" Pro, the redesigned Air) the menu bar is split
in two by the camera housing. Only the strip to the **right of the notch**
is available for status items, and macOS lays them out right-to-left. When
you have many third-party menu-bar apps installed, the bar fills up and
items that don't fit are silently clipped behind the notch — they're still
running, just not drawn. ClaudeAgentsBar is a normal SwiftBar item and is
subject to the same clipping.

Symptoms:

- The plugin appears to "do nothing" — no icon, no counters.
- Other menu-bar apps you've added recently are also missing or only
  partially visible.
- Quitting one of those apps suddenly makes the ClaudeAgentsBar icon
  reappear on the right.

Confirm the plugin itself is healthy before chasing display issues:

```bash
/usr/bin/python3 ~/Projects/ClaudeAgentsBar/claude-agents.5s.py | head -40
```

If that prints the icon line and a session list, the plugin is working
correctly — only the menu-bar rendering is hiding it.

Fixes (in order of effort):

- **Turn on compact mode** (`"compact": true` — see *Compact mode*
  above). Drops ~30 px from the plugin's footprint by hiding the icon
  and swapping `🟡🟢🔵` for narrow ANSI `●` bullets. Cheapest fix; only
  costs you the Claude mark on the bar.
- **System Settings → Control Center.** Set indicators you don't use
  (Spotlight, Stage Manager, Screen Mirroring, Focus, Bluetooth, AirDrop,
  Sound, Now Playing, Fast User Switching, etc.) to *Don't Show in Menu
  Bar*. Each removed icon frees one slot to the right of the notch.
- **Quit or uninstall menu-bar apps you no longer need.** Cloud-sync
  clients, screenshot utilities, and update agents are common offenders.
- **Use a menu-bar manager** to hide overflow items behind a toggle:
  [Ice](https://github.com/jordanbaird/Ice) (free, open source),
  [Bartender](https://www.macbartender.com/), or
  [Hidden Bar](https://github.com/dwarvesf/hidden-bar). Pin
  ClaudeAgentsBar to the always-visible group so it never falls into the
  hidden bucket.

## Contributing

See [PLUGIN.md](./PLUGIN.md) for architecture, dev workflow, code style
notes, and how to add new submenu actions or icons.
