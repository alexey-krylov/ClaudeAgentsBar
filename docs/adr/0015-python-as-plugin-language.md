# 0015. Python as the plugin language

* Status: Accepted
* Date: 2026-05-17

## Context

SwiftBar can run plugins written in any language that produces
SwiftBar-formatted output on stdout. The main constraint is that the
runtime must be available on every target Mac without requiring the
user to install anything. Options considered:

1. **Python 3** — `/usr/bin/python3` ships with macOS as part of the
   Xcode Command Line Tools stub. On any Mac where Xcode CLT is present
   (a prerequisite for nearly every developer tool, including Homebrew),
   Python 3 is already on disk at a fixed, well-known path. Stdlib is
   rich enough for file I/O, JSON, subprocess, and regex — everything
   the plugin needs.
2. **Shell (bash/zsh)** — also ships with macOS. But multi-level
   associative arrays, JSON parsing, and anything resembling a data
   model become unwieldy fast. The plugin already has ~800 lines of
   non-trivial logic; shell would make it unmaintainable.
3. **Node.js / Deno / Bun** — not bundled with macOS. Would require
   the user to install a runtime, adding a hard dependency outside our
   control and a failure mode that's opaque to users who don't develop
   in JS.
4. **Swift** — native binary, fast. But requires Xcode to compile and
   produces a platform-specific binary we'd have to distribute per
   macOS version. Homebrew formulae can do this, but it gates
   development on a full Xcode install and complicates the git-clone
   path. Counter-argument to ADR-0001 (which chose SwiftBar over a
   native app for similar reasons).
5. **Go / Rust / etc.** — same compiled-binary problem as Swift, with
   the added friction of a non-Apple toolchain.
6. **Ruby** — was bundled with macOS until Monterey (12.3), then
   removed. Explicitly deprecated as a system runtime by Apple. Not a
   safe assumption anymore.

## Decision

Use Python 3, invoked as `/usr/bin/python3`.

The shebang line is `#!/usr/bin/python3` — the system path, not
`/usr/bin/env python3`. This ensures SwiftBar always calls the same
interpreter regardless of what the user has on their `$PATH` (Homebrew
Python, pyenv, Conda, etc.), and avoids version surprises when the
user's shell Python is 3.12+ while the system stub is 3.9.

The plugin stays within the Python 3.9 feature set (the version
shipped with Xcode CLT on macOS Sequoia at the time of this writing).
See ADR-0006 for how this constraint shapes config format choices.

## Consequences

**Wins:**

* Zero runtime dependencies for the user — no `brew install python`,
  no `pip install`, no version managers. The plugin works immediately
  after `brew install claude-agents-bar` or a plain git clone.
* A known, stable interpreter path means no support surface around
  "which Python am I on?".
* Python's stdlib — `pathlib`, `json`, `subprocess`, `dataclasses`,
  `re` — is expressive enough that the plugin reads like clean,
  structured code rather than a shell script patchwork.

**Costs:**

* Pinned to Python 3.9 semantics. Features from 3.10+ (`match`/`case`,
  `tomllib`, `slots=True` on dataclasses) are off the table until Apple
  updates the system stub or we change the shebang strategy.
* If Apple ever ships macOS without the Xcode CLT stub (unlikely but
  not impossible), the plugin would stop working silently. The `doctor`
  command checks for this and warns early.
