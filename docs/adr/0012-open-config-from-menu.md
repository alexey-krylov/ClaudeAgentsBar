# 0012. Open the config file from the Tools menu

* Status: Accepted
* Date: 2026-05-14

## Context

The config knob count has grown — `window_minutes`, `fresh_minutes`,
`ack_minutes`, `watchdog_seconds`, `title_max`, `menubar_icon`,
`menubar_icon_fallback`, `editor_url_scheme`, `language`, `compact`,
`context_window_tokens` — and most of them are now things a casual user
might want to touch (compact mode for a notched MacBook, language for a
non-English user, context window for Haiku 4.5, etc.). Today the only
way to land in that file is:

```bash
mkdir -p ~/.config/claude-agents-bar
cp config.example.json ~/.config/claude-agents-bar/config.json
$EDITOR ~/.config/claude-agents-bar/config.json
```

— three shell commands, requires knowing the bundled example exists,
requires remembering the XDG path. That's a lot of friction for what
should be a one-click affordance.

The menu bar already has a *Tools* submenu (introduced for *Acknowledge
all* / *Forget all sessions*) — the natural home for one more entry.

## Alternatives considered

| # | Approach | Verdict |
|---|----------|---------|
| 1 | **Status quo — document the path in README only.** | Cheap. But makes every "how do I change X?" question route through README, and most users won't read it before reaching for the menu. Discoverability suffers. Rejected as the only mechanism. |
| 2 | **Embed editor invocation in Python.** Call `subprocess.run(["open", "-t", path])` directly from `claude-agents.5s.py` via a `--open-config` subcommand. | Tempting (no new shell script), but pulls editor-launching, first-run seeding, and shell-error handling into a file that today renders the menu and otherwise does nothing imperative. Mixes concerns. Rejected. |
| 3 | **`bin/open-config.sh` that re-derives the path itself.** Bash duplicates the env-var → XDG → `~/.config` resolution that `_config_path()` already encodes. | Works, but duplicates the lookup chain. The next time the order or fallback changes the two implementations drift. Rejected on DRY grounds. |
| 4 | **`bin/open-config.sh` that receives the path from the plugin.** Python resolves the path via the existing `_config_path()`, passes it to the shell wrapper as `param1`. Wrapper does first-run seeding (`cp config.example.json …`) and `open -t`. | Accepted. The lookup chain lives in one place; the shell script stays a thin wrapper (a few lines of bootstrap + one `open` call). |
| 5 | **Pin a specific editor** (VS Code, `code`, `nvim` in Terminal, …). | Brittle — assumes a tool the user may not have, and overrides whatever they've already wired up as their default JSON handler. `open -t` defers to the system's registered text editor (TextEdit by default, but VS Code / Sublime / BBEdit inherit on install). User choice wins. Rejected. |
| 6 | **Fail loudly when `config.json` is missing.** Show an alert "no config file at $PATH" and bail. | The empty state is the *normal* state for the majority of users — most never need to override defaults. Greeting them with an error dialog is wrong. The seed-from-example approach (alt #4) makes the empty case land in a documented starter file, which doubles as in-app documentation. Rejected. |

## Decision

Adopt alternative #4 with #5's `open -t` choice.

* Python computes `config_path` (via `_config_path()`) and
  `example_config` (`<plugin-dir>/config.example.json`) and passes both
  to the shell wrapper as `param1` / `param2`.
* `bin/open-config.sh`:
  1. If `param1` doesn't exist, `mkdir -p` its parent, copy
     `param2` into it. If the example is also missing (corrupt
     install) write `{}` so the editor doesn't see a non-JSON file.
  2. `/usr/bin/open -t "$TARGET"` — defers to the system's registered
     default text editor.
* The menu line uses `sfimage=gearshape.fill` for the cogwheel; lives
  *after* *Suggest improvement…* inside the *Tools* submenu so the
  related "configure and provide feedback" pair sits together at the
  bottom of the submenu.
* `refresh=true` is **omitted** — the user is editing a file, not
  performing an action that mutates plugin-readable state. SwiftBar
  picks up the new config on its next 5 s tick automatically.

## Consequences

**Wins:**

* One-click access to every config knob. No shell commands, no path
  hunting.
* The path-resolution chain stays defined exactly once (in
  `_config_path()`), so future changes can't desync the menu wrapper
  from the loader.
* First-run seeding doubles as in-app documentation: the example file
  carries `//`-prefixed comments for every field.
* No editor hardcoding — `open -t` inherits the user's
  default-text-editor choice. VS Code / Sublime / BBEdit installs Just
  Work without us shipping a switch.

**Costs:**

* The first-run seed is a one-time write to the user's
  `$XDG_CONFIG_HOME`. Idempotent (the `[ ! -e ]` guard skips on
  subsequent clicks), and the destination directory is the documented
  location, so the surprise is minimal.
* A user who's set `CLAUDE_AGENTS_BAR_CONFIG` to a path they don't have
  write permission for will see `cp` fail silently and then `open -t`
  on a nonexistent file. Acceptable — that's a self-inflicted
  misconfiguration, and we'd rather not nag with dialogs in the menu
  for it.

## Related

* [ADR-0006](./0006-json-config-stdlib-only.md) — the JSON-config
  decision; this ADR is a UX layer on top of that file format.
* [ADR-0009](./0009-json-per-locale-i18n.md) — the new `menu.config`
  label flows through the same per-locale JSON tables as the rest of
  the menu strings.
