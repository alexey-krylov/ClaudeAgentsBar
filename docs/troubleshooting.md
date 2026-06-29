# Troubleshooting

## First: run `doctor`

Before working through the sections below, run:

```bash
claude-agents-bar doctor
```

It surfaces the most common breakages — missing dependency, hooks not
registered, sidecar TSV gone stale, SwiftBar plugin symlink missing,
the configured editor scheme pointing at an app that isn't installed —
with a `[warn]` / `[err]` prefix per check. If everything reads `[ok]`
and the plugin is still misbehaving, fall through to the specific
sections below.

## Icon not showing on a notched MacBook

On notched MacBooks (14"/16" Pro, the redesigned Air) the menu bar is
split in two by the camera housing. Only the strip to the **right of
the notch** is available for status items, and macOS lays them out
right-to-left. When you have many third-party menu-bar apps installed,
the bar fills up and items that don't fit are silently clipped behind
the notch — they're still running, just not drawn. ClaudeAgentsBar is a
normal SwiftBar item and is subject to the same clipping.

**Symptoms:**

- The plugin appears to "do nothing" — no icon, no counters.
- Other menu-bar apps you've added recently are also missing or only
  partially visible.
- Quitting one of those apps suddenly makes the ClaudeAgentsBar icon
  reappear on the right.

**Confirm the plugin itself is healthy** before chasing display issues:

```bash
/usr/bin/python3 ~/Projects/ClaudeAgentsBar/claude-agents.5s.py | head -40
```

If that prints the icon line and a session list, the plugin is working
correctly — only the menu-bar rendering is hiding it.

**Fixes** (in order of effort):

- **Turn on compact mode** (`"compact": true` — see
  [Compact mode in configuration.md](./configuration.md#compact-mode)).
  Drops ~30 px from the plugin's footprint by hiding the icon and
  swapping `🟡🟢🔵` for narrow ANSI `●` bullets. Cheapest fix; only
  costs you the Claude mark on the bar.
- **System Settings → Control Center.** Set indicators you don't use
  (Spotlight, Stage Manager, Screen Mirroring, Focus, Bluetooth,
  AirDrop, Sound, Now Playing, Fast User Switching, etc.) to *Don't
  Show in Menu Bar*. Each removed icon frees one slot to the right of
  the notch.
- **Quit or uninstall menu-bar apps you no longer need.** Cloud-sync
  clients, screenshot utilities, and update agents are common
  offenders.
- **Use a menu-bar manager** to hide overflow items behind a toggle:
  [Ice](https://github.com/jordanbaird/Ice) (free, open source),
  [Bartender](https://www.macbartender.com/), or
  [Hidden Bar](https://github.com/dwarvesf/hidden-bar). Pin
  ClaudeAgentsBar to the always-visible group so it never falls into
  the hidden bucket.

## Icon missing even without a notch — too many menu-bar apps

Same failure mode as the notch case, different cause. macOS doesn't
wrap menu-bar items to a second row: once status icons fill the strip
to the right of the active app's menu titles, anything that doesn't
fit is silently clipped — running but undrawn. On a non-notched
display this happens when you've accumulated a lot of third-party
status apps (cloud-sync clients, password managers, clipboard
utilities, system monitors, update agents…), especially under apps
with wide localised menu titles.

**Symptoms** are identical to the notched case: `doctor` is happy,
the standalone plugin run prints valid output, but the icon never
shows up on the bar.

**How to confirm it's clipping, not a plugin bug:**

1. Quit the SwiftBar app entirely, then relaunch it. If the icon
   appears briefly and then disappears as another app loads, it's
   clipping.
2. Or just start disabling menu-bar items one by one (right-click →
   *Quit*, or *System Settings → Control Center* for Apple-managed
   indicators). The moment ClaudeAgentsBar reappears, the previous
   one was the straw breaking the bar.

**Fixes** are the same as in the notched section above: compact mode,
prune Control Center entries, quit dead-weight menu-bar apps, or use
[Ice](https://github.com/jordanbaird/Ice) / Bartender / Hidden Bar to
push overflow behind a toggle.

## Plugin appears stuck or empty

Run the plugin standalone:

```bash
/usr/bin/python3 ~/Projects/ClaudeAgentsBar/claude-agents.5s.py | head -40
```

What to look for:

- **First line** is the menu-bar title (icon + counters or just `●`
  bullets in compact mode).
- **`---`** separator.
- **One line per session** active in the last `window_minutes`.

If the output is well-formed but SwiftBar shows nothing, the issue is
either (a) menu-bar clipping (see the notch and overflow sections
above) or (b) SwiftBar hasn't picked up the plugin yet — force a
refresh:

```bash
open "swiftbar://refreshallplugins"
```

If the output itself errors or is empty, check:

- `defaults read com.ameba.SwiftBar PluginDirectory` — does the
  reported directory contain a symlink named `claude-agents.5s.py`?
- `ls -la ~/.claude/agent-state.tsv` — does the sidecar file exist
  and is it being updated?
- `tail ~/.claude/agent-state.tsv` after a Claude Code session emits
  any event — should show one row updated within seconds.

## Session deleted in VSCode still appears in the menu

When you delete a session from the Claude Code extension sidebar, the IDE
does **not** remove the transcript file — it marks the session as hidden
inside its own internal database. ClaudeAgentsBar reads the transcript files
directly and has no access to the editor's internal state, so the session
keeps showing up until it ages out of the `window_minutes` cutoff.

To remove it immediately, open the session's submenu and choose **Forget**.
This records a cutoff timestamp in the per-session sidecar and the row
disappears on the next tick. It is the editor-agnostic equivalent of the
IDE's "delete" action.

## Rows for sessions started before install

Sessions started **before** the hooks were registered have no entries
in `agent-state.tsv`. They still appear in the dropdown (the plugin
falls back to JSONL mtime) but always show as plain `idle`. Once the
session emits its next hook event — typically the next tool call or
the next `Stop` — it picks up live state.

## Clicking a row does nothing (VSCodium / Cursor / Code-OSS forks)

The default deeplink uses the `vscode://` scheme. VSCodium, Cursor,
and other Code-OSS forks don't register that scheme — they register
their own. Set `editor_url_scheme` in `config.json` to match:

```json
{ "editor_url_scheme": "vscodium://" }   // VSCodium
{ "editor_url_scheme": "cursor://" }     // Cursor
```

The editor also needs the `anthropic.claude-code` extension installed
for the deeplink to actually land on a session. Stock VSCode ships
with the extension store; VSCodium and Cursor users typically install
it from a VSIX or open-vsx. See
[configuration.md](./configuration.md#all-fields) for all schemes.

## Clicking a session opens the wrong window (several windows open)

The session deeplink carries only the session id; the editor's
`anthropic.claude-code` handler delivers it to whichever window is
**frontmost**, not the one whose workspace matches the session. With
two or more windows open, both a menu-bar dropdown row and a *Stop* /
*awaiting input* notification banner used to land the session in the
wrong one (and the resume can silently miss, because the session must
belong to the focused workspace).

Both click paths work around this through the shared
`hooks/raise-and-open.sh`: when the session's working directory and a
known editor `.app` are available, the click first surfaces the window
that owns that directory, waits for it to actually come to front, then
fires the deeplink.

It surfaces the window by opening a **file** inside the cwd
(`open -a <editor> <file>`), **not** the folder. The distinction is the
whole trick: opening a *folder* (`open -a <editor> <cwd>` / `code
<cwd>`) is folder-identity based — if the cwd is one of the roots of an
already-open **multi-root workspace**, it spawns a brand-new
single-folder window instead of focusing the existing one
([VS Code #215749](https://github.com/microsoft/vscode/issues/215749)).
Opening a *file* sends an "open document" event that the editor routes
to the window whose workspace already contains it, multi-root included —
and via LaunchServices, so it skips the ~1 s editor-CLI startup and
stays near-instant (no "is more than one window open?" check needed).

The anchor file it opens is the **last file Claude touched in that
session** (the newest tool-use `file_path` inside the cwd, read from the
session transcript) — so the tab you land on is the one the work was
about. It falls back to a stable project file (`README`, …) when the
transcript has no usable path. The trade-off is one extra editor tab.

When the cwd holds **no file at all** — a folder you just opened in a
fresh window and haven't created anything in yet — it instead opens the
**folder** (`open -a <editor> <cwd>`). For a single-folder window the
editor focuses the window already holding that folder rather than
spawning a new one, so this still lands you in the right window (and
without an extra tab). The one edge it can't cover: if that cwd is one
root of an already-open multi-root workspace, opening the folder spawns
a new single-folder window ([VS Code #215749](https://github.com/microsoft/vscode/issues/215749)) —
unlikely for a brand-new empty folder, which is single-root in practice.

This only kicks in for the built-in schemes (`vscode://`, `vscodium://`,
`cursor://`, `windsurf://`, `positron://`) whose `.app` and CLI the
plugin knows. If the editor CLI can't be resolved or a custom
`editor_url_scheme` is set, it falls back to opening the deeplink
directly (which lands in the frontmost window).

## "Configuration…" opens TextEdit, not my editor

`open -t` follows the system *Default text editor* binding. To change
it, right-click any `.json` file in Finder → *Get Info* → *Open with*
→ pick your editor → *Change All…*.

## No banner / no chime / no voice on `Stop` or permission prompts

Walk down the list — these are the usual suspects:

- **`terminal-notifier` not installed.** Required for the banner;
  without it the chime and `say` still fire but no notification
  appears. `claude-agents-bar doctor` flags this under the `notify/`
  check. Fix:

  ```bash
  brew install terminal-notifier
  ```

- **macOS Focus / Do Not Disturb is on.** On Sonoma+ this also
  silences the chime, not just the banner. Check the Control Center
  toggle or *System Settings → Focus*.

- **Notifications are denied for `terminal-notifier`.** *System
  Settings → Notifications → terminal-notifier* must be allowed,
  with *Alert style* set to Banners or Alerts (not None). If the
  entry isn't there, run `terminal-notifier -message hi` once from
  the terminal — the first invocation is what makes macOS register
  it in the Notifications pane.

- **Sound output is muted at the system level.** `Hero.aiff` /
  `Funk.aiff` go through normal audio output. Test directly:

  ```bash
  afplay /System/Library/Sounds/Hero.aiff
  say "test"
  ```

  If both are silent, the issue is the system audio, not the plugin.

- **Stop notifications skipped on short turns.** By design — the
  `notify_threshold_sec` knob (default 30 s) silences quick
  one-liners. Set it to `0` if you want every stop to ring.

- **One channel on, the other off?** Confirm the relevant config knob
  is `true`:

  ```bash
  jq '.notify_on_stop, .notify_on_wait' \
    "${XDG_CONFIG_HOME:-$HOME/.config}/claude-agents-bar/config.json"
  ```

- **Hook not registered.** Check that `notify-stop.sh` and
  `notify-wait.sh` are wired up:

  ```bash
  jq '.hooks.Stop, .hooks.PermissionRequest' ~/.claude/settings.json
  ```

  Both should list a command containing `notify-stop.sh` /
  `notify-wait.sh`. If they're missing, re-run `claude-agents-bar
  setup` (idempotent).

- **Hook fires but stays silent.** Trigger it by hand to see the
  underlying error:

  ```bash
  echo '{"session_id":"00000000-test","cwd":"/tmp","hook_event_name":"Stop"}' \
    | ~/.claude/hooks/notify-stop.sh
  ```

  Missing `jq` or a malformed `config.json` will surface here.

## Permission banner clicks open the wrong editor (or nothing)

The banner uses the same `editor_url_scheme` config knob as the
dropdown row clicks. If row clicks already work, banner clicks
should too — if they don't, see *Clicking a row does nothing* above.
A common gotcha on first install: the banner is delivered by
`terminal-notifier`, which on a brand-new install may need *Open
URLs* permission granted in *System Settings → Notifications →
terminal-notifier* (the click is a no-op until that's granted).

## Compact bullets render as literal `^[[33m`

ANSI rendering requires `ansi=true` on the same menu item. If you're
seeing escape codes printed verbatim, your SwiftBar is too old —
update to 1.5.x or newer.

## Plugin's About dialog is in the wrong language

SwiftBar reads plugin metadata from `<xbar.title.*>` headers in the
plugin file itself, not from the runtime `language` config. If you
want a different language in the *About* dialog specifically, that's
a SwiftBar limitation — the runtime menu still respects your
`language` setting.

## Usage line is missing / shows nothing

The usage line and alerts (spec 0011) come from the **usage monitor**, which is
**on by default** — it holds a window-less background `claude` (Haiku, in a
detached `screen`) and reads usage off its status line. **Start with `doctor`:**

```bash
claude-agents-bar doctor      # look at the [..] usage/ line
```

* `[ok] usage/ live (…)` — working; if the menu still looks off it's a render
  glitch, refresh with `open swiftbar://refreshallplugins`.
* `[err] usage/ … stuck on Claude Code's first-run prompt …` — **the common
  one.** On Claude Code v2.1.181+ the background session hangs on the *"Try the
  new fullscreen renderer?"* upsell and never reaches the ready TUI, so nothing
  is read. `setup` pre-seeds the `~/.claude.json` keys that suppress it — run
  `claude-agents-bar setup`, then `open swiftbar://refreshallplugins`.
* `[warn] usage/ … onboarding keys are seeded …` — peek inside the session with
  `screen -r cab-usage-mon` (detach with **`Ctrl-A` then `d`** — *don't* `Ctrl-C`
  or `/quit`, that kills it). Look for a *new* blocking prompt a fresh Claude
  Code version may have added, or confirm you're not on API-key auth.
* `[warn] usage/ background session down …` — the plugin respawns it on the next
  tick (~5 s); re-check.

Other causes:

* **API-key auth:** `rate_limits` exist only for a Claude.ai subscription
  (Pro/Max). On API-key auth there's nothing to show — this is not a bug.
* **Toggled off:** *Statistics → Usage monitor* (or `usage_monitor: "off"`)
  stops the session, the line, and the alerts together.
* **Stale → hidden on purpose:** if the background session dies, the line
  disappears rather than freezing on old numbers (a `record_ts` staleness gate).
* **Never run `setup` after a `brew upgrade`:** the status-line sensor, the
  `refreshInterval`, and the trusted folder all live in `~/.claude` and only
  `setup` writes them. A plain upgrade ships code, not wiring.

### Usage number jumps around (e.g. 10 % → 20 % → 10 %)

Fixed in 1.3.0. The `statusLine` sensor is global — every interactive session
fired it — so an old session reopened via resume wrote its **stale cached**
`rate_limits` over the daemon's live number. The sensor now writes only for the
monitor's own session (gated on its work-folder cwd). If you still see it, your
plugin predates the fix — `brew upgrade claude-agents-bar` and re-run `setup`.
