# Changelog

All notable changes to ClaudeAgentsBar are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Architectural rationale for each piece below lives in [docs/adr/](./docs/adr/).

## 1.5.0 — 2026-08-27

### Changed

- **Read-only menu rows no longer highlight under the cursor.** Branch,
  model, context-left, IDE group, subagent trails, the usage lines under
  *Statistics* and the *Notifications* / *Keep awake* status headers were
  selectable and took keyboard focus even though clicking them did nothing.
  Cause: SwiftBar attaches a click action to every row carrying `color=` (it
  needs one to repaint a custom colour against the selection highlight), so
  the grey was buying the highlight. They now get their grey from ANSI
  instead, which costs no action — same colour, no phantom selection. Rows
  that actually do something (project, cwd, session rows) are unchanged, as
  are the status colours that mean something: a cwd collision, a worktree
  checkout, a subagent in flight.

### Added

- **IDE session groups in the menu.** Claude Code's editor extension
  (2.1.241+) lets you file sessions into named groups in its sidebar; the bar
  now mirrors that grouping, in one of three shapes set by the new
  **`ide_groups_mode`** knob:

  * `"submenu"` — one top-level entry per group, in the sidebar's
    own order, each header carrying a counter per live state
    (`🟡 🟢2 · Backend`) so a folded group still says whether anything in it
    needs you; a count of one is left off, since the circle already says it.
    Its sessions sit inside as ordinary rows, with every row action intact.
    Ungrouped sessions follow below a plain separator — no label, since
    the gap already says they belong to no group.
  * `"inline"` (default) — the flat list, with the group name prefixing the row
    title
    (`backend · Release 1.4.2`, dimmed and truncated to 16 characters) plus a
    submenu line carrying it in full under *Tags*.
  * `"off"` — no grouping, and the lookup is skipped entirely: no editor
    database is opened at all.

  **Read-only.** Creating, renaming, moving, and collapsing stay in the IDE —
  the data lives in the editor's own globalState database, which VS Code keeps
  in memory and rewrites wholesale, so anything the bar wrote there would be
  silently clobbered. A rename in the sidebar lands in the menu on the next
  tick. See [ADR-0019](./docs/adr/0019-ide-groups-read-only-globalstate.md) and
  [spec 0015](./docs/specs/0015-ide-session-groups.md).

  The mode is switchable live from **Tools → Grouping** ("As in the
  extension" / "Name only" / "Off"), which writes
  `~/.claude/agent-state.ide-groups.mode`; that sidecar wins over the config
  knob, leaving `ide_groups_mode` as the first-launch default — the same
  arrangement *Keep awake* and *Multi-workspace mode* use. New action script
  `bin/app/ide-groups-set.sh` and a `--ide-groups <mode>` CLI flag behind it.

  Also new: **`ide_state_db_paths`** (default `[]`) for a non-standard install,
  replacing the autodetection that otherwise probes Code, VSCodium, Cursor,
  Windsurf, Positron and their Insiders variants, leading with whichever owns
  your `editor_url_scheme`. A missing, locked, or corrupt database isn't an
  error condition — rows render exactly as they did before the feature existed.

- **Terminal sessions are marked, and clicking one goes to the terminal.** A
  session started in a shell rather than in the editor now carries a grey
  dim `❯` right after its state circle, and its row click no longer fires
  the editor deeplink.
  That deeplink resumed the transcript *in the editor* while the terminal
  process kept running — two live sessions appending to one transcript. The
  row now raises the tab that owns the process (matched by tty through
  Terminal.app / iTerm2 AppleScript), falling back to `tmux attach`, then
  `screen -r`, then a fresh window running `claude --resume <id>` in the
  session's folder when there's nothing live to raise. The process is located
  through the session registry Claude Code 2.1.228+ keeps in
  `~/.claude/sessions/<pid>.json`; without it (or without `jq`) the click goes
  straight to the `claude --resume` fallback.

  No opt-in: the bar knows exactly which sessions are terminal ones, so it
  always routes them correctly, and editor sessions are untouched. One new
  knob, **`terminal_app`** (`"auto"` — iTerm when installed, else Terminal —
  or `"Terminal"` / `"iTerm"`). New action script
  `bin/app/open-terminal-session.sh`, invoked via `/bin/bash` so a lost
  executable bit can't kill the row. First click triggers the macOS Automation
  permission prompt; see [spec 0016](./docs/specs/0016-terminal-sessions.md)
  and [troubleshooting](./docs/troubleshooting.md).

### Changed

- **Subscription usage no longer needs a background `claude` session.** The
  5-hour and weekly numbers used to come off the `statusLine` of a hidden
  `claude` TUI parked in a detached `screen`, recycled every 10 minutes because
  a long-lived session's `rate_limits` go stale — the only way to read them at
  the time. Claude Code now answers a **`get_usage` control request** over the
  SDK control protocol, so the plugin just asks: a ~1.7 s `claude -p` call
  every few minutes that runs **no inference**, spends **no quota**, writes no
  transcript and fires no hooks. Before spawning anything it checks Claude
  Code's own cache of the same response in `~/.claude.json`; when that's
  fresher than the interval, nothing is spawned at all.

  What goes away with it: the background process, the `statusLine` wrapper in
  your `settings.json` (and its save/restore dance), the trusted work folder,
  the pre-seeded onboarding keys, and the whole "the session hung on a
  first-run prompt" failure mode that was the single most common way this
  feature broke. Running `claude-agents-bar setup` after the upgrade retires
  all of it — your original status line is restored and any leftover session
  killed. See [ADR-0020](./docs/adr/0020-usage-via-sdk-get-usage.md).

  `usage_ping_interval_min` and `usage_ping_model` are gone; the replacement is
  **`usage_fetch_interval_min`** (default 3, floored at 1). `usage_monitor`
  still switches the whole feature off.

- **The usage line became two lines, with bars.** One row per window, laid out
  like the usage panel in the Claude Code editor extension:

  ```
  Session  ▓▓░░░░░░░░   22% · 3h
  Week     ▓▓▓▓░░░░░░   41% · 1d
  ```

  Ten cells, percentage and time-to-reset, aligned in fixed columns; bar and
  number turn yellow at ≥60 % and red at ≥85 %, as the single line's numbers
  already did. A non-zero percentage always fills at least one cell, so 4 %
  reads differently from 0 %. A plan with no weekly window gets the session row
  alone.

- **Removed the *Statistics → Usage monitor* checkbox.** It existed to stop a
  background process that spent quota; there is no such process any more, so
  the feature is simply on. If you want it gone entirely, `"usage_monitor":
  "off"` in the config still does that. The `agent-state.usage-monitor.mode`
  sidecar is no longer read (left on disk, like every other sidecar) — so if
  you had the checkbox **off** in 1.4, usage comes back on after the upgrade.
  That's deliberate: what you turned off was the quota cost, and it's gone.

- **Upgrading: re-run `claude-agents-bar setup`.** A `brew upgrade` ships code,
  not wiring — the 1.4 sensor stays wired as your `statusLine` until `setup`
  unwires it, and every session then runs a script this version no longer
  ships. `doctor` now says so explicitly (`[warn] usage/ … still runs the
  retired 1.4 usage sensor`). The background session doesn't wait for you: the
  first usage fetch after the upgrade quits any leftover `cab-usage-mon`
  session and removes its marker, whether or not you ever run `setup`.

### Fixed

- **A group holding a live session wouldn't open its submenu.** In
  `submenu` mode the group header went dead — no highlight under the
  cursor, no submenu — for as long as something inside it was running,
  and came back once everything went idle. SwiftBar rebuilds a menu item
  whenever its label changes, including while the menu is open, and the
  header's label carries the state counters, which a working session
  flips every few seconds. An item with no action of its own is left to
  AppKit's menu validation to enable, and that only runs when the menu
  opens — so the rebuilt header landed in the open menu unvalidated and
  disabled. The header now carries a no-op action, which SwiftBar enables
  itself; it never runs, since AppKit routes a click on a parent item to
  its submenu.

- **The folder line in a session's submenu opens Finder.** It was read-only
  text; clicking it now opens the session's working directory
  (`/usr/bin/open <cwd>`), which is what the line already looked like it
  should do. Applies both to the project-name line of a git checkout and to
  the bare-path line shown when the cwd isn't a repo. The full path is now
  always attached as that line's tooltip — previously it was dropped whenever
  the branch line carried a worktree or collision tooltip, which is exactly
  the case where you can't tell where the click would land. Distinct from
  *Session ▸ Reveal in Finder*, which points at the JSONL transcript.

- **A worktree session's row named the branch, not the project.** The project
  line took the last path segment of the session's cwd — for a worktree that's
  the worktree directory, named after the branch, which the submenu's branch
  line already shows one row below. The row now resolves the owning repository
  through the worktree's `.git` marker and labels it with that instead, so a
  session in `.claude/worktrees/my-branch` reads `ClaudeAgentsBar` with
  `my-branch` beneath it rather than saying the same thing twice.

## 1.4.2 — 2026-08-16

### Added

- **`Session ▸` submenu on every row, with *Copy ID*.** *Reveal in Finder* and
  *Delete…* were flat items on the row; they now sit inside a `Session ▸`
  submenu (*Reveal in Finder* → *Copy ID* → *Delete…*) around a new **Copy
  ID**, which puts the session id on the clipboard with no trailing newline.
  Grouping them costs one hop and buys two things: the row's top level is down
  to the actions you actually reach for, and the destructive one is no longer
  a mis-click away from *Bookmark*. That id is what you hand to another agent ("the session `<id>`
  running in parallel") or to `claude --resume`, and reading it off the menu
  beats digging it out of `~/.claude/projects/`. The id also rides as the
  item's tooltip, so you can see what you're about to copy. Both items act on
  the session as an object rather than on its state, which is why they're
  grouped one level down. New action script `bin/app/copy-session-id.sh`,
  invoked via `/bin/bash` so a lost executable bit can't kill the item.

### Changed

- **Fable now sorts with the Claude families in *Stats → Models*.** 1.4.0 gave
  Fable its ⓕ badge but not a rank in the family-ordering table, so a Fable
  session rendered its glyph and then sorted into the non-Claude tail
  alongside OpenRouter strings. It now ranks after haiku. A test pins the two
  tables together so the next family can't be added to one and not the other.

### Fixed

- **Temp files and settings backups no longer pile up in `~/.claude`**
  (issue #3). Two independent leaks:

  - *Orphaned `agent-state*.<pid>` temp files.* Every sidecar writer does
    `awk … > "$FILE.$$" && mv`. The `&&` is correct — a failed `awk` must not
    clobber the sidecar — but it meant the temp survived whenever `awk` exited
    non-zero, and the EXIT trap only released the lock. The traps in
    `agent-state.sh` (both write paths), `record-click.sh`, `ack-session.sh`,
    `forget-session.sh`, `tag-set.sh` and `bookmark-set.sh` now drop `$TMP`
    too. That closes the error path; a `SIGKILL` between the redirect and the
    `mv` is beyond any trap, so the render tick also sweeps
    `agent-state*.<pid>` files whose owning PID is dead **and** which are older
    than five minutes (the age floor is the guard against PID recycling). The
    state hook runs on every tool-use event, so this accumulated fast — 82
    files in three weeks on the reporter's machine.

  - *Unbounded `settings.json.bak.<timestamp>` copies.* `setup.sh` and
    `teardown.sh` wrote one on every run, unconditionally and without
    rotation — 33 files there, 15 of them from a single install session's
    retry loop. Both merges are deterministic, so they now compare the result
    against the current file and skip **both** the backup and the write when
    nothing changed, then keep only the five newest backups. Rotation runs on
    the unchanged path too, so a machine that already accumulated backups
    clears them on its next `setup` rather than keeping them forever. The same
    guard covers the `~/.claude.json` backup.

- **A failed `awk` no longer publishes a half-written state sidecar.**
  `delete-session.sh` paired `|| true` with an unconditional `mv`, so a
  non-zero `awk` moved whatever landed in the temp file over
  `agent-state.tsv`. It now commits only on a clean `awk` and drops the temp
  otherwise — same shape as the bookmarks block below it.

- **A non-PID numeric suffix no longer breaks the whole menu.** The litter
  sweep passed whatever digits it matched straight to `os.kill`, which raises
  `OverflowError` — *not* an `OSError`, so nothing caught it — above 2^31-1. A
  single `~/.claude/agent-state.tsv.20260513184649` was enough to take the
  exception out of `collect_sessions` and replace the entire menu with the red
  error item on every tick. Suffixes are now screened against the macOS PID
  range (1–99999) before the liveness check, which also stops an out-of-range
  value from reading as "dead" and getting deleted.

- **`setup` / `teardown` back up `settings.json` before *every* write.**
  Routing the hook merge through the new skip-if-unchanged guard left the
  statusLine steps (5b/5c in setup, the restore block in teardown) writing the
  file with no backup at all whenever the merge itself was a no-op — exactly
  the documented re-run-after-upgrade path. Every `settings.json` write now
  goes through the same guard, and the guard refuses to publish when the
  staged file is missing (a failed `jq`) instead of trying to `mv` it.

- **The usage sensor no longer leaks a temp on a failed write.**
  `usage-sensor.sh` removed its temp only when the `mv` failed, not when the
  redirect did — and it runs every ~8 s off the statusLine, the highest write
  frequency in the project.

- **`doctor` reports state-directory litter.** A new `litter/` check counts
  orphaned temp files and `settings.json` backups, warns above ten of either,
  and prints the command to clear the backups. Existing litter predates the
  fixes above and won't clean itself, so the check tells you it's there
  instead of leaving you to notice by eye.

## 1.4.1 — 2026-07-06

### Changed

- **Bookmarks — menu polish.** In the *Bookmarks* list a pinned session's row
  now shows its **pin age** (`3м`) as the right-hand label instead of the live
  wait/idle duration, and the `❓` waiting marker is dropped there — a pinned
  entry answers "how long ago I pinned this", not "how long it's been blocked".
  The passive *Added Xm ago* leaf is gone (the age lives on the row alone, with
  no "Added" wording), and the **Bookmark** checkbox now sits directly under
  *Forget* in every session's submenu. See
  [spec 0012](./docs/specs/0012-bookmarks.md).

### Fixed

- **Deleting a session now clears its bookmark immediately.** *Delete…* removes
  the pin from `agent-state.bookmarks` in the same step it removes the
  transcript and the state row, instead of leaving it for the next tick's
  orphan cleanup to prune.

## 1.4.0 — 2026-07-05

### Added

- **Bookmarks — pin sessions so they don't vanish.** Every session's submenu
  gains a **Bookmark** checkbox; pinned sessions appear under a new
  **Bookmarks** item directly below *Refresh*, each with its full submenu
  (Remind, Forget, Delete…, Reveal in Finder, project/branch, context usage)
  one level deep and a passive *Added Xm ago* leaf. A pin **survives the render
  window**: the row is rebuilt from the transcript on demand, so a session that
  would otherwise drop off the menu after `window_sec` stays reachable.
  Opening a pinned session records a click like any row, so it moves to 🔵
  *acknowledged*. A bookmark whose transcript is deleted auto-prunes. The
  Bookmarks item is hidden while nothing is pinned; no config knob. See
  [spec 0012](./docs/specs/0012-bookmarks.md).

- **Session tags — a colored marker on any session.** Each session's submenu
  gains a **Tags ▸** picker of seven colors (Red, Orange, Yellow, Green, Blue,
  Purple, White); pick a color to tag the session, re-pick the same color to
  clear it. A tagged session shows a small colored circled letter (`ⓡⓞⓨⓖⓑⓟⓦ`,
  the letter mirroring the color) right after its state circle, in the live
  list and inside a Bookmarks entry. The color rides in an **ANSI** escape,
  not `sfcolor` — SwiftBar renders ANSI text color in the dropdown but won't
  tint SF Symbols. One color per session (a new color replaces the old).
  Stored as a stable color key in `agent-state.tags`; auto-prunes when the
  transcript is gone; always on, inert until used, no config knob. See
  [spec 0013](./docs/specs/0013-tags.md).

### Fixed

- **Fable now shows a ⓕ badge, not the ⓜ fallback.** The model-family badge
  table had no `claude-fable-` prefix, so a Fable session fell through to the
  generic ⓜ ("other model") glyph in the row badge and the *Stats → Models*
  list. Added the family so it renders ⓕ.

- **Renaming a session in VSCode/VSCodium now shows the new name in the menu.**
  Claude Code records a manual rename as a `custom-title` event; the menu read
  only the auto-generated `ai-title` and ignored it, so a renamed session kept
  its stale title. `custom_title` now ranks above `ai_title` (mirroring what
  the editor sidebar shows) and is read latest-from-tail, so a second rename
  wins.

- **Duplicate notifications from overlapping ticks.** SwiftBar runs the plugin
  concurrently — the scheduled 5-second tick plus any
  `swiftbar://refreshallplugins` a hook or menu action fires — so two ticks
  could overlap. The usage-alert and idle-reminder reconcilers read their
  progress sidecar, decided to fire, and wrote back in three separate steps with
  the lock guarding only the write, so two overlapping ticks could both observe
  the same counter and fire the same alert twice (a double banner + double
  speech — e.g. *"Session limit at 53%"* spoken back-to-back). Both reconcilers
  now hold the sidecar lock across the whole read→decide→fire→write, so the
  second tick observes the first's write and stays quiet.

## 1.3.0 — 2026-06-29

> **After `brew upgrade`, run `claude-agents-bar setup` once** to enable the
> usage monitor — it wires the status-line sensor, the `refreshInterval`, and a
> trusted work folder into `~/.claude` (things a plain upgrade doesn't touch).
> Existing features keep working without it.

### Added

- **Subscription usage monitor — live 5-hour/weekly usage in the menu, with
  alerts.** A new *Statistics* item in the main menu carries a grey, passive
  line mirroring Claude Code's own USAGE view —
  `Session: 24% · 2h · Week: 8% · 4d` — with the session/week percentages
  turning **yellow past 60 %, red past 85 %**. You also get a one-shot
  notification (chime + spoken phrase + banner) when the 5-hour window first
  crosses 50/60/70/80/90 % — *"Session limit at N%"*, the 70 %+ ones quoting the
  hours left — and a distinct critical alert at 95 %. Each threshold fires once
  per window; a fresh window alerts from 50 % again; a jump across several
  thresholds collapses to one banner. Alerts honor quiet hours and *Banner
  only* and share the speech lock; silence them alone with
  `notify_on_usage: false`.

  **How it works (and the catch).** Claude Code exposes the usage figures
  (`rate_limits`) **only** to an interactive terminal status line — never to
  `claude -p`, headless runs, or the VSCode extension. So the monitor holds a
  **hidden background `claude` session** in a detached `screen` (no window, a
  real TTY) and reads the usage off its status line via a bundled sensor. The
  plugin watches that session on its tick and **recycles** it (kill + fresh
  spawn) every 10 minutes — a fresh session's first response is what pulls
  current usage from the server (account-wide, so it catches your VSCode work
  too). It's **on by default and zero-config** (`setup` trusts the work
  folder), but it runs a real (window-less) `claude` (Haiku) and spends a little
  quota, so one click in *Statistics → Usage monitor* turns the whole thing off.
  `teardown` reverses everything.

  Because the `statusLine` is global, the sensor writes the sidecar **only for
  the daemon's own session** (gated on its work-folder cwd) — otherwise an old
  session reopened via resume, carrying a stale cached `rate_limits`, would
  clobber the live number and the menu would flap. `setup` also pre-seeds the
  `~/.claude.json` keys that gate Claude Code's first-run prompts
  (`fullscreenUpsellSeenCount`, `hasCompletedOnboarding`) so the background
  session doesn't hang on the *"Try the new fullscreen renderer?"* upsell, and
  the monitor collapses any duplicate background sessions down to one.
  `claude-agents-bar doctor` gained a `usage/` check that reports monitor health
  and, if the background session is up but no data is arriving, names the fix.
  New knobs: `usage_monitor`,
  `usage_ping_interval_min`, `usage_ping_model`, `notify_on_usage`,
  `notify_usage_title`, `notify_usage_phrase_threshold` /
  `_threshold_reset` / `_critical`, `notify_sound_usage`. See
  [spec 0011](./docs/specs/0011-usage-alerts.md) and
  [ADR-0018](./docs/adr/0018-usage-sensor-statusline-chain.md).

### Changed

- **Idle-reminder default is now 30 minutes** (was 20) — the first "look at
  this" nudge for a finished-but-unread session fires after 30 min instead of
  20. Override with `notify_idle_interval_min` (`0`/`null` to disable).

## 1.2.2 — 2026-06-15

### Fixed

- **A Homebrew install no longer breaks on `brew upgrade`.** `setup`
  symlinked the SwiftBar plugin and the Claude Code hooks at the
  *versioned* Cellar keg path (`…/Cellar/claude-agents-bar/<version>/…`).
  The next `brew upgrade` deletes the old keg, so those symlinks dangled —
  the plugin vanished from the menu bar and hooks stopped firing until
  `setup` was re-run. `setup` now re-anchors at the stable `opt` prefix
  (`$HOMEBREW_PREFIX/opt/claude-agents-bar`, which Homebrew repoints to the
  current version on every upgrade), so a Homebrew install survives
  upgrades with no re-run. Run `claude-agents-bar setup` once after
  upgrading to this version to convert existing symlinks; git-clone installs
  are unaffected. See [ADR-0017](./docs/adr/0017-symlink-homebrew-opt-not-cellar.md).

- **Spoken notifications no longer talk over each other.** Every notify hook
  (Stop, awaiting, idle) and the *Remind* click spawned its own background
  `say(1)`, so two events landing in the same moment — a session finishing
  while an idle nudge fired for another — spoke simultaneously and became
  unintelligible. Speech is now serialized through a shared cross-process
  lock (`_say_lock_acquire` / `_say_lock_release` in `hooks/_notify-common.sh`,
  an atomic `mkdir` mutex under `~/.claude/` since macOS has no `flock`): only
  one `say` speaks at a time, with a configurable pause between utterances
  (**`notify_say_gap_sec`**, default 1 s). A notification that waits for the
  lock longer than **`notify_say_stale_sec`** (default 30 s) is dropped
  unspoken, so a backlog of stale announcements doesn't queue up. Only speech
  is locked — the chime and banner still fire in parallel. The lock is
  crash-safe: a dead holder's lock is stolen by the next waiter. See
  [spec 0010](./docs/specs/0010-speech-lock.md).

- **A catch-up after sleep no longer double-fires the idle reminder.** When the
  machine slept across several escalation thresholds, `idle_reminders.reconcile`
  fired one `notify-idle.sh` per missed threshold in a single tick — so waking
  up could greet you with two back-to-back banners + chimes (and, before the
  speech lock above, two overlapping voices) for the same session. The catch-up
  now collapses to a **single** reminder: the escalation counter jumps straight
  to the current level but only one notification fires per tick. Normal
  one-at-a-time scheduling is unchanged. See
  [spec 0008](./docs/specs/0008-idle-reminders.md).

## 1.2.1 — 2026-06-15

### Fixed

- **Clicking a completion banner now clears the session's 🟢 unread state.**
  Opening a finished session by clicking its notification banner resumed it
  in the editor but left the menu row green (🟢 FRESH) until `fresh_sec`
  elapsed — only a menu-row click recorded the acknowledgement. The banner
  click path bypassed the ack write entirely (a direct deeplink open in
  single-window mode, `raise-and-open.sh` without an ack write in
  multi-workspace mode). The ack write is now a single shared helper
  (`hooks/record-click.sh`) that both paths go through, so a banner click
  promotes the session 🟢 → 🔵 on the next tick just like a row click. This
  was a latent bug present since clickable banners were introduced, not a
  recent regression.

## 1.2.0 — 2026-06-13

### Added

- **Session titles from the response marker (opt-in).** The summary line your
  assistant ends each reply with is now **two-field** —
  `*-- Name - Summary*` (the `notify_summary_marker` prefix, then a `" - "`
  divider). With the new **`use_session_titles_for_menubar`** knob set to
  `true`, the menu shows the **name** as the session title — your own
  context-aware wording (e.g. Russian) in place of Claude Code's
  auto-generated English `ai-title`. **Default `false`**: the menu keeps
  showing `ai-title`, the same label VSCode displays, so the menu stays
  consistent with the editor; when off the per-tick title parse is skipped
  entirely. Either way the marker is parsed for the *spoken* notifications
  (its primary purpose — see below), independent of this knob. When the
  title parse does run it's gated on `notify_summary_marker` and byte-
  prefiltered to assistant lines, so it stays cheap. See
  [spec 0007](./docs/specs/0007-session-title.md).
- **Awaiting notifications now name the session.** A `PermissionRequest`
  (permission prompt) used to speak only a random "your turn" phrase. It now
  also reads the blocked session's **name** and **summary** (phrase → name →
  summary), and the banner shows `name — summary`, so you can tell by ear
  which of several sessions needs you and what it was doing. Pulled from the
  last completed marker turn (the in-flight turn hasn't closed with its marker
  yet); no marker / marker disabled falls back to the phrase alone.
- **Remind (re-speak the last summary).** Every session row's submenu now
  leads with a *Remind* item (speaker icon) that speaks aloud, via `say`,
  that session's last spoken-summary line — the text after
  `notify_summary_marker` (default `"-- "`) on the assistant's final reply,
  the same line the Stop notification reads. It reuses the notification
  voice (`notify_voice`), so a reminder sounds identical to the original.
  The transcript is parsed **on click**, never on the render tick — the menu
  only checks whether a marker is configured: the item is enabled when one is
  set, greyed-out when it's been disabled (`null`/`""`). If the marker is on
  but the latest reply carried no summary line, the click speaks a localised
  "configure Claude to end replies with a summary line" hint instead of going
  silent. Being an explicit click, it speaks even under *Banner only* mode or
  `notify_voice: "off"` (which only mute the *automatic* speech). Localised
  across all shipped locales. With the new `remind_recap_after_min` knob, a
  click on a session that's gone quiet for at least that many minutes also
  speaks the session's **opening** summary first (then the latest), so you can
  recall what a cold thread was about before where it is now; while you're
  still in the flow it stays on the latest only. Unset (default) → latest only.
- **Notification mode in the menu** — *Tools → Notifications* now has a
  *Banner and voice* / *Banner only* radio pair. *Banner only* mutes the
  chime **and** the spoken `say` for every notification while keeping the
  banner; *Banner and voice* restores them. The choice is a standing
  preference (not time-bound like quiet hours), stored in a sidecar at
  `~/.claude/agent-state.notify-audio.mode` that overrides the new
  `notify_audio` config knob (default `true`) — the same
  first-launch-default-then-sidecar precedence as *Multi-workspace mode*.
  Localised across all shipped locales.
- **Idle-session reminders.** A finished session that sits 🟢 green (unread —
  you never clicked it) past a configurable interval is now re-announced —
  chime + spoken phrase + clickable banner, like an awaiting prompt — with its
  own sound (`notify_sound_idle`, default `Submarine`) and phrases
  (`notify_idle_phrases`). The schedule **doubles** from when the session
  finished: 20, 40, 80, … minutes (`notify_idle_interval_min`, default 20).
  How many fire is bounded by how long the row stays green (`fresh_minutes`,
  default 60) — with the defaults you get two reminders (20 and 40 min).
  Clicking the session (or *Tools → Acknowledge all*) ends the schedule; a new
  finished turn restarts it. There's no Claude Code "N minutes after Stop"
  event and the plugin runs no daemon, so the reminder rides the 5-second
  SwiftBar tick: `claude_agents_bar/idle_reminders.py` checks the green-and-
  unread sessions each tick and fires the new (non-hook) `hooks/notify-idle.sh`,
  tracking progress in `~/.claude/agent-state.idle-reminders`. Respects quiet
  hours and *Banner only*. Set `notify_idle_interval_min` to `0` / `null` to
  turn it off. See [spec 0008](./docs/specs/0008-idle-reminders.md).
- **Three new row indicators.** A waiting row's right-hand label now reads
  *waiting {duration}* (e.g. *waiting 6m*) instead of a bare red duration, so a
  blocked session names its state. A **cwd collision** — two or more active
  sessions sharing the same `cwd` — is flagged two ways: a red `⎇` branch glyph
  on the main row between the title and the duration (the same branch icon the
  notification banner uses, spec 0009), and the submenu branch name turned red
  with an "another session is working in this folder" tooltip.
  A session whose checkout is a git **worktree** gets a green `ⓦ` marker on
  the main row (right after the collision glyph, before the duration) *and* its
  submenu branch name turned green, both signalling "changes isolated under
  worktree". The inline `ⓦ` turns red when the worktree is *also* a collision
  (two sessions sharing one worktree checkout) and the branch glyph is then
  dropped, so the row shows the single red `ⓦ` rather than two red markers.
  (Submenu colour rides on the branch *text* — SF Symbols in a submenu
  render monochrome, so the icon can't carry it.) Branch-line priority is
  collision > worktree > plain. All three are localised across every shipped
  locale.

### Changed

- **Stop speech reads the summary field only.** With the two-field marker, the
  `Stop` notification (and the *Remind* action) speak the **summary** — the
  text after the `" - "` divider — not the whole line, so the session name
  isn't read aloud as part of the summary.
- **Spoken segments now pause between parts.** When `say` reads more than one
  part (phrase, name, summary) it inserts a short silence between them — a
  `[[slnc]]` command, 100 ms — so they land as separate beats instead of one
  run-on breath ("Done. … Migrated the auth module"). A hook constant in
  `_notify-common.sh`, not a config knob.
- **Notification hooks share one emit path.** The chime + speech + banner
  tail and the random-phrase picker — previously copy-pasted between
  `notify-stop.sh` and `notify-wait.sh` — are now `_emit_notification` /
  `_pick_phrase` in `hooks/_notify-common.sh`. Both hooks (and the new
  `notify-idle.sh`) are thin shims over them; behaviour is unchanged.
- **Reshuffled the notification banner's three lines** so each carries
  information (spec 0009). The fixed status labels *Claude awaiting input* /
  *Claude session unread* are gone from line 1; awaiting and idle now show
  their random phrase there, prefixed with a coloured type emoji (❓ awaiting,
  ⚠️ idle) — the banner is plain text, so an emoji is the only way to colour
  the type. Line 2, previously the constant *Claude Code*, now shows the
  session's `<project> — <icon> <branch>` (read from its working dir,
  matching the row's submenu), where `<icon>` is `ⓦ` for a worktree or `⎇`
  for an ordinary branch. Line 3 is just the marker `name — summary` (the random
  phrase no longer leaks into it, and it's empty when there's no marker). Stop
  is unchanged — its line 1 stays the `ai-title`. The emoji is banner-only;
  `say` never speaks it.

### Fixed

- **Clicking a session in a freshly-opened, still-empty folder now lands in
  the right window.** With `multi_workspace_mode` on, `hooks/raise-and-open.sh`
  surfaces the owning window by opening a file inside the session's cwd — but
  a brand-new window whose folder has no files yet had nothing to anchor on,
  so the click fell through to a bare deeplink and resumed in whatever window
  was frontmost (the wrong one). It now falls back to opening the **folder**
  itself (`open -a <editor> <cwd>`), which focuses the single-folder window
  already holding it instead of spawning a new one — no extra tab, no
  Accessibility permission. (A cwd that is one root of a multi-root workspace
  *and* file-empty can still spawn a new window per
  [VS Code #215749](https://github.com/microsoft/vscode/issues/215749), but
  that combination is rare and the prior behaviour was no better.)
- **Session title no longer flickers mid-turn** — in sessions whose first
  message carried large pasted attachments (images → big base64), the
  AI-generated title sat past the 256 KB head-scan window, so the row fell
  back to the latest user prompt. Under heavy tool output that prompt slid
  out of the 128 KB tail window and the title briefly jumped to the
  original first message, then snapped back when the turn ended. The reader
  now recovers the title from the tail (Claude Code re-emits `ai-title`
  every turn, so a fresh one is almost always there), keeping the row
  stable.

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
tool-results directory — each under a localized label (`Transcript:`
/ …, `Tool artifacts:` / …). Paths are shown with `$HOME` collapsed to `~` so they stay
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
"Delete…" / … in all six. New `bin/forget-session.sh`
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
