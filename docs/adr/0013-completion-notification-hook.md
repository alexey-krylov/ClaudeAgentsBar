# 0013. Completion-notification hook bundled in the repo

* Status: Accepted
* Date: 2026-05-17

## Context

When a Claude Code session finishes its turn (`Stop` event), the user
gets no ambient signal — there is nothing to look at besides the menu
bar itself. On a long task (> 30 s) it is useful to hear an audio cue
and see a banner notification so attention can be returned to the
session without polling.

A `notify-stop.sh` script already existed as a hand-crafted file at
`~/.claude/hooks/notify-stop.sh`, registered manually in the user's
`settings.json`. It played a sound, spoke a phrase via `say`, and
called `terminal-notifier`. It had several rough edges:

* Hard-coded phrases and threshold — no way to customise without
  editing the script.
* `editor_url_scheme` was read from a separate env var, duplicating
  the value already present in `config.json`.
* The file wasn't in the repo — it couldn't be installed, upgraded, or
  version-controlled alongside the rest of ClaudeAgentsBar.
* The icon (`~/.claude/hooks/assets/claude-icon.png`) had to be placed
  there manually; new installs had no icon.

## Decision

Bundle `hooks/notify-stop.sh` in the ClaudeAgentsBar repository, with
three supporting changes:

**1. Config-driven customisation.** The script reads three keys from
the ClaudeAgentsBar `config.json` (the same file the plugin uses) via
`jq`:

| Key | Default | Meaning |
|-----|---------|---------|
| `notify_on_stop` | `true` | Master switch; `false` silences everything. |
| `notify_threshold_sec` | `30` | Skip if last user turn was less than N seconds ago. |
| `notify_phrases` | built-in list | Array of phrases; one picked at random per `Stop`. |

`editor_url_scheme` is also read from `config.json`, eliminating the
env-var duplication.

Config path resolution mirrors the XDG logic in `claude-agents.5s.py`
exactly: `$CLAUDE_AGENTS_BAR_CONFIG` → `$XDG_CONFIG_HOME/…` →
`~/.config/claude-agents-bar/config.json`. A missing or unreadable
config falls back silently to defaults — the hook never blocks a
session.

**2. Icon extracted at setup time.** `setup.sh` gains a step that
extracts a 256×256 PNG from
`/Applications/Claude.app/Contents/Resources/AppIcon.icns` via `sips`
(a built-in macOS utility, no extra deps) and writes it to
`~/.claude/hooks/assets/claude-icon.png`. If Claude.app isn't
installed the step is skipped and `terminal-notifier` runs without a
`-contentImage` argument — the notification appears but uses the
system default icon. The icon is generated, not committed, so the repo
carries no binary assets.

**3. Lifecycle wired into setup / teardown.** `setup.sh` symlinks
`notify-stop.sh` into `~/.claude/hooks/` (alongside `agent-state.sh`)
and registers it in the `Stop` event block of `settings-hooks.json`.
`teardown.sh` removes the symlink and strips the registration. The
jq filters in both scripts are extended to match both
`agent-state.sh` and `notify-stop.sh` so a re-run replaces stale
registrations rather than duplicating them.

**4. Doctor check.** `claude-agents-bar doctor` gains a `notify/`
check that warns when `terminal-notifier` is absent. The check is a
`warn`, not an `err`, because the feature is opt-out — a user who sets
`notify_on_stop: false` doesn't need the binary.

## Alternatives considered

| # | Approach | Verdict |
|---|----------|---------|
| 1 | **Leave notify-stop.sh as a user-managed file.** No repo changes. | The script has no upgrade path, no config integration, and no install story for new users. Rejected. |
| 2 | **Plugin detects state transitions and fires notifications itself.** Python polls `agent-state.tsv` on each tick, compares to a previous-state sidecar, fires `terminal-notifier` when a session transitions `working→idle`. | More robust than a hook (no event can be dropped), but makes the plugin non-stateless — it would need to persist previous-state somewhere. The tick-based detection also has up to 5 s latency. The hook fires synchronously on `Stop`, which is already the authoritative signal. Rejected. |
| 3 | **Use macOS `osascript` / `NSUserNotification` instead of `terminal-notifier`.** No extra dependency. | `osascript` notifications have no `-open` equivalent that deep-links into the editor, and the API is noisier to write (multi-line AppleScript for a one-liner). `terminal-notifier` already ships on most developer Macs and is used elsewhere in the project. Accepted only as a future fallback path if `terminal-notifier` proves problematic. |
| 4 | **Commit the icon PNG directly.** Store `hooks/assets/claude-icon.png` in the repo. | Avoids a `sips` call at setup time. But the icon is derived from Claude.app, which is not ours to redistribute. Extracting it locally at install time is legally cleaner and ensures the icon matches the version of Claude.app the user actually has. Rejected. |

## Consequences

**Wins:**

* New installs get completion notifications out of the box — no manual
  file placement or `settings.json` surgery.
* Phrases and threshold are user-configurable without editing the
  script; the same `config.json` the user already edits for other
  knobs.
* `editor_url_scheme` is defined once; clicking the notification banner
  opens the correct editor without a separate env-var.
* `teardown` is complete: `claude-agents-bar teardown` removes the
  hook and its `settings.json` registration.

**Costs:**

* Requires `terminal-notifier` (`brew install terminal-notifier`).
  `doctor` warns when it's missing; the hook itself silently skips the
  banner if the binary isn't on `PATH`, so the install never hard-fails
  over a missing notifier.
* `setup` now calls `sips` to extract the icon. On a machine without
  Claude.app the step no-ops; on a slow filesystem the `sips` call
  adds a fraction of a second to setup time. Acceptable.

## Related

* [ADR-0003](./0003-hook-driven-sidecar.md) — the hook infrastructure
  this notification hook is built on top of.
* [ADR-0006](./0006-json-config-stdlib-only.md) — the config format
  the `notify_on_stop` / `notify_phrases` keys are added to.
