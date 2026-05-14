# ClaudeAgentsBar — notes for Claude Code

You're reading this because you (a Claude Code session) have been
asked to install, upgrade, debug, or modify ClaudeAgentsBar — a
SwiftBar plugin that puts every running Claude Code session's status
into the macOS menu bar.

If you're a **human contributor**, read [PLUGIN.md](./PLUGIN.md) for
architecture and dev workflow. If you're a **user**, read
[README.md](./README.md).

## What this project is

A SwiftBar plugin (`claude-agents.5s.py`, Python, stdlib only) that
polls every 5 s and renders one menu-bar item per Claude Code session.
A Claude Code hook (`hooks/agent-state.sh`, Bash) writes live state
into `~/.claude/agent-state.tsv` on every session event. The plugin
reads JSONL transcripts under `~/.claude/projects/*/*.jsonl` for
titles + `cwd` and joins them with the TSV at render time.

Stateless: no daemon, no IPC. Each tick rebuilds the menu from disk.

## Installing on the user's machine

There are two install paths. **Confirm with the user before either** —
both modify `~/.claude/settings.json` and create symlinks outside the
project. Don't proceed silently.

### Path A: Homebrew (preferred if the user has brew)

```bash
brew install --cask swiftbar                          # if missing
brew install alexey-krylov/claude-agents-bar/claude-agents-bar
claude-agents-bar setup
```

`brew install` puts the plugin into `$HOMEBREW_PREFIX/Cellar/...`
and exposes a single `claude-agents-bar` binary on PATH. `setup` is
the part that touches `~/.claude/*` and the SwiftBar plugins dir.

### Path B: Git clone (canonical inside this repo)

```bash
bash install.sh                  # or: bin/claude-agents-bar setup
```

Both forms run the same `bin/setup.sh` underneath. Idempotent — safe
to re-run.

### What `setup` does (either path)

1. Verify `jq`, `python3`, and SwiftBar.app are present.
2. Symlink `claude-agents.5s.py` into the SwiftBar plugins directory.
3. Symlink `hooks/agent-state.sh` into `~/.claude/hooks/`.
4. Back up `~/.claude/settings.json` (`.bak.YYYYMMDD-HHMMSS`) and merge
   the hook registrations from `settings-hooks.json` into it. Existing
   user hooks are preserved.
5. Smoke-test the hook with a fake event.
6. Signal SwiftBar to refresh (`swiftbar://refreshallplugins`).

### Before running setup

- Check the project is **not** inside the SwiftBar plugins folder
  (only matters for path B). Run
  `defaults read com.ameba.SwiftBar PluginDirectory` and compare with
  `pwd`. `setup` refuses to run if they overlap, but it's better to
  catch this earlier.
- Check SwiftBar is installed: `ls /Applications/SwiftBar.app`. If
  missing, suggest `brew install --cask swiftbar` before continuing —
  `setup` warns but proceeds without it. You can also run
  `claude-agents-bar doctor` to see all three checks at once.

### After installing

- Check `~/.claude/agent-state.tsv` exists and gets a row when the
  user starts (or continues) any Claude Code session.
- Look for the icon in the menu bar. If it's not visible on a notched
  MacBook, see [docs/troubleshooting.md](./docs/troubleshooting.md) —
  most likely the menu bar is clipping behind the notch, not a
  plugin bug.
- Sessions started **before** install will show as `idle` until they
  emit their next hook event. That's expected; don't try to backfill.

### Uninstalling

`claude-agents-bar teardown` (or the legacy `bash uninstall.sh`)
reverses everything except the four sidecar files under `~/.claude/`
(`agent-state.tsv`, `agent-state.clicks`, `agent-state.dismiss`,
`agent-state.forget`). If the user wants a truly clean slate, delete
those manually. After a Homebrew install, finish with
`brew uninstall claude-agents-bar` to remove the binary itself.

## Coding conventions (if modifying the project)

These exist for a reason — most are spelled out in [PLUGIN.md](./PLUGIN.md)
and the ADRs under [docs/adr/](./docs/adr/). Quick version:

- **Python 3.9 compat.** SwiftBar invokes `/usr/bin/python3`, which is
  3.9.6 on Sequoia. `from __future__ import annotations` is on, so
  `list[Path]` in type hints is fine, but runtime 3.10+ features
  (`match`/`case`, `slots=True`) are not.
- **Stdlib only.** No external deps. Whatever ships on macOS.
- **Pure helpers first, I/O second, rendering last.** The file is
  organised in that order — preserve it when adding code.
- **Fail soft everywhere.** A broken JSONL row, missing `.git/HEAD`,
  unreadable TSV — every reader catches `OSError` /
  `json.JSONDecodeError` and returns an empty value. The menu must
  always render *something*.
- **Test under `/usr/bin/python3`**, not your shell's `python3`. The
  latter may be a newer Homebrew install that silently accepts syntax
  3.9 would reject.

Verify before reporting "done":

```bash
/usr/bin/python3 -m py_compile claude-agents.5s.py
/usr/bin/python3 -m unittest discover -s tests
/usr/bin/python3 claude-agents.5s.py | head -3   # well-formed output
open "swiftbar://refreshallplugins"              # live refresh
```

## Where to look

When a user reports "clicking a row does nothing", first check what
editor they're using — VSCode works by default, but VSCodium and
Cursor need `editor_url_scheme` set to `"vscodium://"` or
`"cursor://"` respectively. See
[docs/troubleshooting.md](./docs/troubleshooting.md).

| If you need to… | Read |
|---|---|
| Understand architecture, gc, locking, render groups | [PLUGIN.md](./PLUGIN.md) |
| Add a config knob | [PLUGIN.md § Adding a config knob](./PLUGIN.md) + update [docs/configuration.md](./docs/configuration.md) and `config.example.json` |
| Add a submenu action or row | [PLUGIN.md § Adding a submenu action](./PLUGIN.md) |
| Add a new state bucket | [PLUGIN.md § Render groups](./PLUGIN.md) |
| Touch the hook or sidecars | [PLUGIN.md § Touching the hook](./PLUGIN.md) |
| Understand a structural choice before reverting it | [docs/adr/](./docs/adr/) |
| Help a user troubleshoot install / display issues | [docs/troubleshooting.md](./docs/troubleshooting.md) |
| Document a user-visible change | [CHANGELOG.md](./CHANGELOG.md) |

When you change something that's documented in multiple places (config
field, file layout, install flow), update **all** copies in the same
turn. The three places to check are:

- `README.md` — landing page (lean, points to docs)
- `docs/configuration.md` — full config reference
- `PLUGIN.md` — contributor / architecture reference
- `config.example.json` — bundled starter config
- `tests/` — unit tests for the Config dataclass + helpers

## Things not to do

- **Don't roll your own merge into `~/.claude/settings.json`.**
  `install.sh` does an additive jq-based merge with a timestamped
  backup. Re-use it.
- **Don't add external Python deps.** Stdlib only. If you find yourself
  reaching for `requests` or `pyyaml`, you're solving the wrong
  problem.
- **Don't use Python 3.10+ syntax.** `/usr/bin/python3` is 3.9 and
  that's what SwiftBar invokes — see ADR-0006 if you're tempted to
  change this.
- **Don't put the project inside the SwiftBar plugins folder.**
  SwiftBar would run `install.sh` and `uninstall.sh` as plugins. The
  installer refuses, but you shouldn't try.
- **Don't commit unless the user asks.** General rule, but worth
  repeating — this repo has a CHANGELOG, ADRs, and a versioning story
  that you don't necessarily have full context on.
- **Don't auto-clean the sidecar files in `~/.claude/`** during
  uninstall or upgrade. They may contain state the user wants to keep
  (e.g. forget/dismiss cutoffs); leave them in place unless asked.

## Repo layout (quick reference)

```
ClaudeAgentsBar/
├── claude-agents.5s.py      ← SwiftBar plugin (Python 3.9, stdlib only)
├── hooks/agent-state.sh     ← Claude Code hook → ~/.claude/agent-state.tsv
├── bin/                     ← scripts invoked from menu rows / Tools submenu
├── tests/                   ← unittest suite (stdlib only)
├── locales/                 ← i18n JSON per language
├── docs/
│   ├── configuration.md     ← full user-facing config reference
│   ├── troubleshooting.md   ← notch + display + hook issues
│   └── adr/                 ← architecture decision records (MADR)
├── config.example.json      ← bundled starter config
├── settings-hooks.json      ← fragment merged into ~/.claude/settings.json
├── install.sh / uninstall.sh
├── README.md                ← landing page for users
├── PLUGIN.md                ← architecture + contributor guide
├── CLAUDE.md                ← (this file) notes for Claude Code agents
└── CHANGELOG.md
```
