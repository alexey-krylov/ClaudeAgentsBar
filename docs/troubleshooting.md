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
either (a) menu-bar clipping (see notch section above) or (b) SwiftBar
hasn't picked up the plugin yet — force a refresh:

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

## Sessions show as `idle` even when working

The hooks aren't registered or aren't firing.

```bash
jq '.hooks' ~/.claude/settings.json
```

Should list entries pointing at `~/.claude/hooks/agent-state.sh` for
`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`Notification`, `Stop`. If they're missing, re-run `bash install.sh`
in the project root (idempotent — safe to run multiple times).

Test the hook directly:

```bash
echo '{"session_id":"00000000-test","cwd":"/tmp","hook_event_name":"SessionStart"}' \
  | ~/.claude/hooks/agent-state.sh working
grep '^00000000-test' ~/.claude/agent-state.tsv  # should print one row
```

Clean up the test row afterwards:

```bash
grep -v '^00000000-test	' ~/.claude/agent-state.tsv \
  > ~/.claude/agent-state.tsv.tmp \
  && mv ~/.claude/agent-state.tsv.tmp ~/.claude/agent-state.tsv
```

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

## "Configuration…" opens TextEdit, not my editor

`open -t` follows the system *Default text editor* binding. To change
it, right-click any `.json` file in Finder → *Get Info* → *Open with*
→ pick your editor → *Change All…*.

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
