# 0007. Project lives outside the SwiftBar plugins folder

* Status: Accepted
* Date: 2026-05-13

## Context

SwiftBar discovers plugins by scanning its **plugins folder** every few
seconds. The catch:

* The scan is **recursive** — subdirectories are walked too.
* The pref `MakePluginExecutable = 1` is on by default; SwiftBar
  `chmod +x`'s anything it finds.
* Any executable file (regardless of extension) is treated as a plugin
  candidate.

Result: if the project lives inside the plugins folder, SwiftBar will
quietly discover and **run** `install.sh`, `uninstall.sh`, and the
shell scripts in `hooks/` and `bin/` as plugins. We hit this for real:
SwiftBar ran `uninstall.sh` on a 5 s loop, undoing the install each
tick.

The natural fix is to put the project somewhere else and symlink only
the plugin script into the plugins folder. But we also want
`install.sh` to be friendly enough that a first-time user can run it
without reading every warning, so we want an explicit guard against
the misconfiguration.

## Decision

The project lives outside the SwiftBar plugins folder (the README
recommends `~/Projects/ClaudeAgentsBar`). `install.sh` resolves the
plugins folder via `defaults read com.ameba.SwiftBar PluginDirectory`
and **refuses to run** if the repo's path is the plugins folder or a
descendant of it, pointing the user at a safe location.

Only one file ends up inside the plugins folder: a symlink named
`claude-agents.5s.py` pointing at the real script in the project.

## Consequences

**Wins:**

* SwiftBar only sees the plugin entry point, not the support scripts.
* `install.sh` and `uninstall.sh` can't accidentally be invoked as
  plugins, removing a class of bug that's invisible until SwiftBar
  starts spinning them.
* The project root is a normal git-friendly tree — sane to clone,
  trivial to inspect, no special-casing in editors.

**Costs:**

* One extra symlink step in the installer.
* Users have to put the repo somewhere; `install.sh` won't quietly
  adapt to the misconfiguration — it errors out with a fix-it message.
  This is the intended trade.
