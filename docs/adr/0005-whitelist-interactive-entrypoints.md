# 0005. Whitelist interactive entrypoints

* Status: Accepted
* Date: 2026-05-13

## Context

Claude Code runs in several flavours. Each transcript event carries an
`entrypoint` string that identifies the runtime:

| `entrypoint`     | Launched by                                   |
|------------------|-----------------------------------------------|
| `claude-vscode`  | The VSCode extension (interactive UI)         |
| `cli`            | `claude` invoked interactively in a terminal  |
| `sdk-cli`        | `claude -p …`, Python SDK, scheduled jobs     |

Headless / scripted runs (`sdk-cli`) produce sessions the user cannot
usefully interact with — they're invoked by scripts, schedulers, or
the Python SDK rather than by a human typing at a UI. Without a
filter, they clutter the menu and confuse the "who needs me?" signal
we exist to surface.

Filtering approaches:

1. **Blacklist** specific values (`sdk-cli`). Simple, but a new
   headless flavour added by Anthropic ships visible by default.
2. **Whitelist** specific values (`claude-vscode`, `cli`). New headless
   flavours are hidden by default; new *interactive* flavours need a
   code update before appearing.
3. **Config knob** (`show_headless: bool`). User-controllable but adds
   surface area for a problem nobody asked to have.

## Decision

Hardcode a whitelist of interactive entrypoints. No config knob.

```python
INTERACTIVE_ENTRYPOINTS = frozenset({"claude-vscode", "cli"})
```

Sessions whose `entrypoint` is missing from the transcript (older or
malformed JSONL) are treated as interactive — better to show one too
many than swallow something the user might want.

## Consequences

**Wins:**

* The menu stays focused on sessions a human is actually typing into.
* New headless flavours are silently filtered the moment Anthropic
  ships them — no doc reading required.
* No user-facing config flag to support.

**Costs:**

* A new *interactive* entrypoint (e.g. a Cursor-flavoured extension
  emitting `claude-cursor`) ships **hidden** until somebody updates
  `INTERACTIVE_ENTRYPOINTS`.
* The whitelist is in source code; a non-developer user can't add a
  flavour locally without editing the plugin. Acceptable given the
  audience of this tool.

The "unset entrypoint = interactive" fallback specifically protects
sessions started before this filter existed — they remain visible
rather than silently disappearing.
