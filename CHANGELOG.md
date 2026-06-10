# Changelog

All notable changes to ClaudeAgentsBar are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Architectural rationale for each piece below lives in [docs/adr/](./docs/adr/).

## 1.1.3 — Unreleased

### Added

- **Notification mode in the menu** — *Tools → Notifications* now has a
  *Banner and voice* / *Banner only* radio pair. *Banner only* mutes the
  chime **and** the spoken `say` for every notification while keeping the
  banner; *Banner and voice* restores them. The choice is a standing
  preference (not time-bound like quiet hours), stored in a sidecar at
  `~/.claude/agent-state.notify-audio.mode` that overrides the new
  `notify_audio` config knob (default `true`) — the same
  first-launch-default-then-sidecar precedence as *Multi-workspace mode*.
  Localised across all shipped locales.

## 1.1.2 — 2026-06-10

### Added

- **Spoken summary** — on `Stop`, `say` can read a one-line summary of
  what the session did instead of a random phrase. A new
  `notify_summary_marker` config knob (default `"-- "`) sets a line
  prefix: when the **last line** of the assistant's reply starts with it
  (after stripping markdown italic/bold wrappers), that text becomes the
  summary: `say` reads the random phrase **then** the summary ("Done.
  …"), while the notification banner shows the **summary alone**.
  Otherwise — or when the marker is `null` / `""` — both fall back to
  just a random `notify_phrases` entry. The match is literal and
  considers the closing line only. The feature is on by
  default but inert until your Claude is told to end replies with an
  italic `*-- …*` line — see
  [docs/configuration.md § Spoken summary](./docs/configuration.md) for
  the one-line `CLAUDE.md` instruction and
  [spec 0005](./docs/specs/0005-voice-summary.md). Hook-only change: no
  plugin, menu, or sidecar.

## 1.1.1 — 2026-06-02

### Added

- **Multi-workspace mode** — clicking a session opens it in the editor
  window that *owns* it, so it works smoothly when you keep several
  editor windows (or a multi-root workspace) open at once. The session
  deeplink on its own carries only the session id and the
  `anthropic.claude-code` handler delivers it to the frontmost window, so
  the plugin first surfaces the right window: on click, a shared
  `hooks/raise-and-open.sh` (the dropdown via `open-session.sh`, the
  banner via terminal-notifier `-execute`) brings the window owning the
  session's working directory to front, then fires the deeplink. It
  surfaces the window by opening a *file* inside the cwd
  (`open -a <editor> <file>` — an "open document" event the editor routes
  to the owning window, **multi-root aware**), so even a folder that's
  one root of a multi-root workspace lands in the existing window instead
  of a new one. Going through LaunchServices keeps it near-instant. The
  file it opens is the last one Claude touched in that session (newest
  tool-use `file_path` inside the cwd, from the transcript), so you land
  where the work was, falling back to a stable project file (`README`, …);
  it costs one extra editor tab. Works with the built-in schemes
  (`vscode://`, `vscodium://`, `cursor://`, `windsurf://`, `positron://`).

  Switchable: a `multi_workspace_mode` config knob (default `true`) and a
  *Tools → Multi-workspace mode* checkbox turn it on/off live — off gives
  the snappy single-window path (fire the deeplink directly, no extra
  tab, lands in the frontmost window). The checkbox writes a sidecar
  (`~/.claude/agent-state.multi-workspace.mode`) that wins over the config
  knob, mirroring *Keep awake*. A second knob `editor_focus_settle_sec`
  (default `0.1`, range `0..5`) tunes the pause that lets the anchor tab
  render before the deeplink fires.

### Changed

- **Quiet hours no longer hides the banner by default.** The default
  `quiet_hours_silences` is now `["sound", "voice"]` instead of all
  three channels: during a quiet window the chime and `say` voice are
  muted, but the clickable banner still appears so you never miss that
  a session finished or is waiting. Add `"banner"` back to
  `quiet_hours_silences` for full silence, or list only `"voice"` to
  keep the chime. Existing configs that set the key explicitly are
  unaffected.

### Fixed

- **Clicking a session now opens the matching window, not whatever's
  frontmost.** With several editor windows open, both a menu-bar
  dropdown row and a *Stop* / *awaiting input* notification banner used
  to deliver the deeplink to the focused window — the wrong one —
  because the `anthropic.claude-code` handler doesn't route by
  workspace. Both paths now run a new shared `hooks/raise-and-open.sh`
  on click (the dropdown via `open-session.sh`, the banner via
  terminal-notifier `-execute`), which surfaces the window owning the
  session's working directory and waits for it to come to front before
  firing the deeplink. It surfaces the window by opening a *file* inside
  the cwd (`open -a <editor> <file>` — an "open document" event the
  editor routes to the owning window, **multi-root aware**), rather than
  opening the folder, which would spawn a new window when the cwd is one
  root of a multi-root workspace. Going through LaunchServices keeps it
  near-instant (no editor-CLI startup), so the focus step always runs —
  no window-count check needed. The file it opens is the last one Claude
  touched in that session (newest tool-use `file_path` inside the cwd,
  from the transcript), falling back to a stable project file (`README`,
  …); costs one extra editor tab. Applies to the built-in schemes
  (`vscode://`, `vscodium://`, `cursor://`, `windsurf://`, `positron://`);
  otherwise it falls back to opening the deeplink directly.

## 1.1.0 — 2026-05-27

### Custom audio + quiet hours + keep-awake

Three notification-/lifecycle-level knobs land together, all
opt-in-with-sensible-defaults and surfaced under a new *Tools →
Notifications* + *Tools → Keep awake* block.

**Custom audio (spec 0001).** Three new config knobs let you override
the hardcoded `Hero.aiff` / `Funk.aiff` chimes and the system `say`
voice:

```jsonc
{
  "notify_sound_stop": "Hero",       // built-in name, abs/~ path, or null to suppress
  "notify_sound_wait": "Funk",
  "notify_voice": "Samantha"         // null = system default; "off" = skip say entirely
}
```

A missing file or unknown bare name logs a warning to SwiftBar's log
and falls back to no chime for that event — the notification is never
taken down by a misconfigured value. The voice is shared between Stop
and PermissionRequest; one voice with two phrase lists is enough
variety in practice. See
[docs/configuration.md § Custom audio](./docs/configuration.md#custom-audio).

**Quiet hours (spec 0002).** A nightly silence window plus one-click
*Pause for 1 hour* / *Pause until tomorrow morning* / *Resume now*
actions in the Tools submenu. Two pieces of config:

```jsonc
{
  "quiet_hours": "23:00-08:00",                 // 24h local; start>end wraps midnight
  "quiet_hours_silences": ["sound", "voice", "banner"]
}
```

The schedule covers the long tail (everyday overnights); the ad-hoc
sidecar (`~/.claude/agent-state.quiet-until`) covers "pause now"
clicks. Both gates feed into per-channel suppression — drop
`"banner"` from `quiet_hours_silences` to keep visual notifications
while muting audio overnight. The default ships opinionated
(`"23:00-08:00"`) so fresh installs aren't loud at 02:00. See
[docs/configuration.md § Quiet hours](./docs/configuration.md#quiet-hours).

The inverse channel — *Bypass until window ends* / *Cancel bypass* —
fires notifications even though the scheduled quiet window is active,
for nights when you do want to be reachable for the rest of *this*
window. Backed by a second sidecar
(`~/.claude/agent-state.quiet-bypass-until`); the deadline is always
pinned to the end of the current window so the bypass auto-expires
when the window does. Pause wins over bypass when both are held
(the user has more recently or more explicitly asked for quiet);
the Tools status line surfaces whichever is active
(`Quiet hours: bypassed for 3h 14m more`).

**Keep awake (spec 0003).** The plugin can own one detached
`caffeinate -i` and reconcile it every tick. Three modes selectable
from *Tools → Keep awake*:

| Mode | Behaviour |
|---|---|
| `off` *(default)* | Never inhibit sleep. |
| `auto` | Inhibit while any session is in `working` state (the parent rollup from spec 0004 already folds live subagents in). Waiting on a permission prompt doesn't count — if the user is away there's nothing for the screen to be lit for. |
| `always` | Inhibit until disabled. |

PID lives at `~/.claude/agent-state.caffeinate`; the mode override
lives at `~/.claude/agent-state.keep-awake.mode` (sidecar wins over
the `keep_awake` config knob once written). PID reuse across reboots
is defended with a `ps -p <pid> -o comm=` check before any signal is
sent. `caffeinate -i` does *not* override clamshell sleep — closed
lid + no external display still sleeps. See
[docs/configuration.md § Keep awake](./docs/configuration.md#keep-awake).

Hook plumbing: `hooks/notify-stop.sh` and `hooks/notify-wait.sh` now
source a shared `hooks/_notify-common.sh` for the config readers,
sound resolver, and quiet-hours gate — DRY across both hooks without
the bug-divergence risk of doubled state-machine code. The hook
scripts resolve their symlink chain to find the include, so
`brew`-installed and clone-installed variants both Just Work.

`bin/install/teardown.sh` now kills any caffeinate we own before
stripping the symlinks; sidecars under `~/.claude/agent-state.*` are
preserved per existing policy.

### Per-session model row in the submenu

The dropdown gains an axis that wasn't there before: which Claude
model is running each session. Useful when several agents are live at
once on the same project — title + branch don't disambiguate three
Sonnet sessions and one Opus session, but a dedicated row in the
submenu does.

Each session's submenu picks up a new info row carrying the full
model string (`claude-opus-4-7`), sitting between the branch line
and the context-usage line — same `font=Menlo color=#999999` style
as its neighbours, `cpu` SF Symbol. The row is shown whenever the
JSONL has a parseable `"model":"..."` field. A new config key
`model_badge` (default `true`) suppresses it in one go — escape
hatch for users who want a quieter submenu.

The session model is the last `"model":"..."` match in the JSONL
tail. Re-uses the same shared 128 KB tail buffer that backs the
existing usage / tool_use / branch readers, so the cost is folded
in. Mixed-model sessions (user switched mid-stream via `/model`)
take the *latest* — the row answers "what is live right now".

Subagent rows in the submenu deliberately do **not** carry their
own model row. Subagents inherit the parent's model in current
Claude Code releases; surfacing per-subagent rows would just be
noise. If `Task` ever gains explicit per-subagent model selection,
the row can grow one then.

The spec originally proposed an inline `ⓞ`/`ⓢ`/`ⓗ`/`ⓜ` badge next
to the title (yellow zone in Claude Code's own row UX). It's
deferred — the submenu row covers the same intent without crowding
the dropdown labels, and the family glyphs live on in the
*Stats today* surface below.

### Stats today: per-model and per-subagent breakdown

The *Tools → Stats today* dialog grows two new blocks below the
existing Sessions/Turns/Tokens header:

* **Models** — every Claude model that ran today, with the number
  of sessions on each (`ⓞ claude-opus-4-7: 5`, `ⓢ claude-sonnet-4-6:
  2`, `ⓜ openrouter/...: 1`). Sorted family-first (Opus → Sonnet →
  Haiku → others), then by model id for stable ordering across
  releases.
* **Subagents today** — count of `Task` invocations whose JSONL
  was modified after local midnight, plus the same family
  breakdown for the subagent runtimes. Useful to see how `Task`
  routes across families (Haiku for cheap lookups, Opus for hard
  reasoning).

Both blocks are pure tail reads on the existing per-transcript
JSONL walk, so they fold into the same single pass over
`~/.claude/projects/*/*.jsonl` that already powers the sessions /
turns / tokens aggregate.

### Per-row "Mark as read" action

🟢 FRESH session rows pick up a new submenu entry **Mark as read**
(blue checkmark), sitting above *Forget*. Clicks promote the
single session to 🔵 ACKNOWLEDGED without touching any of its
peers — the row-scoped twin of *Tools → Acknowledge all*. Hidden
on rows that aren't FRESH (a click would be a no-op).

Backed by a new `bin/app/ack-session.sh` that records a click into
`agent-state.clicks` under the same mutex `bin/app/open-session.sh`
uses.

### Tools → Suggest improvement…

A new footer entry above *Configuration…* (lightbulb glyph) opens
`https://github.com/alexey-krylov/ClaudeAgentsBar/issues/new` in
the default browser — one click to file feedback without hunting
the repo. Pure `href=` row, no shell wrapper. Localised label in
all eight locales (`menu.suggest`).

### Subagent activity surface (`🤖×N` badge + submenu block)

Spawning subagents through Claude Code's `Task` tool had two bad
effects on the menu:

1. Every subagent `PreToolUse` / `PostToolUse` event arrives with
   the **parent's** `session_id` (confirmed by the spike in
   [spec 0004](./docs/specs/0004-subagent-grouping.md) — risk #1
   fired). The pre-1.1 hook wrote those events straight into
   `agent-state.tsv`, clobbering the parent row's
   `last_event_kind` / `cwd` on every subagent tool call.
2. Long subagent runs drifted the parent row 🟡 → 🟢 → 🔵 mid-Task:
   the parent's own JSONL freezes while a `Task` is in flight, so
   the watchdog tripped even though work was actively happening
   underneath. Tracked in
   [`issues/no-green.md`](./issues/no-green.md).

`hooks/agent-state.sh` now branches on the payload's `agent_id`
field:

- Parent-side events (no `agent_id`) keep going to
  `~/.claude/agent-state.tsv` exactly as before.
- Subagent-side events (with `agent_id`) go to a new sidecar
  `~/.claude/agent-state.subagents.tsv`, keyed on
  `(parent_sid, agent_id)`. Schema:
  `parent_sid \t agent_id \t agent_type \t state \t state_since \t last_event_ts`.
- A new `SubagentStop` registration writes `state=stopped`. The
  hook accepts `stopped` only when `agent_id` is present, and
  `idle` only when it isn't — cross combinations are silent no-ops.

`render.build_session` rolls the subagent sidecar into the parent's
view:

- While at least one subagent is `working`, the parent stays
  ACTIVE — watchdog short-circuits, and an `idle` parent (parent
  `Stop` already fired but a subagent kept running) is promoted
  back to ACTIVE so the row doesn't flash 🟢 mid-Task.
- The row label gets a `🤖×N` suffix (live subagent count) between
  the title and the right-side age label.
- The submenu picks up a `🤖 Subagents (N/total)` info row plus
  one row per subagent: `[type] · working · 12s · Bash: grep …`
  for live ones, `[type] · done · 4m ago` for stopped ones. Rows
  aren't clickable — Claude Code's deep-link can't reach a
  subagent transcript.
- A per-subagent watchdog demotes `working` rows whose
  `last_event_ts` is older than `watchdog_seconds` to `stopped`,
  so a crashed Task doesn't pin the parent yellow forever.

Menu-bar counters (🟡 N 🟢 M 🔵 K) keep counting parents — a job
with five subagents is still one yellow dot, not six.

Locale tables grew four keys across all eight locales
(`row.subagent_badge`, `menu.subagents_header`,
`menu.subagent_working`, `menu.subagent_done`).
[`docs/specs/0004-subagent-grouping.md`](./docs/specs/0004-subagent-grouping.md)
carries the full spike write-up and the rationale for the new
schema; the model-surface section in that spec is deferred to a
follow-up branch.

Re-run `claude-agents-bar setup` to pick up the new `SubagentStop`
registration in `~/.claude/settings.json`. `bin/install/setup.sh`'s
existing purge-then-append merge handles the SubagentStop matcher
identically to the others — no manual edits needed.

Total test count across the 1.1.0 surface (subagents + model row +
quiet hours + custom audio + keep-awake + stats today blocks): **269**,
up from 49 in 1.0.

### Subagent block polish

Three rendering fixes for the subagent submenu block:

* **Status colour is back on the main row.** The first attempt
  put `sfimage=circle.fill sfcolor=systemYellow` / `systemGreen`
  on the main row to put it in the same icon column as the model
  / tool sub-rows. SwiftBar's `color=` (used for the row text)
  silently overrides `sfcolor=` on the same line, so every
  circle came out grey regardless of state. Reverted to inline
  🟡 / 🟢 emoji on the main row — they carry their own colour
  natively.
* **Indent works again.** NSMenu strips leading whitespace from
  rows that carry an `sfimage=`, so the NBSP indent on sub-rows
  vanished after the sfimage attempt and the hierarchy
  collapsed. Switching the model / tool sub-rows back to inline
  glyphs (`▣` for the chip, `↳` for the tool arrow) lets the
  NBSP indent survive. Hierarchy is now: header sfimage in the
  icon column; main rows NBSP×4 + 🟡/🟢 emoji; sub-rows
  NBSP×10 + `▣` / `↳`. The chip-shaped `▣` (U+25A3 WHITE SQUARE
  CONTAINING BLACK SMALL SQUARE) stands in for the `sfimage=cpu`
  the parent's model row uses — it's the inline counterpart of
  the SF Symbol the parent renders.
* **Tool-summary truncation** is head-clipped now
  (`…/app/src/main/Foo.kt`) rather than tail-clipped — the
  meaningful part of a long path or Bash command lives at the
  end. New `_shorten_head` helper in `core.py`; the existing
  `_shorten` (tail-clip) still drives titles.

## 1.0.0 — 2026-05-17

### Surface tool-approval prompts in the menu and as a banner

Permission dialogs ("Make this edit to X?", "Run this Bash command?")
are easy to miss — the only signal is an inline panel inside VSCode
that doesn't beep, doesn't surface in the menu bar, and sits there
silently while the agent is blocked. Until now ClaudeAgentsBar didn't
know about them either: the `Notification` hook was registered hoping
it would fire for approval prompts, but in the VSCode extension it
doesn't.

`PermissionRequest` does. It's a separate Claude Code hook event
documented as "fires when a permission dialog appears", and unlike
`Notification` it lights up reliably for the inline approval flow.
ClaudeAgentsBar now registers it (writing `waiting` to the TSV, same
state the `Notification` hook used to target) and the row gets a `❓`
between the title and the age label while the agent is blocked. The
state clears automatically on the next hook event — `PostToolUse` if
the user approves, `UserPromptSubmit`/`Stop` if they deny — so no
extra dismiss UI is needed.

A new sibling Bash hook `hooks/notify-wait.sh` fires on the same
event and produces the audible side of the alert: short `Funk.aiff`
chime, a spoken phrase from `notify_wait_phrases`, and a
`terminal-notifier` banner whose click deep-links straight back into
the waiting session. Two new config keys gate the behaviour:

| Key | Default | Effect |
|---|---|---|
| `notify_on_wait` | `true` | Set to `false` to silence permission notifications without affecting completion notifications |
| `notify_wait_phrases` | `["Need instructions", "Awaiting input", "Decision needed", "I'm blocked"]` | Replace to customise the spoken/banner text |

No `notify_threshold_sec` analogue — every approval prompt is
deliberate and worth surfacing.

`Notification` is kept registered as a fallback in case future
Claude Code releases start firing it for approval dialogs. Re-running
`setup.sh` is idempotent: it adds the `PermissionRequest` matcher,
symlinks `notify-wait.sh` into `~/.claude/hooks/`, and cleans up any
duplicate `agent-state.sh` matchers a previous version left behind.

### A session only enters the menu after real agent activity

Tightening over the previous "don't paint sessions yellow or green
on tab switches" fix. The earlier branch still let untouched
sessions leak in as blue (`ACKNOWLEDGED`): Claude Code writes a
`SessionStart` event into the JSONL transcript on every IDE tab
switch, which updates the file's mtime — and `collect_sessions`
treated any in-window JSONL as a renderable session, falling back to
`idle` for those without a TSV row. So clicking through a sidebar
full of sessions filled the menu with blue rows you'd never actually
worked with.

The new rule: a session appears in the menu only after a real hook
event has fired for it. `SessionStart` is no longer registered at
all (it doesn't reflect agent activity), `collect_sessions` filters
out any JSONL whose session id is missing from `agent-state.tsv`,
and `_doctor_check_hook_registration` now expects 5 events instead
of 6. The five surviving hooks — `UserPromptSubmit`, `PreToolUse`,
`PostToolUse`, `Notification`, `Stop` — between them cover every
state transition the menu cares about.

`hooks/agent-state.sh` is back to a plain `{working,waiting,idle}`
switch; the `session-start` pseudo-state introduced one branch ago
is gone (an unknown argument is still a silent no-op, so stale
registrations from a previous version don't crash). `setup.sh`
already idempotently purges old `agent-state.sh` matchers from
`settings.json`, including the obsolete `SessionStart` one, so a
re-run cleans up after itself.

### Don't flash sessions yellow or green on IDE tab switches

`SessionStart` fires not only on a genuine cold start but also when
the user merely re-opens an existing session in the IDE — the VSCode
extension emits it on every tab switch, with `source=resume` in the
payload. The hook used to write `working` unconditionally, so each
tab switch turned the corresponding menu row yellow (`ACTIVE`) even
though the agent wasn't actually doing anything.

Fix is in three layers:

* `hooks/agent-state.sh` now accepts a new pseudo-state
  `session-start` and branches on `payload.source`. `startup` /
  `clear` write `idle` (fresh session, awaits its first prompt —
  `UserPromptSubmit` will flip it to working). `resume` / `compact`
  leave the existing row untouched, and **write nothing** when no
  row exists yet: the plugin already falls back to the JSONL
  transcript's mtime in that case.
* `_classify` now requires `last_event_kind == "Stop"` before
  granting the FRESH grace window. Without this guard, any
  idle-with-a-recent-timestamp row painted the session green
  ("Stop fired, you haven't looked yet"), so writing an `idle`
  fallback during a tab switch turned every clicked session into
  a fake-green row. Non-Stop idles now collapse straight into
  ACKNOWLEDGED or STALE.
* The watchdog downgrade in `build_session` clears
  `last_event_kind` when it turns a stuck `working` into `idle`,
  so a hung session doesn't get retroactively painted green.

Re-run `claude-agents-bar setup` to pick up the new
[settings-hooks.json](./hooks/settings-hooks.json) registration — the hook
script itself is symlinked, so it updates with `git pull` alone.

### `setup.sh` is now idempotent across command-line changes

The settings.json merge was previously additive — re-running setup
after a bundled hook command changed (e.g. the `SessionStart` arg
above) appended a second matcher alongside the stale one, and both
fired on every event. `bin/setup.sh` now purges its own prior
matchers (anything whose command references `agent-state.sh`) before
appending the patch, so a re-run *replaces* the registration. User
hooks on the same events — anything whose command does not mention
`agent-state.sh` — are preserved untouched, including the edge case
where a user packed our hook into a matcher of their own (only that
hook entry is scrubbed; the rest of the matcher survives).

### "Currently doing" tooltip on the context-usage row

The context-window line in each session's submenu (`{N}% — {used}k/{total}k`)
now carries the freshest `tool_use` from the JSONL tail as its hover
tooltip: `Read: main.py`, `Bash: pytest …`, `Edit: src/parser.py`, etc.
Same pattern the branch row already uses to surface the full cwd —
leaf submenu rows render their NSMenuItem tooltip reliably on hover,
while the parent session row's hover gets eaten by AppKit's automatic
submenu expansion. The parser keeps a short map of
`tool name → input field` to pick the most meaningful arg
(`command` for `Bash`, `file_path` for editors, `query` for search
tools); unmapped tools fall back to the first string arg, so new tools
get a sensible default until they're added explicitly. Reading is
bounded to the trailing 64 KB — same window as `last_usage_tokens` —
so the cost stays O(1) per session regardless of transcript size.

No truncation: tooltips have plenty of room and the whole point is to
surface the full command/path that wouldn't fit in a row. Rows whose
tail has no parseable `tool_use` keep the bare context line — the
tooltip is just suppressed.

### Context-burn warning between title and age

Sessions that have consumed more context than
`context_warning_threshold` (new config knob, default 80 %) now render
an inline `· ⚠ {pct}%` token between the AI title and the right-hand
age label. Yellow up to 90 %, red beyond — matches the yellow / red
zones Claude Code's own CLI uses, so a single glance at the dropdown
tells you which sessions are close to auto-compact. Below the
threshold nothing extra is drawn; the existing `{N}% — {used}k/{total}k`
gauge in the submenu stays available for the detail view.

Pure rendering branch — `Session.context_used` was already computed
per-tick, so this adds zero I/O. New `_format_context_warning` helper
+ six unit tests around it; `context_warning_threshold` is validated in
`Config._from_mapping` against the `1..100` range, out-of-range and
non-numeric values fall back to 80 with the usual warning to stderr.

### Last user message as title fallback for new sessions

When Claude Code hasn't generated an `ai-title` event yet (the very
first turns of a freshly-started session), the row now shows the
*latest* real user prompt as its title instead of falling all the way
back to the project name. Previously this slot was the *first* prompt,
which on a long-running unsummarised thread became increasingly stale
the further the conversation drifted.

`last_user_message_preview` tail-reads 128 KB of the transcript and
filters out the noise Claude Code stores as `type:"user"` events
alongside real prompts: `tool_result` payloads, IDE/harness wrappers
(`<system-reminder>`, `<ide_opened_file>`, `<command-*>`, …) and the
synthetic `[Request interrupted by user for tool use]` line. Only
called when `meta.ai_title` is empty, so the warm path costs nothing.
`TranscriptMeta.display_title` priority is now
`ai_title → last_user_message → raw_title (= first prompt)`, with the
old "first prompt" entry kept as a last-ditch fallback for transcripts
whose tail didn't yield a parseable user event.

Eight new tests across `TestUserPromptText`,
`TestLastUserMessagePreview`, and `TestDisplayTitleFallback`.

### Reveal-in-Finder on each row

A new submenu entry under every session row — below *Forget* and
*Delete…* — opens Finder with the session's JSONL transcript selected
(`open -R ~/.claude/projects/<slug>/<sid>.jsonl`). Useful for
inspecting raw JSONL, exporting a transcript, or jumping to the
tool-results directory next to it. Silently no-ops if the transcript
was already deleted via the *Delete…* action, so a stale click won't
surface an error dialog.

New `bin/reveal-session.sh` mirrors the session-id validation from
`bin/delete-session.sh` (alphanumeric + `_-`, ≤ 64 chars) so a
manually-passed value can't smuggle `find` predicates. Label
`menu.reveal_in_finder` added across all eight locales.

### Tools → Stats today

A new entry in the *Tools* submenu pops a modal AppleScript dialog
with today's Claude Code activity, aggregated from
`~/.claude/projects/*/*.jsonl` since local midnight: number of active
sessions, real user turns (filtered through the same `_user_prompt_text`
that drives the title fallback so tool-results don't inflate the
count), tokens with prompt-vs-cache split + cache-hit ratio, and the
three projects with the most turns. JSONLs whose mtime is older than
midnight are skipped before opening the file, and the per-transcript
read is bounded to the trailing 64 KB for the usage block.

Implementation lives in the plugin behind a new `--stats-today`
subcommand; the `bin/stats-today.sh` wrapper exists only because
SwiftBar binds menu actions to executable scripts. Seven new locale
keys (`menu.stats_today`, `stats.title`, `stats.sessions`,
`stats.turns`, `stats.turns_short`, `stats.tokens`,
`stats.tokens_empty`, `stats.top_projects`) added across all eight
locales.

### `claude-agents-bar doctor` actually checks the install

The `doctor` subcommand stopped at *"jq + python3 + SwiftBar.app are
on disk"* — which says nothing about whether the plugin is actually
wired up. It now runs five additional in-plugin checks behind a new
`--doctor` subcommand on the plugin itself:

* `hooks/` — all six required events (`SessionStart`,
  `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification`,
  `Stop`) are registered in `~/.claude/settings.json` and point at
  `agent-state.sh`. Names the missing events when they aren't.
* `tsv/` — `~/.claude/agent-state.tsv` was written within the last
  hour, i.e. some session has actually fired hooks recently.
* `plugin/` — SwiftBar's `PluginDirectory` defaults preference is
  set and contains a `claude-agents.*.py` symlink.
* `perms/` — every `~/.claude/agent-state.*` sidecar is readable and
  writable by the current user.
* `editor/` — the configured `editor_url_scheme` resolves to an .app
  bundle that's actually installed (the top symptom from
  `docs/troubleshooting.md`: clicks on rows do nothing because the
  user has VSCodium but kept the default `vscode://`).

Each line prefixed with `[ok]` / `[warn]` / `[err]`, so the output
stays greppable in CI logs or Homebrew formula tests. Hard errors
(`err`) bubble up as a non-zero exit code; warnings don't, so the
command remains advisory rather than a gate. Nine new tests in
`TestDoctorChecks` covering the TSV/freshness, hook-registration,
and editor-app branches.

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
