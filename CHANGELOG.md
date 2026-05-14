# Changelog

All notable changes to ClaudeAgentsBar are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Architectural rationale for each piece below lives in [docs/adr/](./docs/adr/).

## Unreleased

### Per-session context-window indicator

Each row's hover submenu gained a fourth line under the git branch:
`{N}% — {used}k/{total}k`, marked with the `gauge.medium` SF Symbol.
Percent is how much room is left in the context window before
auto-compact; absolute numbers show used-vs-total. The numerator is
parsed from the freshest `usage` block in the session's JSONL
(`input_tokens + cache_creation_input_tokens +
cache_read_input_tokens`) by scanning only the trailing 64 KB, so the
cost is O(1) regardless of transcript size. Rows are hidden on
transcripts too young to have an assistant reply yet.

The denominator is exposed as a new config knob
**`context_window_tokens`** (default `1000000` — matches Claude
Opus 4.7 / Opus 4.6 / Sonnet 4.6, which has been Anthropic's API
default since 2026-04-23). Override down to `200000` when running
Haiku 4.5 or Sonnet 4.5. Invalid values (`0`, negative, non-numeric)
warn to SwiftBar's log and keep the 1M default. Auto-detection from
the transcript was considered and rejected — the API response carries
the model name but not the window size, and the transcript doesn't
record beta flags either. See
[ADR-0011](./docs/adr/0011-configurable-context-window.md) for the
alternatives.

Thirteen new tests across `TestFormatContextLeft`,
`TestLastUsageTokens`, and `TestConfigLoad`; total is now 84.

### Compact menu-bar mode

New optional config knob `"compact": true` switches the menu-bar title
to a narrower rendering for notched MacBooks where every slot to the
right of the camera housing is contested:

* The icon is suppressed.
* The 🟡🟢🔵 emoji counters are replaced with ANSI-coloured `●` bullets
  rendered through SwiftBar's `ansi=true`. Result: `●2 ●1 ●3` instead
  of `[icon] 🟡2 🟢1 🔵3` — roughly 30 px saved.
* Empty buckets are still omitted; if nothing is active, a single grey
  `●` keeps the plugin visible on the bar.

Default stays `false` so out-of-the-box rendering is unchanged. The
rationale for picking ANSI bullets over SF Symbols / numbers-only / a
narrower icon is captured in
[ADR-0010](./docs/adr/0010-compact-menubar-ansi-bullets.md).

Three new `TestConfigLoad` cases cover the default, a real JSON
boolean override, and the bogus-type rejection (since `bool("false")`
would otherwise silently parse as `True`). 71 tests total.

### Idle bucket split: FRESH / ACKNOWLEDGED / STALE

The single 🟢 *recent* bucket is gone. Idle sessions now flow through
three stages:

* 🟢 **FRESH** — Stop fired, the user hasn't opened the row from the
  menu yet. Stays fresh for `fresh_minutes` (default 60). A click
  promotes it immediately; otherwise it auto-promotes when the timer
  elapses.
* 🔵 **ACKNOWLEDGED** — under active follow-up. Each click restarts the
  `ack_minutes` (default 60) timer.
* ⚪ **STALE** — past the acknowledgement window, still visible until
  the global `window_minutes` evicts it.

The menu-bar title now carries three counters in urgency order
(🟡 / 🟢 / 🔵). STALE is deliberately omitted from the title — it would
always be the largest number and would drown out the urgent buckets.

### New sidecars and scripts

* `~/.claude/agent-state.clicks` — `{session_id: click_ts}` TSV
  maintained by the new `bin/open-session.sh`. Drives the
  🟢 → 🔵 promotion and the `ack_minutes` reset on every click.
* `~/.claude/agent-state.dismiss` — single-timestamp cutoff written by
  `bin/forget-sessions.sh`; sessions whose latest activity is at or
  before it are hidden until they fire a fresh hook event.
* `bin/open-session.sh` — replaces the inline `href=` on row clicks:
  records the click first, *then* fires the `vscode://…` deeplink.
* `bin/ack-fresh.sh` — backed by `claude-agents.5s.py --ack-fresh`,
  bulk-promotes every currently-🟢 row to 🔵.
* `bin/forget-sessions.sh` — wipes the state TSV and the clicks TSV
  under their mutexes, then writes the dismissal cutoff. Renamed from
  the earlier `clear-sessions.sh`. Nothing under `~/.claude/projects/`
  is touched.

### Tools submenu

A new *Tools* submenu in the footer (between *Refresh* and the SwiftBar
menu) groups the bulk actions: *Acknowledge all* (🔵 checkmark) and
*Forget all sessions* (🟠 eraser).

### Menu-bar icon: template images with multi-rep TIFFs

`menubar_icon` now accepts four forms instead of two:

* a plain glyph (emoji / Unicode);
* `sf:<name>` — SF Symbol (unchanged);
* `template:<path>` — monochrome PNG, rendered through SwiftBar's
  `templateImage=` so macOS tints it to match the menu bar;
* `image:<path>` — full-colour PNG, no theme tinting.

The default is now
`template:/Applications/Claude.app/Contents/Resources/TrayIconTemplate@2x.png`,
so the bar shows the Claude mark out of the box when Claude.app is
installed. PNG sources are auto-resized via `sips` and stitched at 1× /
2× / 3× into a multi-rep TIFF with `tiffutil -cathidpicheck` so retina
displays render crisply. Cached output lives under
`$XDG_CACHE_HOME/claude-agents-bar/`.

A new `menubar_icon_fallback` field (default `"🤖"`) is used when the
configured file is missing — Claude.app not installed, broken path,
etc.

### Config

* New: `fresh_minutes`, `ack_minutes`, `menubar_icon_fallback`,
  `compact`.
* Removed: `recent_minutes`. **Breaking** — old configs continue to
  load (the key is silently ignored as an unknown field), but the
  behaviour they encoded is now split across `fresh_minutes` and
  `ack_minutes`. Update by hand.

### Tests

* Coverage grew to 68 tests, adding `TestParseClicks`, `TestAckFresh`,
  `TestReadDismissTs`, and `TestMenubarIconPieces`.

## 1.0 — 2026-05-13

Initial release. Everything below shipped in this version.

### Menu bar

* 🤖 icon plus colour counters (🟡 active, 🟢 recent within 30 min), no
  text labels. Title dims when nothing is happening.
* Icon configurable: any emoji or an SF Symbol via the `sf:` prefix.

### Dropdown

* Sessions for the last 3 h (configurable), grouped:
  active → recent (≤ 30 min) → stale (> 30 min, still within window).
* Each row: state icon, AI-generated session title, coloured right
  label (`working` / `needs you` / `Xm ago` / `Xh ago`). Coloured
  segments via ANSI escapes with `ansi=true`.
* Click → opens that session in VSCode via
  `vscode://anthropic.claude-code/open?session=<uuid>`.
* Submenu per row:
  * 🗑 Delete session… — native confirm dialog, then removes JSONL,
    tool-results dir, and the sidecar row.
  * 📁 *project name* — clickable; reveals `cwd` in Finder.
  * ⎇ *git branch* — read-only, taken from `<cwd>/.git/HEAD` (live,
    not the snapshot Claude Code recorded at session start).

### Filtering

* Only interactive sessions appear in the menu: VSCode-extension
  sessions (`entrypoint == "claude-vscode"`) and terminal sessions
  (`entrypoint == "cli"`). Scripted runs (`entrypoint == "sdk-cli"` —
  Python SDK, `claude -p`, anything launched non-interactively) are
  filtered out unconditionally. See
  [ADR-0005](./docs/adr/0005-whitelist-interactive-entrypoints.md).

### Live state

* `hooks/agent-state.sh` registered against
  `SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` /
  `Notification` / `Stop` writes one row per session into
  `~/.claude/agent-state.tsv`.
* The plugin's watchdog demotes stuck `working` rows to `idle` after
  `watchdog_seconds` (default 90) using
  `max(TSV last_event_ts, JSONL mtime)` — catches both killed
  processes (TSV freezes) and stalled hooks (JSONL keeps streaming).
* The sidecar is garbage-collected at render time: rows whose JSONL is
  gone, or whose last event has fallen out of the dropdown window, are
  dropped under the same `mkdir`-based mutex the hook uses.

### Configuration

* Optional JSON file at
  `$XDG_CONFIG_HOME/claude-agents-bar/config.json`
  (or pointed at by `$CLAUDE_AGENTS_BAR_CONFIG`).
* `window_minutes` / `recent_minutes` / `watchdog_seconds` /
  `title_max` / `menubar_icon`.
* Invalid values per field fall back to the default; bad JSON keeps the
  menu running on full defaults. Warnings go to stderr (SwiftBar
  surfaces them under *Show Logs*).

### Installer

* `install.sh` refuses to run when the project sits inside the
  SwiftBar plugins folder (otherwise SwiftBar would discover and run
  the support scripts as plugins). See [ADR-0007](./docs/adr/0007-project-outside-plugins-folder.md).
* `~/.claude/settings.json` is patched additively: existing hooks of
  yours are preserved; ours are appended. A timestamped backup is
  taken first.
* The Claude Code hook is fed one synthesised event as a smoke test
  during install; the test row is cleaned up afterwards.

### Tests

* `tests/test_plugin.py` — 49 unit tests covering pure helpers,
  predicates, the sidecar parser, the config loader, and SwiftBar
  param quoting. Stdlib `unittest`, runs in ~1 ms.
* `python3 -m unittest discover -s tests` is part of the smoke
  checklist in `PLUGIN.md`.
