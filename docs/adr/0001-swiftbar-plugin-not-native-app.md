# 0001. SwiftBar plugin, not a native Swift app

* Status: Accepted
* Date: 2026-05-13

## Context

ClaudeAgentsBar must surface live Claude Code session state in the macOS
menu bar. The runtime choices on the table:

1. **Native Swift menu-bar app** (`NSStatusItem` + `NSMenu`, packaged as
   a `.app`).
2. **SwiftBar plugin** — a script SwiftBar invokes on a fixed cadence;
   stdout is parsed as a menu spec.
3. **BitBar / xbar plugin** — same paradigm as SwiftBar, older and less
   maintained.
4. **Terminal TUI** (`fzf`/`tmux`-based) — no menu-bar integration.

Constraints driving the call:

* The author iterates on the project alone; rebuilding and signing a
  Swift app per change is a friction tax that compounds over weeks of
  tuning UX.
* Almost all needed UI primitives are *NSMenu primitives*: rows with
  state icons, submenus, hover tooltips, badge counters, click handlers.
  Almost nothing the SwiftBar abstraction can't express.
* Performance budget is generous: rebuilding the menu every five seconds
  for a few dozen sessions is microseconds in any language; we're not
  CPU-bound.

## Decision

Ship as a SwiftBar plugin written in Python 3.9 (the system Python on
recent macOS, which is what SwiftBar invokes via shebang).

## Consequences

**Wins:**

* Editing a file = live update on the next 5 s tick. No build step.
* Standard library only — no toolchain to install, no `pip` lockfile to
  maintain, no signing or notarisation.
* Easy to test: just run the script and look at stdout.

**Costs / boxed-in by:**

* `NSMenu` has no input field, so an interactive search bar is
  fundamentally impossible.
* Rows have no inline buttons; per-row actions must live in submenus.
* The minimum refresh cadence is 5 s; instant reactivity would need a
  daemon (out of scope).
* Plugin code must stay Python 3.9-compatible — see [ADR-0006](./0006-json-config-stdlib-only.md)
  and the *Code style* section in `PLUGIN.md`.

A native Swift rewrite is a viable v2 if real inline buttons, hover
popovers or live search ever become must-haves. It's not in scope today.
