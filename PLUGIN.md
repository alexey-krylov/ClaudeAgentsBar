# Contributing to ClaudeAgentsBar

This document is for anyone who wants to **hack on the plugin** — add a
new submenu action, change the rendering, fix a bug, or write a similar
SwiftBar plugin against Claude Code's data.

If you only want to *install* and use it, see [README.md](./README.md).

## Architecture in one diagram

```
            ┌─ ~/.claude/projects/<slug>/<sid>.jsonl ─┐   transcripts
            │   (written by Claude Code itself)        │
            ▼                                          │
                                                       │
  ┌────────────────────────┐    ┌─────────────────────┴────────┐
  │  agent-state.sh        │    │  claude-agents.5s.py         │
  │  (Claude Code hook)    │───▶│  (SwiftBar plugin, every 5s) │
  │  writes one TSV row    │    │  reads ALL sources, renders  │
  └────────────────────────┘    └──────────┬───────────────────┘
            │                              │
            ▼                              ▼
   ~/.claude/agent-state.tsv         macOS menu bar  ──┐
   ~/.claude/agent-state.subagents.tsv                  │ user clicks/hovers
   (live state per session +                            ▼
    one row per live subagent)                ┌───────────────────────────┐
            ▲                                 │  bin/open-session.sh      │
            │                                 │  bin/forget-session.sh    │
            │                                 │  bin/delete-session.sh    │
            │                                 │  bin/forget-sessions.sh   │
            └─────────────────────────────────┤  (actions triggered       │
   ~/.claude/agent-state.clicks               │   from menu rows / Tools) │
   ~/.claude/agent-state.dismiss              └───────────────────────────┘
   ~/.claude/agent-state.forget
   (sidecars written by actions)
```

The plugin is **stateless** — every 5 s it rebuilds the entire menu from
six sources:

1. The JSONL transcripts Claude Code already writes (titles, cwd).
   - **Session title sourcing** (priority order):
     - `session_title` — the **name** field of the last response's two-field marker line `*-- Name - Summary*` (prefix = `notify_summary_marker`, default `-- `; name/summary split on the first `" - "`). **Opt-in via `use_session_titles_for_menubar` (default off).** When off, `read_transcript_meta` skips the per-tick parse and leaves this empty, so the menu shows `ai_title` — the same label VSCode displays. When on, the parse runs at render time (still gated on `notify_summary_marker`, byte-prefiltered to assistant lines, so it's cheap). The prefix + divider are split identically in `sidecars._parse_marker_line` and `hooks/_notify-common.sh`. Note: the marker is parsed for the *spoken* notifications regardless of this knob (the hooks do it in Bash) — the toggle only affects the menu title.
     - `ai_title` — Claude Code's auto-generated conversation summary (event type `ai-title`). The **menu default**; a single-field marker (`*-- Summary*`, no name) or the opt-in being off both fall through to this.
     - `last_user_message` — latest user prompt (for fresh sessions).
     - `raw_title` — initial session title or first message (fallback).
2. `agent-state.tsv` that `agent-state.sh` maintains (parent state, last
   event). One row per session.
3. `agent-state.subagents.tsv` — also written by `agent-state.sh`, but for
   subagent-side events (events whose payload carries an `agent_id`).
   One row per `(parent_sid, agent_id)`. Drives the `🤖×N` badge and the
   parent state rollup so a row stays 🟡 while subagents are in flight,
   instead of drifting through 🟢 / 🔵 mid-Task.
4. `agent-state.clicks` that `hooks/record-click.sh` writes — the single ack
   writer shared by both resume paths (`open-session.sh` for a menu-row click,
   `raise-and-open.sh` for a notification-banner click). Records which idle
   sessions the user has opened — drives 🟢 FRESH → 🔵 ACKNOWLEDGED.
5. `agent-state.dismiss`, a single-timestamp cutoff set by *Forget all
   sessions* under Tools.
6. `agent-state.forget`, a `{sid → forget_ts}` map written by the per-row
   *Forget* action — same cutoff semantics as dismiss, scoped to one row.

There is no daemon, no IPC, no shared in-memory state. This makes the
plugin trivial to test (just run the script) and trivial to reason about.

## Project layout

```
ClaudeAgentsBar/
├── claude-agents.5s.py      ← plugin entry point, all rendering logic
├── hooks/
│   ├── agent-state.sh       ← hook: writes ~/.claude/agent-state.tsv
│   └── settings-hooks.json  ← hook registrations merged into ~/.claude/settings.json
├── bin/
│   ├── claude-agents-bar    ← main dispatcher (setup/teardown entry point)
│   ├── install/
│   │   ├── setup.sh         ← symlink plugin + hook, merge settings
│   │   └── teardown.sh      ← reverse setup
│   └── app/
│       ├── open-session.sh  ← row click: records click + opens in editor
│       ├── remind-session.sh ← submenu: re-speak the session's last summary
│       ├── ack-session.sh   ← submenu: mark one session as read
│       ├── ack-fresh.sh     ← Tools: acknowledge all fresh sessions
│       ├── forget-session.sh ← submenu: hide one session
│       ├── forget-sessions.sh ← Tools: wipe TSV + clicks, set dismiss cutoff
│       ├── delete-session.sh ← submenu: delete a session
│       ├── reveal-session.sh ← submenu: open in Finder
│       ├── open-config.sh   ← Tools: open config.json
│       └── stats-today.sh   ← Tools: show session activity summary
├── config.example.json      ← bundled starter config
├── tests/                   ← unittest suite for pure helpers + config
├── docs/adr/                ← architecture decision records (MADR)
├── CHANGELOG.md
└── README.md / PLUGIN.md
```

When in doubt about *why* a structural choice was made, consult
[docs/adr/](./docs/adr/) before changing it.

### Why is the plugin called `claude-agents.5s.py`?

SwiftBar uses the filename to learn the refresh cadence. `5s` means
"refresh every 5 seconds". Don't rename without updating the README.

### Why separate `hooks/`, `bin/install/`, and `bin/app/`?

- `hooks/` — scripts Claude Code runtime invokes on events (agent-state.sh)
- `bin/install/` — install/uninstall lifecycle (setup.sh, teardown.sh)
- `bin/app/` — actions the SwiftBar menu invokes (open, delete, ack, etc.)

Separating lifecycle from app actions makes intent clear and prevents
accidentally registering an action script as a Claude Code hook.

## Dev setup

### First-time bootstrap

```bash
git clone https://github.com/alexey-krylov/ClaudeAgentsBar
cd ClaudeAgentsBar
brew install --cask swiftbar               # if not already installed
brew install jq                            # if not already installed
bash install.sh                            # or: bin/claude-agents-bar setup
```

> ⚠️ **Don't place this project inside the SwiftBar plugins folder.**
> SwiftBar would scan it and try to run `setup`/`teardown` scripts as
> plugins. Anywhere else is fine. `install.sh` refuses to run if it
> detects this misconfiguration.

### Iterating

You don't need to reinstall anything to iterate on the plugin code —
`install.sh` only creates symlinks, so editing files in the repo
immediately reflects in the running plugin.

```bash
# Edit the script
$EDITOR claude-agents.5s.py

# Compile-check it (catches syntax errors against the same Python SwiftBar uses)
/usr/bin/python3 -m py_compile claude-agents.5s.py

# Run it standalone to see SwiftBar-format output
/usr/bin/python3 claude-agents.5s.py | head -30

# Force SwiftBar to refresh (otherwise it'll pick up the change in ≤5 s)
open "swiftbar://refreshallplugins"
```

> ⚠️ **Always test under `/usr/bin/python3`** (the system Python — 3.9 on
> recent macOS). That's what SwiftBar invokes via shebang. Your shell's
> `python3` may be a newer Homebrew install that accepts syntax 3.9
> rejects (we got bitten by `match-case` once).

## Code style

- **Python 3.9-compatible.** `from __future__ import annotations` is on,
  so generic syntax in type hints (e.g. `list[Path]`) is fine; runtime
  3.10+ features (`match`/`case`, `slots=True` on `@dataclass`) are not.
- **Pure helpers first, I/O second, rendering last.** The file is
  organised in that order — search for the section dividers.
- **No external dependencies.** Standard library only. The plugin must
  run with whatever ships on macOS.
- **Fail soft.** A broken JSONL, a missing `.git/HEAD`, an unreadable
  TSV row — every reader catches `OSError` / `json.JSONDecodeError`
  and returns an empty value. The menu must always render *something*.
- **One last-ditch try/except** in `main()` prints `⚠️` to the menu bar
  rather than letting SwiftBar show a stack trace.

## Adding a submenu action

Each session row's submenu lives in `_print_session_row`; the
footer-level *Tools* submenu lives in `_print_footer`. To add a new
action, append a `--`-prefixed line under either. Three knobs you'll
typically use:

```python
print(
    f"--{label} | "                   # menu text (with leading "--" for submenu)
    f"shell={_swiftbar_quote(...)} "  # binary to run
    f"param1={_swiftbar_quote(...)} " # first arg
    f"terminal=false "                # don't open Terminal
    f"refresh=true "                  # refresh SwiftBar after the action finishes
    f"sfimage={SF_SYMBOL_NAME}"       # leading icon (SF Symbol)
)
```

If your action needs **confirmation**, model it on `bin/delete-session.sh`:
shell wrapper with `osascript -e 'display dialog …'` for the prompt,
followed by the real work behind a button-name check.

### Conventions for new actions

- **Use SF Symbols for icons.** Emoji works but inherits the size/quirks
  of Apple Color Emoji; SF Symbols are crisp at any DPI and inherit the
  menu colour. `sfimage=trash.fill sfcolor=systemRed` is the canonical
  destructive style.
- **End user-facing labels with `…` when the action opens a dialog.**
  Native macOS HIG.
- **Set `refresh=true`** when the action changes anything the plugin
  reads — otherwise the menu is stale until the next 5 s tick.
- **Quote every path/value** via `_swiftbar_quote`. SwiftBar's lexer
  parses param values before they reach your script.

## Adding a config knob

User-tunable knobs live in `~/.config/claude-agents-bar/config.json` and
are loaded once into the :class:`Config` dataclass at import time. To add
a new field:

1. Add the attribute (with a default) to `Config`.
2. Document the JSON-friendly key in the docstring's *Field semantics*
   section.
3. Wire it up inside `Config._from_mapping` using the local `take()`
   helper. Pass a `transform` if the JSON shape differs from the internal
   one (e.g. `window_minutes` JSON → `window_sec` field).
4. Replace any in-code constant referring to the value with
   `CONFIG.<field>`.
5. Add the field to `config.example.json` and the README's table.

The `take()` helper is deliberately tolerant — an invalid value emits a
warning to stderr (SwiftBar surfaces it under *Show Logs*) and keeps the
default. Never raise from config parsing: a broken file shouldn't take
the menu down.

## Render groups

The plugin partitions sessions into four buckets:

| Group | Glyph | Meaning |
|---|---|---|
| `ACTIVE` | 🟡 | `working` or `waiting` per the state TSV |
| `FRESH` | 🟢 | idle, no click after Stop, `now < stop_ts + fresh_sec` |
| `ACKNOWLEDGED` | 🔵 | idle, either user clicked or the fresh timer expired; each click restarts the `ack_sec` countdown |
| `STALE` | ⚪ | idle, past the ack window — out of sight, still in the dropdown until `window_sec` evicts it |

The menu-bar title shows counters for ACTIVE / FRESH / ACKNOWLEDGED (in
that order, see `_MENUBAR_COUNTER_ORDER`). STALE is intentionally
omitted: it would be the largest number and would drown out the urgent
buckets.

`_print_menubar` has two branches keyed on `CONFIG.compact`:

* **default** — `[icon] 🟡N 🟢M 🔵K`, dimmed when every bucket is zero.
* **compact** — `●N ●M ●K`, with each `●` ANSI-coloured via
  `_COMPACT_ANSI`. The icon is dropped entirely. When everything is
  zero a single grey `●` keeps the plugin visible on the bar. See
  [ADR-0010](./docs/adr/0010-compact-menubar-ansi-bullets.md) for why
  ANSI bullets won over SF Symbols / narrower glyphs / numbers-only.

### Adding a new state

1. Add an enum member to `RenderGroup` with a 4-tuple
   `(key, order, icon, color)`. Lower `order` = higher in the list.
2. Update `_classify` to route the new condition into the group.
3. Optionally add an ANSI variant in `right_label_ansi` for inline
   colour on the timestamp.
4. Decide whether the new group should appear in the menu-bar counter —
   if yes, append it to `_MENUBAR_COUNTER_ORDER` (and to the per-group
   `counts` dict in `render`).

Sorting and section separators derive directly from the enum order.

### Row indicators (orthogonal to the group)

A few signals ride on top of the bucket colour:

* **Waiting marker.** `Session.right_label` wraps a `waiting` row's
  duration in the localised `label.blocked` template (`waiting {duration}`),
  so a blocked session names its state rather than showing a bare red
  number. `working` and idle rows keep the plain duration.
* **Branch line colour.** `_branch_decoration(session)` picks the submenu
  branch line's colour, text and status tooltip with a fixed priority:
  `cwd_collision` (red `#cc0000`, `⚠`, `tooltip.cwd_collision`) >
  `is_worktree` (green `#1f7a1f`, `tooltip.worktree`) > plain grey.
  `cwd_collision` is set in `collect_sessions` via `_mark_cwd_collisions`
  (two or more active sessions sharing a normalised non-empty `cwd`);
  `is_worktree` is computed once in `build_session` from
  `sidecars.is_worktree_checkout`.
* **Inline main-row markers.** `_print_session_row` emits the same two
  signals inline between the title and the duration, as ANSI-coloured
  glyphs (the main row is `ansi=true`, so it can't use `sfimage`):
  `cwd_collision` → a red `⎇` branch glyph (the same branch icon the
  notification banner uses, spec 0009), `is_worktree` → a `ⓦ` that is green
  normally and red when the worktree *also* collides. A colliding worktree
  shows the red `ⓦ` *alone* — the marker absorbs the collision signal and
  the branch glyph is suppressed, so the row never carries two red markers. The
  worktree marker is always on (no config knob), mirroring the collision
  fork; `is_worktree` is already computed each tick, so it adds no cost.

## Touching the hook

`hooks/agent-state.sh` runs on every Claude Code event. It's a Bash
script using a `mkdir`-based mutex (atomic on every POSIX filesystem,
unlike `flock` which isn't on stock macOS) plus `awk` to atomically
rewrite a single row of `agent-state.tsv`. The plugin uses the **same**
mutex (`_sidecar_lock` in Python) when it garbage-collects stale rows,
so concurrent hook writes and plugin cleanups can't race.

The state column is driven by which Claude Code event the hook fires
on (see `hooks/settings-hooks.json`):

| Claude Code event | Written state | Routes to |
|---|---|---|
| `UserPromptSubmit` / `PreToolUse` / `PostToolUse` | `working` | parent or subagent TSV (by `agent_id`) |
| `Notification` / `PermissionRequest` | `waiting` | parent TSV (subagents don't emit these) |
| `Stop` | `idle` | parent TSV |
| `SubagentStop` | `stopped` | subagent TSV |

`PermissionRequest` is the reliable signal that Claude is blocked on a
tool-approval dialog — `Notification` is registered too but doesn't
fire for inline approval prompts in the VSCode extension. Both write
the same `waiting` state, so the plugin doesn't care which one
delivered it. See the [Claude Code hooks reference][cc-hooks] for the
full event list.

The **subagent split** is the load-bearing routing in the hook: every
event Claude Code runs *inside* a `Task` (subagent's PreToolUse,
PostToolUse, etc.) arrives with the parent's `session_id` but also a
non-empty `agent_id` field. If `agent_id` is present the hook writes
into `agent-state.subagents.tsv` (keyed on `(parent_sid, agent_id)`)
instead of clobbering the parent row. Without this split, subagent
tool calls would overwrite the parent row's `last_event_kind` / `cwd`
many times per Task. With it, the plugin can both:

* leave the parent row's bookkeeping untouched, and
* notice that the parent is still doing work (via the subagent row's
  fresh `last_event_ts`) so the watchdog doesn't demote 🟡 to 🟢 / 🔵
  mid-Task. See [spec 0004](./docs/specs/0004-subagent-grouping.md)
  for the spike that confirmed subagents share the parent's
  `session_id`.

Two sibling Bash hooks ship alongside `agent-state.sh` and produce
side effects (sound, banner) rather than touching the TSV:

| Hook | Fires on | What it does |
|---|---|---|
| `hooks/notify-stop.sh` | `Stop` | Plays `Hero.aiff`, speaks a phrase from `notify_phrases`, shows a `terminal-notifier` banner. Has a `notify_threshold_sec` knob so quick one-liners don't beep. With `notify_summary_marker` set (default `"-- "`), when the assistant's closing line starts with the marker, `say` appends that line's text to the phrase and the banner shows it alone — see [spec 0005](./docs/specs/0005-voice-summary.md). |
| `hooks/notify-wait.sh` | `PermissionRequest` | Plays `Funk.aiff`, speaks a phrase from `notify_wait_phrases`, shows a banner whose click jumps straight to the waiting session. No threshold — every approval prompt is intentional. |

Both read the same JSON config the plugin uses and degrade gracefully
when `terminal-notifier` / `jq` / the icon asset isn't present. The
chime + speech + banner tail and the random-phrase picker are factored
into `_emit_notification` / `_pick_phrase` in `hooks/_notify-common.sh`,
so all three notify scripts share one implementation and differ only in
sound / phrases / title. The shared banner layout (spec 0009): line 1 is
the title each shim passes (Stop's `ai-title`; the phrase prefixed with a
type emoji — ❓ awaiting, ⚠️ idle — for the other two), line 2 is the
session's `<project> — <icon> <branch>` computed by `_banner_subtitle`
from the cwd (`_git_branch_from_cwd` reads `.git/HEAD` worktree-aware,
mirroring `sidecars.current_git_branch`; `<icon>` is `ⓦ` for a worktree
or `⎇` for an ordinary branch), line 3 is the marker `name — summary`.
The emoji is banner-only — it's never in the `say` text.

The `say` step is serialized across processes by `_say_lock_acquire` /
`_say_lock_release` (an atomic `mkdir` mutex at
`~/.claude/agent-state.say.lock` — macOS has no `flock`), so concurrent
notifications (a finishing Stop + an idle nudge, plus the *Remind* click,
which shares the same lock) never talk over each other. `_say_lock_release`
holds the lock for `notify_say_gap_sec` before releasing, which is the
inter-utterance pause; an utterance that waits past `notify_say_stale_sec`
is dropped unspoken. Only speech is locked — the chime and banner still
fire in parallel. The holder records its real pid via `sh -c 'echo $PPID'`
(`$$` in a `( ) &` subshell is the parent's, and bash 3.2 has no
`BASHPID`) so a waiter can steal a dead holder's lock — see
[spec 0010](./docs/specs/0010-speech-lock.md).

A third script, `hooks/notify-idle.sh`, is **not** a Claude Code hook —
it isn't registered in `settings-hooks.json` and reads its session id +
cwd from positional arguments, not a hook payload. It's fired by the
plugin itself (`claude_agents_bar/idle_reminders.py`) on the SwiftBar
tick for 🟢 green sessions left unread too long (spec 0008). There's no
"N minutes after Stop" Claude Code event and the project runs no daemon,
so the periodic tick is the only place a time-based reminder can come
from. `reconcile` (called from `main` right after `keep_awake.reconcile`)
does only cheap work — read the `agent-state.idle-reminders` sidecar,
compare timestamps against the doubling schedule, and `Popen` the
detached script (so the transcript parse for the spoken name/summary
happens off the tick). Progress is tracked in `agent-state.idle-reminders`
(`{sid → (stop_ts, fired_count)}`); the escalation is bounded by the
green window (`fresh_sec`) because `reconcile` only ever considers
`RenderGroup.FRESH` sessions.

### The usage sensor — a new data source via `statusLine`

Subscription usage (spec 0011) needed a source the plugin couldn't reach:
the Claude.ai `rate_limits` are exposed by Claude Code **only** on the
`statusLine` command's stdin — not in transcripts, not in hook payloads.
So `hooks/usage-sensor.sh` is wired in as the user's `statusLine` (by
`setup`): it parses `rate_limits.five_hour` / `seven_day`, atomically
writes the one-row `agent-state.usage` snapshot
(`record_ts  five_used  five_resets_at  seven_used  seven_target`), then
**chains** to the user's original `statusLine` command (saved in
`agent-state.statusline.orig`, restored by `teardown`) so their status line
still renders. This is the same hook→sidecar→tick shape as ADR-0003, with
the statusLine as the writer — see
[ADR-0018](./docs/adr/0018-usage-sensor-statusline-chain.md).

The data is **account-wide** (not per-session), so the snapshot is a single
row. `claude_agents_bar/usage_alerts.reconcile(now)` runs each tick (right
after the idle reconcile): it reads the snapshot and fires
`hooks/notify-usage.sh` (another non-registered, plugin-fired notifier, like
`notify-idle.sh`) when `five_used` first crosses 50/60/70/80/90 % / 95 %,
recording progress in `agent-state.usage-alerts`
(`five_resets_at  max_threshold_fired`, keyed by the window so a rollover
resets it). Both `reconcile` and the grey Tools usage line
(`render._print_usage_line`) gate on `now < five_resets_at` to skip a stale
snapshot. The weekly pacing target shown in the Tools line is computed by
the sensor (a `WK_CUM` lookup), not the plugin.

[cc-hooks]: https://code.claude.com/docs/en/hooks.md

The two action scripts that also write into `~/.claude/`
(`bin/open-session.sh` against `agent-state.clicks`, `bin/forget-sessions.sh`
against both TSVs) use the same `mkdir`-based scheme, each against its
own lock directory (`<file>.lock.d`). The plugin acquires those locks
through `_sidecar_lock` and `_clicks_lock` for its garbage collectors.

Treat the hook as performance-sensitive (it runs many times per session)
and keep dependencies to coreutils + `jq`. The payload is parsed by
exactly one `jq` invocation — adding more would visibly slow hot paths.

If you add new events:

1. Add the matcher to `hooks/settings-hooks.json`.
2. Re-run `bash install.sh` to merge it into `~/.claude/settings.json`.
3. Check `tail -f ~/.claude/agent-state.tsv` to confirm rows update.

## Tests

A small `unittest` suite under `tests/` covers the pure helpers and the
config loader — everything that doesn't depend on the user's filesystem
state. Stdlib only, no external test runner needed.

```bash
# Run the suite under the same Python SwiftBar invokes.
/usr/bin/python3 -m unittest discover -s tests -v
```

What's covered:

| Area | Test class |
|---|---|
| Title truncation + whitespace collapse | `TestShorten` |
| Duration formatting | `TestHumanizeAge` |
| State → render group mapping (ACTIVE / FRESH / ACKNOWLEDGED / STALE) | `TestClassify` |
| Slash-command / XML cleanup | `TestCleanText` |
| Content flattening (string / list / mixed) | `TestContentToTitle` |
| Project-name derivation (cwd vs slug fallback) | `TestProjectName` |
| Interactive-entrypoint predicate | `TestPredicates` |
| State TSV parsing (malformed rows skipped) | `TestParseSidecar` |
| Clicks TSV parsing | `TestParseClicks` |
| `ack_fresh` bulk-promote logic | `TestAckFresh` |
| `read_dismiss_ts` (missing / valid / garbage file) | `TestReadDismissTs` |
| Config defaults / overrides / coercion / bad values | `TestConfigLoad` |
| `menubar_icon` prefix handling (plain / `sf:` / `template:` / `image:` / fallback) | `TestMenubarIconPieces` |
| SwiftBar param quoting | `TestSwiftbarQuote` |
| Subagent hook routing (parent vs subagent TSV) | `TestAgentStateHookSubagentRouting` |
| Subagent TSV parser + GC | `TestSubagentSidecar` |
| Parent state rollup from live subagents | `TestSubagentRollup` |

Filesystem-bound paths (`read_transcript_meta`, `current_git_branch`,
`gc_sidecar`, `gc_clicks`) aren't covered by unit tests — they're
exercised by the smoke checks below and by everyday usage.

## Smoke checklist before pushing

```bash
# 1. Syntax OK under SwiftBar's Python.
/usr/bin/python3 -m py_compile claude-agents.5s.py

# 2. Unit tests pass.
/usr/bin/python3 -m unittest discover -s tests

# 3. Plugin output is well-formed (first line is the title, then ---).
/usr/bin/python3 claude-agents.5s.py | head -3

# 4. No traceback in any rendering path. The catch-all in main() is a safety
#    net, not a substitute for actually exercising the code.
/usr/bin/python3 claude-agents.5s.py > /dev/null && echo OK

# 5. Hook smoke test (writes a phantom row then strips it).
echo '{"session_id":"_dev-test","cwd":"/tmp","hook_event_name":"SessionStart"}' \
    | hooks/agent-state.sh working
grep '^_dev-test' ~/.claude/agent-state.tsv && \
    sed -i '' '/^_dev-test\t/d' ~/.claude/agent-state.tsv

# 6. Live: trigger a refresh and click around in the menu.
open "swiftbar://refreshallplugins"
```

If you're shipping a destructive action, also test the **cancel** path —
osascript dialogs return non-zero on `Cancel`, which `set -e` will
propagate unless wrapped in `|| true`.

## SwiftBar cheat sheet

Reference for the SwiftBar output conventions actually used in this
plugin. Full docs: <https://github.com/swiftbar/SwiftBar/wiki/Plugin-API>.

### Line shape

```
<text> | <param>=<value> <param>=<value> …
```

The first line of a plugin's output is the *menu-bar title*. Each
subsequent line is one menu item. A literal line of three or more
dashes (`---`) is a section separator. A line that starts with `--` is
a **submenu item** for the most recent main row; `-- -----` is a
separator inside a submenu.

### Params used here

| Param            | Effect                                                                  |
|------------------|-------------------------------------------------------------------------|
| `href=<url>`     | Click opens the URL via `open` (we use `vscode://…`)                    |
| `shell=<cmd>`    | Click runs `<cmd>` with `paramN=<arg>` as argv[N+1]                     |
| `param0`, `param1`, … | Positional arguments passed to `shell=`                            |
| `terminal=false` | Don't open Terminal for the shell action                                |
| `refresh=true`   | Refresh the plugin after the action completes                           |
| `color=<hex>`    | Foreground colour of the whole row                                      |
| `font=<name>`    | Font for the row (we use `Menlo` so the timestamps align)               |
| `ansi=true`      | Honour ANSI SGR escapes inside the text                                 |
| `sfimage=<sym>`  | Leading SF Symbol icon (e.g. `trash.fill`)                              |
| `sfcolor=<col>`  | Tint colour for the SF Symbol (e.g. `systemRed`)                        |
| `image=<b64>`    | Leading bitmap icon, base64 PNG — keep small; long titles get truncated |

### Quoting

Values containing spaces or special chars must be wrapped in
`"double quotes"`. Embedded double quotes have to be neutralised
because SwiftBar's lexer parses param values before the shell sees
them. Use `_swiftbar_quote()`.

### Tips that bit us

* The menu-bar title (first line) silently truncates when too long;
  keep base64 images out of it. SF Symbols + emoji are crisp at every
  DPI.
* `sfimage=` only renders in recent SwiftBar builds — older versions
  show nothing.
* ANSI escapes are printed literally unless the row also carries
  `ansi=true`.
* Submenu separators are `-- -----` (the `--` prefix keeps them inside
  the submenu); a bare `-----` would land on the outer menu.

## Common gotchas

- **SwiftBar scans subdirectories of its plugin folder.** Don't keep this
  project inside the plugins dir; `install.sh` refuses to run if you do.
- **`sfimage=` works in submenu items but only in recent SwiftBar
  versions.** If you see a blank glyph, fall back to an emoji or
  Unicode glyph (`⎇` for branch, `🗑` for delete).
- **Title-line length matters.** SwiftBar truncates very long menu-bar
  titles silently — keep base64-encoded images out of the title. SF
  Symbols and emoji are the right tool there.
- **ANSI escapes need `ansi=true`** on the same menu item, otherwise
  they're printed as literal `^[[33m…` text.
- **Hooks must be fast.** They run inline with every Claude Code event.
  `agent-state.sh` is ~5 ms; if you add work, keep that budget.

## Compatibility matrix

| Component | Tested with |
|-----------|-------------|
| macOS | Sequoia 15.x (Darwin 25) |
| Python | 3.9.6 (system) — also runs on 3.10–3.14 |
| SwiftBar | 1.5.x |
| Claude Code | 2.1.139 (extension), 2.1.112 (CLI) |
| `jq` | any |
