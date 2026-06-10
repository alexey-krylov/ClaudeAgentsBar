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
reverses everything except the sidecar files under `~/.claude/`
(`agent-state.tsv`, `agent-state.subagents.tsv`,
`agent-state.clicks`, `agent-state.dismiss`, `agent-state.forget`). If the user wants a truly clean slate, delete
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

## README structure (don't reshuffle without thinking)

The top-level [README.md](./README.md) is laid out in a deliberate
order. If you're editing it, **preserve the section order** unless the
user explicitly asks for a restructure. The flow is:

1. **Tagline + ASCII demo** — value-prop in one line, then a picture.
   No "Built as a SwiftBar plugin" framing in the tagline; that's
   implementation detail and belongs in *How it works*.
2. **Why I built this** — honest first-person motivation. *Before*
   install, because readers decide whether to keep reading here. This
   replaced an earlier "The problem" section that duplicated it in a
   marketing voice — don't reintroduce a second motivation section.
3. **What it shows** — features, *before* Install. A reader needs to
   know what they're getting before they decide to run `brew install`.
   Keep it compact: counter table, one paragraph on the dropdown, one
   on the row submenu, one on the Tools submenu. Deeper detail belongs
   in [docs/configuration.md](./docs/configuration.md), not here.
4. **Install** — Homebrew only. The git-clone path lives in
   [PLUGIN.md § Dev setup](./PLUGIN.md), because it's a contributor
   concern, not a user one. Don't re-add it to the README.
5. **Configuration** — three-field table + link to the full reference.
   Don't expand the table; if a new knob is interesting enough to
   surface, replace one of the three rather than growing the list.
6. **How it works** — one or two paragraphs of architecture, then
   pointer to PLUGIN.md and ADRs.
7. **Troubleshooting** — top 1–2 issues only (notch, standalone run),
   each one line plus a link to `docs/troubleshooting.md`. Resist the
   urge to inline more cases here; the docs/ file is canonical.
8. **Contributing** — pointer to PLUGIN.md and CHANGELOG.md.

### Voice

Peer-to-peer technical register. No marketing copy, no overselling,
no "✨ powerful ✨" adjectives. If you find yourself writing "seamlessly"
or "delightful experience", back up.

## Before publishing a version

**Always ask the user to manually check every new user-facing change
before you cut a release.** A release is the version bump → tag → GitHub
release → Homebrew formula chain; once any of it is pushed, undoing it is
painful (see below).

Automated checks (`py_compile`, `unittest`, grepping the rendered menu
output) do **not** exercise the SwiftBar side: whether a menu action
actually fires on click, whether a `checked=`/`sfimage=` row looks right,
whether a new `bin/app/*.sh` is executable enough for SwiftBar to run it,
whether a notification banner click lands where it should. The agent
can't see the GUI. So before publishing:

1. List exactly what's new and user-visible (new menu items, toggles,
   row/banner click behaviour, icons, audio).
2. Ask the user to click through each one in the real menu bar and
   confirm it behaves, **then** proceed with the version bump and
   release. Don't infer "it works" from tests alone.

This is not optional politeness — shipping 1.1.1 with a dead
*Multi-workspace mode* checkbox (the action script lost its executable
bit, so SwiftBar silently couldn't run it) is exactly the failure this
gate prevents. Re-releasing the *same* version number to fix it means
moving a published tag, recomputing the Homebrew `sha256`, and forcing a
`brew reinstall` (a plain `brew upgrade` won't re-fetch when the version
is unchanged) — all avoidable by one round of manual verification first.

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
├── hooks/
│   ├── agent-state.sh       ← Claude Code hook → ~/.claude/agent-state.tsv
│   └── settings-hooks.json  ← hook registrations fragment
├── bin/
│   ├── claude-agents-bar    ← CLI dispatcher (setup/teardown/doctor/version)
│   ├── install/
│   │   ├── setup.sh         ← wires plugin, hook, and settings
│   │   └── teardown.sh      ← reverses setup
│   └── app/                 ← actions invoked from menu rows / Tools
│       ├── open-session.sh, delete-session.sh, forget-*.sh, ack-*.sh
│       ├── open-config.sh, reveal-session.sh, stats-today.sh
│       └── (all executable, called by SwiftBar with session id, paths, etc.)
├── tests/                   ← unittest suite (stdlib only)
├── locales/                 ← i18n JSON per language
├── docs/
│   ├── configuration.md     ← full user-facing config reference
│   ├── troubleshooting.md   ← notch + display + hook issues
│   └── adr/                 ← architecture decision records (MADR)
├── config.example.json      ← bundled starter config
├── README.md                ← landing page for users
├── PLUGIN.md                ← architecture + contributor guide
├── CLAUDE.md                ← (this file) notes for Claude Code agents
└── CHANGELOG.md
```
