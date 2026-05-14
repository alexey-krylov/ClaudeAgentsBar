# Changelog

All notable changes to ClaudeAgentsBar are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Architectural rationale for each piece below lives in [docs/adr/](./docs/adr/).

## Unreleased

### Region-aware locale resolution

Locale codes from `defaults read -g AppleLocale` / `$LANG` /
`CONFIG.language` are now normalised (`zh_TW.UTF-8` → `zh-tw`) and
resolved region-first, then by primary subtag, then English. Two new
tables shipped alongside: `locales/zh-TW.json` (Traditional Chinese,
Taiwan terminology — `工作階段` / `重新整理` / `設定` instead of the
mainland `会话` / `刷新` / `配置`) and `locales/vi.json` (Vietnamese).
Users on generic `zh-*` locales fall through to `zh.json`; the
matching `<xbar.title.vi>` / `<xbar.desc.vi>` headers were added so
SwiftBar's About box localises too.

### Brighter ANSI palette for the compact menu-bar

`_print_menubar` (compact mode) now uses a dedicated palette
(`_ANSI_ACTIVE_BAR` / `_ANSI_FRESH_BAR` / `_ANSI_ACK_BAR` — the bold
bright `9{2,3,4}m` variants) for the `●` bullets. The dropdown rows
keep the softer `_ANSI_WORKING` / `_ANSI_FRESH` / `_ANSI_ACK` palette
they already had. Same colour semantics across both (yellow / green /
blue), but the 9 px bar glyph needs more contrast against the
wallpaper than a row sitting on the menu's solid background. See the
updated [ADR-0010](./docs/adr/0010-compact-menubar-ansi-bullets.md)
for the trade-off.

### Delete-session confirm dialog shows the actual paths

The per-row *Delete session…* confirmation now lists the exact
filesystem paths that are about to be removed — the transcript
`.jsonl` and, when the session ever invoked any tools, the
tool-results directory — each under a localized label (`Транскрипт:`
/ `Transcript:` / …, `Результаты инструментов:` / `Tool artifacts:`
/ …). Paths are shown with `$HOME` collapsed to `~` so they stay
readable inside the narrow text column. The question itself is
prepended to the body as the first line so it stands out above the
paths.

The dialog uses AppleScript's `display dialog … with icon alias` and
points at `/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/TrashIcon.icns`,
so the macOS trash icon appears next to the question instead of
osascript's default folder icon — matches what the *Delete* button
actually does and reads as destructive at a glance.

Two new locale keys (`dialog.delete.label.transcript`,
`dialog.delete.label.artifacts`) and two new body placeholders
(`{transcript_path}`, `{artifacts_section}`) added across all
locales.

### Per-row *Forget* action and submenu cleanup

Each session row's submenu gained a 🟠 **Forget** entry, sitting above
**Delete…** (eraser SF symbol, orange — same visual vocabulary as the
existing *Tools → Forget all sessions*). Clicking it records a
`{session_id → forget_ts}` row in a new sidecar `~/.claude/agent-state.forget`,
and the plugin then filters that session out until a fresh hook event or
click pushes its `last_event_ts` past the cutoff — same cutoff semantics
as the global dismiss, just per-row. A fresh event re-surfaces the row,
which is the intended escape hatch.

Motivation: the VSCode Claude Code extension's own *Delete* doesn't
remove the transcript — it only stores the session id under
`hiddenSessionIds` in its globalState, so the row keeps showing up here.
*Forget* is the row-level twin of *Forget all sessions* for that case;
the existing *Delete…* action (which physically wipes the transcript and
the tool-results dir) is unchanged in behaviour.

Same pass tightened the submenu layout:

* **Delete session…** is now just **Delete…** in every locale — the row
  context already conveys what's being deleted, and the shorter label
  pairs cleanly with the new **Forget** entry above it.
* The dedicated **📁 `{project-name}` → reveal in Finder** line is gone.
  The cwd was redundant with the project name on the main row, and the
  reveal-in-Finder action is a one-line shortcut for a flow that's
  rarely the goal. The full cwd is now exposed as a hover **tooltip** on
  the git-branch line instead. When the cwd isn't a git repository
  (`session.git_branch` empty), the branch line falls back to printing
  the cwd itself with the folder icon, so the path stays visible in
  every case.

`menu.forget_session` label added to all six locale tables (en / ru / de
/ fr / it / zh) and `menu.delete_session` retranslated as the shorter
"Delete…" / "Удалить…" / … in all six. New `bin/forget-session.sh`
carries the awk-based record-or-replace write under the same mkdir
mutex used by the other sidecars. Six new tests in `TestForgetSidecar`;
total is now 90.

### Configuration shortcut in Tools

A new *Tools → Configuration…* entry opens `config.json` in the system
default text editor (`open -t`). On the very first click the bundled
`config.example.json` is copied into place — so the user lands in a
documented starter file instead of getting "file not found". The path
is resolved Python-side via the existing `_config_path()` so the
lookup chain stays defined once. Rationale and rejected alternatives
in [ADR-0012](./docs/adr/0012-open-config-from-menu.md).

`menu.config` label added to all six locale tables (en / ru / de / fr /
it / zh). New `bin/open-config.sh` carries the seed + `open -t` logic.

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
