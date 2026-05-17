# Configuration reference

User-tunable knobs live in an **optional JSON config file**. Defaults
apply for any field you don't set, so the file is entirely optional.

## Where the file lives

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

…or click **Tools → Configuration…** in the menu — the plugin seeds
the file from `config.example.json` on first click and hands it to
your default text editor (`open -t`).

SwiftBar picks up new values on the next 5 s tick — no reinstall or
restart needed.

## All fields

| Key | Default | Meaning |
|---|---|---|
| `window_minutes` | `180` | How far back the dropdown lists sessions. Anything older is hidden. |
| `fresh_minutes` | `60` | An idle session stays 🟢 fresh this long after `Stop`. A click before the timer expires promotes it to 🔵 immediately; otherwise it auto-promotes when the window elapses. |
| `ack_minutes` | `60` | An acknowledged 🔵 session fades to ⚪ stale after this long without a new click. Each click restarts the timer. |
| `watchdog_seconds` | `90` | `working` entries whose last event is older than this get demoted to `idle` — covers sessions that crashed mid-tool and never emitted `Stop`. |
| `title_max` | `60` | Max length of a session title shown on a row, with an ellipsis appended on overflow. |
| `menubar_icon` | Claude.app tray icon (`template:` PNG) | Icon drawn before the counters. See *Menu-bar icon* below for the four accepted shapes. |
| `menubar_icon_fallback` | `"🤖"` | Glyph used when `menubar_icon` points at a missing file (e.g. Claude.app not installed). |
| `editor_url_scheme` | `"vscode://"` | URL scheme prefix used when opening a session on row click. Full URL: `<scheme>anthropic.claude-code/open?session=<uuid>`. Common values: `"vscode://"` (default, stock VSCode), `"vscodium://"` (VSCodium), `"cursor://"` (Cursor). Other Code-OSS forks may register their own scheme. Must include the trailing `://`. The editor must also have the `anthropic.claude-code` extension installed for the deeplink to land on a session. |
| `language` | `"auto"` | UI language for menu labels, dialogs, and `X ago` strings. Supported: `en`, `ru`, `zh`, `zh-TW`, `fr`, `de`, `it`, `vi`. `"auto"` detects from macOS `AppleLocale`, falling back to `$LANG`. Region tag optional — `zh-TW` picks Taiwan locale; others fall back to the primary subtag (`zh`) then `en`. |
| `compact` | `false` | When `true`, drops the icon and replaces `🟡🟢🔵` with ANSI-coloured `●` bullets (`●2 ●1 ●3`). Saves ~30 px — useful on notched MacBooks. See *Compact mode* below. |
| `context_window_tokens` | `1000000` | Total context-window size used to compute the per-session `{N}% — {used}k/{total}k` indicator. Matches Claude Opus 4.7 / 4.6 and Sonnet 4.6 (Anthropic's API default since 2026-04-23). Override to `200000` when running Haiku 4.5 or Sonnet 4.5. See [ADR-0011](./adr/0011-configurable-context-window.md) for the alternatives we considered. |
| `context_warning_threshold` | `80` | Percent of context-window usage above which the main row gets an inline `⚠ {pct}%` marker between the title and the age label. Yellow up to 90 %, red beyond — same zones Claude Code's CLI uses. Set to `100` to suppress the inline marker while keeping the submenu gauge. Valid range `1..100`; out-of-range and non-numeric values fall back to `80`. |
| `notify_on_stop` | `true` | Play a chime, speak a phrase, and show a macOS notification banner when a session finishes. Requires `terminal-notifier` (`brew install terminal-notifier`). Set to `false` to silence all completion notifications. |
| `notify_threshold_sec` | `30` | Skip the notification if the last user turn was less than this many seconds ago. Avoids noise from quick one-liner exchanges. |
| `notify_phrases` | `["Check it", "Done", "Ready for review", "Your turn"]` | Phrases spoken aloud (via `say`) and shown in the notification banner. One is chosen at random on each `Stop`. Replace the list entirely to customise the voice lines. |
| `notify_on_wait` | `true` | Play a chime, speak a phrase, and show a macOS notification banner when Claude is blocked on a tool-approval dialog (`PermissionRequest` event). Requires `terminal-notifier`. Set to `false` to silence permission-prompt notifications while keeping completion notifications. |
| `notify_wait_phrases` | `["Need instructions", "Awaiting input", "Decision needed", "I'm blocked"]` | Phrases spoken aloud and shown in the notification banner when Claude needs permission. One is chosen at random on each `PermissionRequest`. |

Fractional values are accepted where they make sense — e.g.
`"window_minutes": 30` for a half-hour window, or `"fresh_minutes": 0.5`
for thirty-second granularity. Keys starting with `//` are ignored, so
JSON-style "comments" in the file are fine. Unknown keys are ignored
too: forward-compatible config files don't error. Invalid values emit
a warning to SwiftBar's log (*Show Logs…*) and the default is used —
the menu never goes down on a broken config.

## Menu-bar icon

`menubar_icon` accepts four shapes:

| Prefix | Example | Effect |
|---|---|---|
| *(none)* | `"✱"`, `"🤖"` | Embedded as an inline glyph. Apple Color Emoji won't line up with SF Pro baselines — use sparingly. |
| `sf:` | `"sf:bubble.left.fill"` | Rendered as an SF Symbol via SwiftBar's `sfimage=`. |
| `template:` | `"template:/Applications/Claude.app/Contents/Resources/TrayIconTemplate@2x.png"` | A monochrome PNG. macOS auto-tints it for the current menu-bar appearance (light / dark / active). **Default.** |
| `image:` | `"image:~/Pictures/my-icon.png"` | A full-colour PNG, no theme adaptation. |

Paths may be absolute or relative to the plugin directory. For
`template:` and `image:` sources the plugin auto-resizes to fit the
menu-bar height and stitches the 1× / 2× / 3× variants into a
multi-rep TIFF so retina displays render crisply. The cached output
lives under `$XDG_CACHE_HOME/claude-agents-bar/` (or
`~/.cache/claude-agents-bar/`); safe to delete at any time — it'll be
rebuilt on the next tick.

If the configured file is missing the plugin falls back to
`menubar_icon_fallback` (default `"🤖"`), keeping the bar populated
even when e.g. Claude.app isn't installed.

Multi-rep TIFF rationale: [ADR-0008](./adr/0008-menubar-template-image-with-multirep-tiff.md).

## Compact mode

Setting `"compact": true` collapses the menu-bar title to its
narrowest form:

```
●2 ●1 ●3        ← compact: true   (ANSI-coloured bullets, no icon)
◐ 🟡2 🟢1 🔵3   ← compact: false  (default)
```

The icon is suppressed and `🟡🟢🔵` are replaced with `●` rendered
through SwiftBar's `ansi=true`, in a brighter palette than the
dropdown rows (the 9 px glyph competes with the wallpaper). Colour
semantics line up: yellow = active, green = fresh, blue =
acknowledged. Empty buckets are omitted; if nothing is active, a
single dim `●` keeps the slot occupied so the plugin doesn't
disappear from the bar entirely.

Trade-off: loss of branding (no Claude mark) in exchange for ~30 px
of horizontal space. Recommended only on notched MacBooks where the
bar is contended; on a roomy external display the default is easier
to read at a glance.

Rationale for picking ANSI bullets over SF Symbols, narrower emoji,
or plain numbers: [ADR-0010](./adr/0010-compact-menubar-ansi-bullets.md).

## Notifications

Two Claude Code events trigger an audible nudge:

| Event | When | Sound | Phrase source | Suppressible? |
|---|---|---|---|---|
| `Stop` | A session finishes its turn | `Hero.aiff` | `notify_phrases` | `notify_on_stop: false` |
| `PermissionRequest` | Claude is blocked on a tool-approval dialog | `Funk.aiff` | `notify_wait_phrases` | `notify_on_wait: false` |

For each event the plugin (1) plays the chime, (2) speaks a random
phrase from the matching list via macOS `say`, and (3) shows a
`terminal-notifier` banner. Clicking the permission banner deep-links
straight back to the waiting session in your editor — no need to
hunt through the menu bar.

**Why two knobs, not one.** Stop notifications are most useful for
long turns and noisy for quick one-liners — that's what
`notify_threshold_sec` (default 30 s) silences. Permission
notifications have no threshold: every approval prompt is
intentional, and Claude is genuinely stuck until you respond.
Silencing one channel doesn't affect the other.

**Customising the phrases.** Replace `notify_phrases` /
`notify_wait_phrases` with whatever you want spoken aloud — your
name, inside jokes, lines from a movie, your dog's name. `say` reads
any UTF-8 string, so non-ASCII works too. One phrase is picked at
random on each event, so a longer list gives more variety. To pick a
specific voice, edit `hooks/notify-stop.sh` / `hooks/notify-wait.sh`
(`say -v Samantha …`) — `say -v '?'` lists what's installed on your
Mac.

**Dependencies.** Banners require [`terminal-notifier`][tn]
(`brew install terminal-notifier`); without it the chime and `say`
still fire but no banner appears. `claude-agents-bar doctor` reports
its presence under the `notify/` check.

[tn]: https://github.com/julienXX/terminal-notifier

**Silencing everything temporarily.** macOS Focus / Do Not Disturb
suppresses the banner (and on Sonoma+, the chime as well) — usually
the right escape hatch when you don't want to edit config. For a
permanent off-switch:

```json
{ "notify_on_stop": false, "notify_on_wait": false }
```

Underlying hooks: `hooks/notify-stop.sh` and `hooks/notify-wait.sh`.
Both are plain Bash, read the same JSON config the plugin uses, and
degrade silently when `terminal-notifier` / `jq` / the icon asset is
missing.

## Changing the refresh rate

SwiftBar derives the polling cadence from the filename —
`claude-agents.5s.py` means *every 5 seconds*. To poll every 10
seconds, rename the file (and the symlink) to `claude-agents.10s.py`.

5 s is SwiftBar's minimum useful cadence; going lower won't make the
menu more responsive but will cost CPU.

## Examples

Half-hour dropdown window and a tighter watchdog:

```json
{ "window_minutes": 30, "watchdog_seconds": 45 }
```

SF-symbol icon, faster fresh promotion, longer ack window:

```json
{
  "menubar_icon": "sf:bubble.left.fill",
  "fresh_minutes": 30,
  "ack_minutes": 90
}
```

Compact mode for a 14" MacBook with a crowded menu bar:

```json
{ "compact": true }
```

Haiku 4.5 context window:

```json
{ "context_window_tokens": 200000 }
```

VSCodium instead of VSCode:

```json
{ "editor_url_scheme": "vscodium://" }
```

Cursor instead of VSCode:

```json
{ "editor_url_scheme": "cursor://" }
```

Force Russian UI:

```json
{ "language": "ru" }
```

Custom voice lines, no chime on quick turns, banner only on permission prompts:

```json
{
  "notify_on_stop": false,
  "notify_wait_phrases": [
    "Need a human",
    "Permission, captain",
    "Halt and catch fire",
    "Standing by"
  ]
}
```

## Files on disk

Four sidecar files live under `~/.claude/`, maintained by the plugin
and its hook/action scripts:

| File | Writer(s) | Purpose |
|---|---|---|
| `agent-state.tsv` | `hooks/agent-state.sh`, plugin (gc) | One row per session: latest hook state + cwd. |
| `agent-state.clicks` | `bin/open-session.sh`, `bin/ack-session.sh`, `bin/ack-fresh.sh` via plugin | `{session_id: click_ts}` — drives 🟢 → 🔵 promotion. |
| `agent-state.dismiss` | `bin/forget-sessions.sh` | Single timestamp; sessions whose latest activity is at or before it are hidden. |
| `agent-state.forget` | `bin/forget-session.sh`, plugin (gc) | `{session_id: forget_ts}` — per-row cutoff. Scoped variant of `agent-state.dismiss`. |

`uninstall.sh` leaves these in place — delete them manually if you
want a fully clean slate. The cache directory
`$XDG_CACHE_HOME/claude-agents-bar/` (re-rasterised menu-bar icons)
is also safe to delete at any time.

Locking and gc semantics for these files: [PLUGIN.md](../PLUGIN.md).
