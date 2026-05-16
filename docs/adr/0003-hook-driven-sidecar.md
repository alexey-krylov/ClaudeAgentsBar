# 0003. Hook-driven sidecar TSV for live state

* Status: Accepted
* Date: 2026-05-13

## Context

The plugin needs to distinguish three live states: `waiting` (model is
blocked on the user — permission prompt or `AskUserQuestion`), `working`
(a tool call is in flight), and `idle` (turn ended). Options:

1. **Parse JSONL transcripts post-hoc.** Tail the file, infer state
   from the last event. Tedious — JSONL events don't cleanly map to
   our three states (a permission prompt isn't a transcript event),
   and we'd be redoing work Claude Code already does internally.
2. **Poll Claude Code process state** (`pgrep`, `lsof`). Doesn't carry
   semantic info; also expensive.
3. **Claude Code hooks.** Claude Code already invokes user-defined
   shell scripts on every meaningful event (`SessionStart`,
   `PreToolUse`, `Notification`, `Stop`, …). One script writing a TSV
   row per event gives the plugin a perfect, semantically-clean index.

## Decision

A single `hooks/agent-state.sh` script is registered against every
relevant event in `~/.claude/settings.json`. It writes one row per
session into `~/.claude/agent-state.tsv` — the sidecar. The plugin
reads that sidecar on every tick.

TSV schema (tab-separated, last write wins):

```
<session_id> <state> <last_event_ts> <last_event_kind> <cwd>
```

Mapping from event to state lives in `settings-hooks.json`:

| Event              | Written state                       |
|--------------------|-------------------------------------|
| `SessionStart`     | depends on `payload.source` (below) |
| `UserPromptSubmit` | `working`                           |
| `PreToolUse`       | `working`                           |
| `PostToolUse`      | `working`                           |
| `Notification`     | `waiting`                           |
| `Stop`             | `idle`                              |

`SessionStart` fires both on a genuine cold start and when the user
merely *opens* an existing session in the IDE (the VSCode extension
emits it on every tab switch, with `source=resume`). Treating it as
`working` made every tab switch flash the menu yellow — see
[this branch's fix](#). The hook now branches on `payload.source`:

| `source`            | Action                                                       |
|---------------------|--------------------------------------------------------------|
| `startup` / `clear` | Write `idle` (fresh session, awaits first prompt)            |
| `resume` / `compact`| Leave existing row untouched; if none exists, write nothing  |

For `resume` / `compact` without an existing row we deliberately
**do not** synthesise an `idle` row: the plugin already falls back
to the JSONL transcript's mtime when a session is missing from the
TSV (see `build_session`), and writing a row with the *current*
timestamp would make the classifier paint the session `FRESH`
("Stop fired just now") on every IDE tab switch — see the FRESH
guard in `_classify`, which additionally requires
`last_event_kind == "Stop"` before granting the FRESH grace window.

This is implemented inside `hooks/agent-state.sh` rather than via
two separate hook registrations because Claude Code dispatches all
`SessionStart` matchers identically — there is no per-source filter
in `settings.json`.

## Consequences

**Wins:**

* Semantically clean: each state corresponds to a hook the Claude Code
  runtime emits on a real lifecycle event.
* Plugin has zero coupling to JSONL internals beyond title/cwd reads.
* Hook is dumb — three jq invocations and an `awk` rewrite. Trivial to
  port to another shell or another renderer.

**Costs:**

* Sessions started **before** the hook was installed don't appear in
  the sidecar and look `idle` by default until they emit their next
  hook event.
* Hook adds ~5 ms to every Claude Code event. Negligible.
* The sidecar grows monotonically without explicit cleanup; addressed
  by [ADR-0004](./0004-mkdir-lock-vs-flock.md) (lock) and a plugin-side
  garbage collector that drops rows whose transcript is gone or whose
  last event has fallen out of the dropdown window.

## Related

* [ADR-0004](./0004-mkdir-lock-vs-flock.md) — how concurrent writes are
  serialised.
* [ADR-0007](./0007-project-outside-plugins-folder.md) — why the hook
  installer can't live inside the SwiftBar plugins folder.
