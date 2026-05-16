# Manual install (without Homebrew)

The recommended path is `brew install` — it handles the binary, updates, and
keeps things tidy. If you don't use Homebrew, the git-clone path works
just as well.

## Prerequisites

**SwiftBar** — download the latest `.dmg` from
[github.com/swiftbar/SwiftBar/releases](https://github.com/swiftbar/SwiftBar/releases),
open it, drag SwiftBar.app to `/Applications`, and launch it once so it
prompts you to pick a plugins folder.

**python3** — ships with macOS 13+; if missing, install Xcode CLI tools:

```bash
xcode-select --install
```

**jq** — not bundled with macOS. Download a prebuilt binary from
[github.com/jqlang/jq/releases](https://github.com/jqlang/jq/releases)
(pick `jq-macos-arm64` for Apple Silicon, `jq-macos-amd64` for Intel),
then drop it somewhere on your PATH:

```bash
# example — adjust filename for your arch
sudo mv ~/Downloads/jq-macos-arm64 /usr/local/bin/jq
sudo chmod +x /usr/local/bin/jq
```

Verify:

```bash
jq --version        # e.g. jq-1.7.1
python3 --version   # e.g. Python 3.9.x
```

## Install

Clone the repo to a permanent location **outside** the SwiftBar plugins
folder (SwiftBar would try to run the scripts directly otherwise):

```bash
git clone https://github.com/alexey-krylov/ClaudeAgentsBar.git ~/Projects/ClaudeAgentsBar
cd ~/Projects/ClaudeAgentsBar
bash install.sh
```

`install.sh` calls `bin/install/setup.sh`, which:

1. Symlinks `claude-agents.5s.py` into SwiftBar's plugins directory.
2. Symlinks `hooks/agent-state.sh` and `hooks/notify-stop.sh` into
   `~/.claude/hooks/`.
3. Backs up `~/.claude/settings.json` (`.bak.YYYYMMDD-HHMMSS`) and
   merges the hook registrations from `hooks/settings-hooks.json` into it.
4. Smoke-tests the hook.
5. Signals SwiftBar to refresh.

Idempotent — safe to re-run (e.g. after a `git pull`).

### Non-standard SwiftBar plugins folder

If you moved SwiftBar's plugins folder to a custom location, pass it:

```bash
SWIFTBAR_PLUGINS_DIR=~/Dropbox/SwiftBar bash install.sh
```

## Verify

```bash
bin/claude-agents-bar doctor
```

Checks: `jq`, `python3`, `SwiftBar.app`, hook registration, sidecar TSV
freshness, and editor app.

## Stay up to date

```bash
cd ~/Projects/ClaudeAgentsBar
git pull
bash install.sh   # re-runs setup to pick up any new hook registrations
```

## Uninstall

```bash
bin/claude-agents-bar teardown
```

Removes symlinks and hook entries (takes a fresh `settings.json` backup
first). The four sidecar files under `~/.claude/` are left in place.
